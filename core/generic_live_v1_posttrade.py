"""Production causal closure for generic Live v1 posttrade evidence."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Callable, Mapping

from authority.lane_exact_plan import canonical_json
from core.accounting_journal import validate_accounting_journal
from core.generic_live_v1_submission import (
    _write_exclusive,
    finalize_generic_live_v1_posttrade,
)
from core.lane_performance import validate_lane_performance
from core.lane_reconciliation import validate_lane_reconciliation
from core.lane_truth_status import (
    validate_all_lane_audit,
    validate_daily_lane_audit,
    validate_dashboard_performance_surfaces,
)
from core.lane_valuation import accounting_journal_hash, validate_lane_valuation


GENERIC_LIVE_V1_PRODUCTION_CLOSURE_SCHEMA = (
    "caerus.generic_live_v1_production_posttrade_closure.v1"
)


class GenericLiveV1PosttradeError(RuntimeError):
    """Raised when the exact production causal chain is incomplete."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def finalize_generic_live_v1_production_posttrade(
    *, submission_result: Mapping[str, Any], exact_plan: Mapping[str, Any],
    order_lifecycle: Mapping[str, Any], reconciliation: Mapping[str, Any],
    journal_entries: list[Mapping[str, Any]],
    valuations: list[Mapping[str, Any]], performance: Mapping[str, Any],
    daily_lane_audit: Mapping[str, Any], all_lane_audit: Mapping[str, Any],
    dashboard_projection: Mapping[str, Any], finalized_at: str,
    rearm_state_path: Path | str, base_result_path: Path | str,
    closure_result_path: Path | str,
    rollback_handler: Callable[[str], Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate and durably bind reconciliation through published truth surfaces."""

    rolled_back = False

    def rollback_break(trigger: str) -> Mapping[str, Any]:
        nonlocal rolled_back
        evidence = rollback_handler(trigger)
        if (
            not isinstance(evidence, Mapping)
            or evidence.get("status") != "ROLLED_BACK_ARMED"
            or evidence.get("trigger") != trigger
            or evidence.get("paper_bytes_unchanged") is not True
            or evidence.get("cron_exact_line_removed") is not True
            or not evidence.get("rearm_hash")
            or evidence.get("config_action") not in {
                "RESTORED_BACKUP", "REMOVED_NO_PRIOR_CONFIG", "ALREADY_ABSENT"
            }
        ):
            raise GenericLiveV1PosttradeError(
                f"{trigger} rollback evidence is incomplete"
            )
        rolled_back = True
        return evidence

    try:
        reconciled = validate_lane_reconciliation(reconciliation, exact_plan=exact_plan)
    except Exception:
        try:
            rollback_break("RECONCILIATION_BREAK")
        finally:
            raise
    try:
        journal = validate_accounting_journal(journal_entries)
        if not journal:
            raise GenericLiveV1PosttradeError("production Live valuation requires journal history")
        session_rows = [
            row for row in journal if row["source_hash"] == reconciled["content_hash"]
        ]
        reconciled_fill_ids = {row["fill_id"] for row in reconciled["reconciled_fills"]}
        if {row["fill_id"] for row in session_rows} != reconciled_fill_ids:
            raise GenericLiveV1PosttradeError("journal does not exactly cover reconciled session fills")
        checked_valuations = [validate_lane_valuation(row) for row in valuations]
        if not checked_valuations:
            raise GenericLiveV1PosttradeError("production posttrade requires factual valuation evidence")
        if checked_valuations != sorted(checked_valuations, key=lambda row: (row["as_of"], row["valuation_id"])):
            raise GenericLiveV1PosttradeError("valuations must be in deterministic as-of order")
        for valuation in checked_valuations:
            if (
                valuation["lane_id"] != "generic-live-v1"
                or valuation["lane_kind"] != "LIVE"
                or valuation["account_id_hash"] != exact_plan["account_id_hash"]
                or valuation["deployment_version"] != exact_plan["deployment_version"]
                or valuation["performance_surface"] != "FACTUAL_LIVE"
            ):
                raise GenericLiveV1PosttradeError("valuation scope differs from exact Live plan")
        observed_journal_hash = accounting_journal_hash(journal)
        latest_valuation = checked_valuations[-1]
        if (
            latest_valuation["journal_hash"] != observed_journal_hash
            or latest_valuation["journal_entry_count"] != len(journal)
            or reconciled["content_hash"] not in latest_valuation["source_hashes"]
        ):
            raise GenericLiveV1PosttradeError("latest valuation does not bind journal/reconciliation")
    except Exception:
        try:
            rollback_break("ACCOUNTING_BREAK")
        finally:
            raise
    try:
        perf = validate_lane_performance(performance)
        valuation_hashes = [row["content_hash"] for row in checked_valuations]
        if (
            perf["source_valuation_hashes"] != valuation_hashes
            or perf["latest_as_of"] != latest_valuation["as_of"]
            or perf["lane_id"] != "generic-live-v1"
            or perf["lane_kind"] != "LIVE"
            or perf["account_id_hash"] != exact_plan["account_id_hash"]
        ):
            raise GenericLiveV1PosttradeError("performance does not exactly bind factual valuations")
        daily = validate_daily_lane_audit(daily_lane_audit)
        if (
            daily["lane_id"] != "generic-live-v1"
            or daily["lane_kind"] != "LIVE"
            or daily["account_id_hash"] != exact_plan["account_id_hash"]
            or daily["deployment_version"] != exact_plan["deployment_version"]
            or daily["as_of"] != perf["latest_as_of"]
            or perf["content_hash"] not in daily["source_hashes"]
        ):
            raise GenericLiveV1PosttradeError("daily audit does not bind Live performance")
        aggregate = validate_all_lane_audit(all_lane_audit)
        matching_summaries = [
            row for row in aggregate["lane_audits"] if row["lane_id"] == "generic-live-v1"
        ]
        if (
            len(matching_summaries) != 1
            or matching_summaries[0]["audit_hash"] != daily["content_hash"]
            or daily["content_hash"] not in aggregate["lane_audit_hashes"]
            or aggregate["as_of"] != daily["as_of"]
        ):
            raise GenericLiveV1PosttradeError("all-lane audit omits exact Live daily audit")
        dashboard = validate_dashboard_performance_surfaces(dashboard_projection)
        live_surfaces = [
            row for row in dashboard["performance_surfaces"]
            if row["lane_id"] == "generic-live-v1"
        ]
        if (
            daily["content_hash"] not in dashboard["source_audit_hashes"]
            or dashboard["as_of"] != daily["as_of"]
            or not live_surfaces
            or any(perf["content_hash"] not in row["source_hashes"] for row in live_surfaces)
        ):
            raise GenericLiveV1PosttradeError("dashboard omits exact Live audit/performance lineage")
    except Exception:
        try:
            rollback_break("REPORTING_BREAK")
        finally:
            raise
    try:
        base = finalize_generic_live_v1_posttrade(
            submission_result=submission_result, exact_plan=exact_plan,
            order_lifecycle=order_lifecycle, reconciliation=reconciled,
            journal_entries=journal, performance=perf,
            dashboard_projection=dashboard, finalized_at=finalized_at,
            rearm_state_path=rearm_state_path, result_path=base_result_path,
            rollback_handler=rollback_break,
        )
    except Exception:
        if not rolled_back:
            try:
                rollback_break("ORDER_BREAK")
            finally:
                raise
        raise
    try:
        evidence_hashes = sorted({
            base["content_hash"], reconciled["content_hash"],
            *[row["record_hash"] for row in session_rows], *valuation_hashes,
            perf["content_hash"], daily["content_hash"], aggregate["content_hash"],
            dashboard["content_hash"],
        })
        body = {
            "schema_version": GENERIC_LIVE_V1_PRODUCTION_CLOSURE_SCHEMA,
            "status": base["status"],
            "finalized_at": base["finalized_at"],
            "submission_result_hash": submission_result["content_hash"],
            "plan_hash": exact_plan["content_hash"],
            "reconciliation_hash": reconciled["content_hash"],
            "journal_hash": observed_journal_hash,
            "valuation_hashes": valuation_hashes,
            "performance_hash": perf["content_hash"],
            "daily_lane_audit_hash": daily["content_hash"],
            "all_lane_audit_hash": aggregate["content_hash"],
            "dashboard_projection_hash": dashboard["content_hash"],
            "base_posttrade_result_hash": base["content_hash"],
            "evidence_hashes": evidence_hashes,
            "rollback_required": base["rollback_required"],
            "generic_kill_switch_state": "ARMED",
            "broker_call_performed": False,
            "broker_submission_allowed": False,
            "execution_authority": False,
            "activation_authority": False,
        }
        body["content_hash"] = _hash(body)
        _write_exclusive(Path(closure_result_path), body)
        return body
    except Exception:
        if not rolled_back:
            try:
                rollback_break("REPORTING_BREAK")
            finally:
                raise
        raise


__all__ = [
    "GENERIC_LIVE_V1_PRODUCTION_CLOSURE_SCHEMA", "GenericLiveV1PosttradeError",
    "finalize_generic_live_v1_production_posttrade",
]
