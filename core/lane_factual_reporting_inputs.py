"""Off-by-default factual valuation/performance input builder for one lane."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from authority.lane_exact_plan import canonical_json, validate_lane_exact_execution_plan
from core.lane_performance import build_lane_performance
from core.lane_reconciliation import validate_ending_lane_state, validate_lane_reconciliation
from core.lane_truth_status import build_truth_lineage_status
from core.lane_valuation import (
    RECONCILED_LANE_STATE_SCHEMA,
    accounting_journal_hash,
    build_lane_valuation,
    seal_lane_state,
)


LANE_FACTUAL_REPORTING_INPUTS_SCHEMA = "caerus.lane_factual_reporting_inputs.v1"


class LaneFactualReportingInputsError(ValueError):
    """Raised when scheduled factual reporting inputs cannot be proved."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def build_lane_factual_reporting_inputs(
    *,
    exact_plan: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    ending_state: Mapping[str, Any],
    journal_entries: Sequence[Mapping[str, Any]],
    prior_valuations: Sequence[Mapping[str, Any]] = (),
    valuation_date: str,
) -> dict[str, Any]:
    """Build factual valuation, performance, and truth-lineage inputs in memory."""

    failures = validate_lane_exact_execution_plan(exact_plan)
    if failures:
        raise LaneFactualReportingInputsError("exact plan is invalid: " + ",".join(failures))
    if exact_plan["lane_kind"] not in {"PAPER", "LIVE"}:
        raise LaneFactualReportingInputsError("factual reporting inputs require PAPER or LIVE")
    try:
        recon = validate_lane_reconciliation(reconciliation, exact_plan=exact_plan)
        state = validate_ending_lane_state(ending_state)
    except Exception as exc:
        raise LaneFactualReportingInputsError(f"reconciliation evidence is invalid: {exc}") from exc
    if recon["status"] != "PASS":
        raise LaneFactualReportingInputsError("factual reporting requires PASS reconciliation")
    if bool(recon["reconciled_fills"]) is not bool(recon["accounting_ready"]):
        raise LaneFactualReportingInputsError(
            "PASS reconciliation accounting readiness differs from fill economics"
        )
    if recon["source_hashes"]["ending_state"] != state["content_hash"]:
        raise LaneFactualReportingInputsError("reconciliation does not bind supplied ending state")
    scope_fields = ("trade_date", "account_id_hash", "lane_id", "lane_kind", "deployment_version", "plan_id")
    if any(state[field] != exact_plan[field] for field in scope_fields) or state["plan_hash"] != exact_plan["content_hash"]:
        raise LaneFactualReportingInputsError("ending state differs from exact plan scope")
    journal_hash = accounting_journal_hash(journal_entries)
    surface = f"FACTUAL_{exact_plan['lane_kind']}"
    lane_state = seal_lane_state(
        {
            "schema_version": RECONCILED_LANE_STATE_SCHEMA,
            "status": "PASS",
            "as_of": state["as_of"],
            "valuation_date": valuation_date,
            "account_id_hash": exact_plan["account_id_hash"],
            "lane_id": exact_plan["lane_id"],
            "lane_kind": exact_plan["lane_kind"],
            "deployment_version": exact_plan["deployment_version"],
            "performance_surface": surface,
            "economic_authority": "BROKER_RECONCILED",
            "cash": state["cash"],
            "equity": state["equity"],
            "positions": [
                {
                    "symbol": row["symbol"],
                    "quantity": row["quantity"],
                    "price": row["mark"],
                    "market_value": row["market_value"],
                    "source_hash": row["source_hash"],
                }
                for row in state["positions"]
            ],
            "journal_hash": journal_hash,
            "source_hash": recon["content_hash"],
        }
    )
    valuation = build_lane_valuation(
        journal_entries=journal_entries, lane_state=lane_state
    )
    valuations = [*prior_valuations, valuation]
    performance = build_lane_performance(valuations)
    journal_status = build_truth_lineage_status(
        evidence_type="JOURNAL",
        status="PASS",
        as_of=state["as_of"],
        lane_id=exact_plan["lane_id"],
        lane_kind=exact_plan["lane_kind"],
        deployment_version=exact_plan["deployment_version"],
        performance_surface=surface,
        source_hashes=[journal_hash, *[row["record_hash"] for row in journal_entries]],
    )
    reconciliation_status = build_truth_lineage_status(
        evidence_type="RECONCILIATION",
        status="PASS",
        as_of=state["as_of"],
        lane_id=exact_plan["lane_id"],
        lane_kind=exact_plan["lane_kind"],
        deployment_version=exact_plan["deployment_version"],
        performance_surface=surface,
        source_hashes=[recon["content_hash"], state["content_hash"]],
    )
    body = {
        "schema_version": LANE_FACTUAL_REPORTING_INPUTS_SCHEMA,
        "input_id": f"factual-reporting:{exact_plan['lane_id']}:{valuation['content_hash'][:24]}",
        "as_of": state["as_of"],
        "valuation_date": valuation_date,
        "lane_id": exact_plan["lane_id"],
        "lane_kind": exact_plan["lane_kind"],
        "deployment_version": exact_plan["deployment_version"],
        "account_id_hash": exact_plan["account_id_hash"],
        "plan_hash": exact_plan["content_hash"],
        "reconciliation_hash": recon["content_hash"],
        "journal_hash": journal_hash,
        "journal_status": journal_status,
        "reconciliation_status": reconciliation_status,
        "valuation": valuation,
        "performance": performance,
        "write_enabled": False,
        "write_performed": False,
        "broker_call_performed": False,
        "execution_authority": False,
        "approval_authority": False,
    }
    body["content_hash"] = _hash(body)
    return body


def run_lane_factual_reporting_inputs_dry_run(
    *, output_root: Path | str, write_enabled: bool = False, **builder_inputs: Any
) -> dict[str, Any]:
    """Validate always and immutably persist only with literal opt-in."""

    if type(write_enabled) is not bool:
        raise LaneFactualReportingInputsError("write_enabled must be a literal boolean")
    built = build_lane_factual_reporting_inputs(**builder_inputs)
    if not write_enabled:
        return built
    built["write_enabled"] = True
    built["write_performed"] = True
    built["content_hash"] = _hash(built)
    path = (
        Path(output_root)
        / "factual_reporting_inputs"
        / built["valuation_date"]
        / built["lane_id"]
        / f"{built['content_hash']}.json"
    )
    serialized = (canonical_json(built) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False) as handle:
            temporary = handle.name
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != serialized:
                raise LaneFactualReportingInputsError("immutable reporting input path conflict")
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return json.loads(canonical_json(built))


__all__ = [
    "LANE_FACTUAL_REPORTING_INPUTS_SCHEMA",
    "LaneFactualReportingInputsError",
    "build_lane_factual_reporting_inputs",
    "run_lane_factual_reporting_inputs_dry_run",
]
