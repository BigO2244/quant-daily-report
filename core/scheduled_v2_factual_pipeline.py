"""Disabled-by-default orchestration for decision v2 and factual lane inputs.

This module is deliberately not a scheduler.  It is a production-shaped,
explicit-input boundary which a future governed scheduler may call.  It emits
decision v2 in memory, proves reconciled PAPER/LIVE accounting and reporting
inputs, and describes (but never performs) the corresponding history append.
The only optional side effect is immutable content-addressed evidence storage,
guarded by two literal boolean opt-ins.
"""

from __future__ import annotations

import copy
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from authority.lane_exact_plan import canonical_json
from core.lane_factual_reporting_inputs import (
    build_lane_factual_reporting_inputs,
)
from core.lane_execution_dry_run import run_lane_execution_dry_run
from core.sleeve_decision_adapter import (
    build_sleeve_decision_v2_batch,
    validate_adapted_sleeve_decision_batch,
)


PIPELINE_RESULT_SCHEMA = "caerus.scheduled_v2_factual_pipeline_result.v1"
PIPELINE_RUN_RECEIPT_SCHEMA = "caerus.scheduled_v2_factual_pipeline_run_receipt.v1"
ACCOUNTING_INPUT_SCHEMA = "caerus.factual_accounting_input_descriptor.v1"
HISTORY_INPUT_SCHEMA = "caerus.factual_history_append_input.v1"
GENERIC_EXECUTION_ADAPTER = "core.lane_execution_dry_run:caerus.lane_execution_dry_run.v1"
DECISION_V2_ADAPTER = "core.sleeve_decision_adapter:caerus.sleeve_decision_batch.v2"


class ScheduledV2FactualPipelineError(ValueError):
    """Raised when a rehearsal cannot prove its inputs or safety boundary."""


def _hash(payload: Any, *, remove_content_hash: bool = False) -> str:
    body = copy.deepcopy(payload)
    if remove_content_hash and isinstance(body, dict):
        body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _strict_bool(value: Any, *, label: str) -> bool:
    if type(value) is not bool:
        raise ScheduledV2FactualPipelineError(f"{label} must be a literal boolean")
    return value


def _lane_inputs(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or not rows:
        raise ScheduledV2FactualPipelineError("lane_inputs must be a non-empty sequence")
    expected = {
        "exact_plan",
        "reconciliation",
        "ending_state",
        "journal_entries",
        "prior_valuations",
        "valuation_date",
        "safety_evidence",
    }
    normalized: list[Mapping[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != expected:
            raise ScheduledV2FactualPipelineError(
                f"lane_inputs[{index}] fields must be exactly {sorted(expected)}"
            )
        normalized.append(row)
    return normalized


def _accounting_descriptor(
    source: Mapping[str, Any], lane_input: Mapping[str, Any]
) -> dict[str, Any]:
    reconciliation = lane_input["reconciliation"]
    entries = lane_input["journal_entries"]
    body = {
        "schema_version": ACCOUNTING_INPUT_SCHEMA,
        "lane_id": source["lane_id"],
        "lane_kind": source["lane_kind"],
        "deployment_version": source["deployment_version"],
        "account_id_hash": source["account_id_hash"],
        "plan_hash": source["plan_hash"],
        "reconciliation_hash": source["reconciliation_hash"],
        "reconciliation_status": reconciliation["status"],
        "accounting_ready": reconciliation["accounting_ready"],
        "reconciled_fill_count": len(reconciliation["reconciled_fills"]),
        "reconciled_fill_hashes": [
            _hash(row) for row in reconciliation["reconciled_fills"]
        ],
        "journal_hash": source["journal_hash"],
        "journal_entry_count": len(entries),
        "journal_record_hashes": [row["record_hash"] for row in entries],
        "factual": True,
        "accounting_write_authorized": False,
    }
    body["content_hash"] = _hash(body)
    return body


def _history_descriptor(source: Mapping[str, Any]) -> dict[str, Any]:
    valuation = source["valuation"]
    performance = source["performance"]
    body = {
        "schema_version": HISTORY_INPUT_SCHEMA,
        "valuation_date": source["valuation_date"],
        "as_of": source["as_of"],
        "lane_id": source["lane_id"],
        "lane_kind": source["lane_kind"],
        "deployment_version": source["deployment_version"],
        "account_id_hash": source["account_id_hash"],
        "performance_surface": valuation["performance_surface"],
        "economic_authority": valuation["economic_authority"],
        "valuation_hash": valuation["content_hash"],
        "performance_hash": performance["content_hash"],
        "source_valuation_hashes": performance["source_valuation_hashes"],
        "factual": True,
        "append_only_required": True,
        "history_write_authorized": False,
    }
    body["content_hash"] = _hash(body)
    return body


def build_scheduled_v2_factual_pipeline(
    *,
    evaluation_batch: Mapping[str, Any],
    expected_sleeve_ids: Sequence[str],
    session_id: str,
    session_hash: str,
    generated_at: str,
    decision_inputs: Mapping[str, Mapping[str, Any]],
    lane_inputs: Sequence[Mapping[str, Any]],
    expected_lane_kinds: Sequence[str] = ("PAPER", "LIVE"),
    schedule_enabled: bool = False,
    write_enabled: bool = False,
) -> dict[str, Any]:
    """Build a deterministic PAPER/LIVE rehearsal without external reads/writes."""

    schedule_enabled = _strict_bool(schedule_enabled, label="schedule_enabled")
    write_enabled = _strict_bool(write_enabled, label="write_enabled")
    if write_enabled and not schedule_enabled:
        raise ScheduledV2FactualPipelineError(
            "write_enabled requires the independent schedule_enabled opt-in"
        )
    kinds = [str(value or "").strip().upper() for value in expected_lane_kinds]
    if not kinds or any(value not in {"PAPER", "LIVE"} for value in kinds):
        raise ScheduledV2FactualPipelineError(
            "expected_lane_kinds must contain only PAPER and LIVE"
        )
    if len(kinds) != len(set(kinds)):
        raise ScheduledV2FactualPipelineError("expected_lane_kinds contains duplicates")

    decisions = build_sleeve_decision_v2_batch(
        evaluation_batch=evaluation_batch,
        expected_sleeve_ids=expected_sleeve_ids,
        session_id=session_id,
        session_hash=session_hash,
        generated_at=generated_at,
        decision_inputs=decision_inputs,
    )
    failures = validate_adapted_sleeve_decision_batch(
        decisions,
        expected_sleeve_ids=expected_sleeve_ids,
        evaluation_batch=evaluation_batch,
        decision_inputs=decision_inputs,
    )
    if failures:
        raise ScheduledV2FactualPipelineError(
            "decision v2 emission is invalid: " + ",".join(failures)
        )

    factual_rows: list[dict[str, Any]] = []
    observed_kinds: list[str] = []
    observed_lane_ids: list[str] = []
    valuation_dates: set[str] = set()
    for lane_input in _lane_inputs(lane_inputs):
        plan = lane_input["exact_plan"]
        if (
            plan.get("session_id") != decisions["session_id"]
            or plan.get("session_hash") != decisions["session_hash"]
            or plan.get("trade_date") != decisions["trade_date"]
        ):
            raise ScheduledV2FactualPipelineError(
                "lane plan session scope differs from decision emission"
            )
        execution_rehearsal = run_lane_execution_dry_run(
            exact_plan=plan,
            safety_evidence=lane_input["safety_evidence"],
            write_enabled=False,
        )
        if (
            execution_rehearsal["status"] != "VALIDATED_NO_WRITE"
            or execution_rehearsal["write_enabled"] is not False
            or execution_rehearsal["broker_submission_allowed"] is not False
            or execution_rehearsal["broker_call_performed"] is not False
        ):
            raise ScheduledV2FactualPipelineError(
                "lane execution rehearsal must be VALIDATED_NO_WRITE and broker-disabled"
            )
        built = build_lane_factual_reporting_inputs(
            exact_plan=plan,
            reconciliation=lane_input["reconciliation"],
            ending_state=lane_input["ending_state"],
            journal_entries=lane_input["journal_entries"],
            prior_valuations=lane_input["prior_valuations"],
            valuation_date=lane_input["valuation_date"],
        )
        if built["write_enabled"] is not False or built["write_performed"] is not False:
            raise ScheduledV2FactualPipelineError(
                "nested factual reporting builder unexpectedly authorized a write"
            )
        if built["broker_call_performed"] is not False:
            raise ScheduledV2FactualPipelineError(
                "nested factual reporting builder unexpectedly called a broker"
            )
        observed_kinds.append(built["lane_kind"])
        observed_lane_ids.append(built["lane_id"])
        valuation_dates.add(built["valuation_date"])
        factual_rows.append(
            {
                "lane_id": built["lane_id"],
                "lane_kind": built["lane_kind"],
                "execution_adapter": GENERIC_EXECUTION_ADAPTER,
                "execution_evidence_classification": "STRUCTURAL_REHEARSAL",
                "execution_rehearsal_hash": execution_rehearsal["content_hash"],
                "execution_rehearsal": execution_rehearsal,
                "accounting_input": _accounting_descriptor(built, lane_input),
                "reporting_input": built,
                "history_input": _history_descriptor(built),
            }
        )
    if len(observed_lane_ids) != len(set(observed_lane_ids)):
        raise ScheduledV2FactualPipelineError("lane_inputs contains duplicate lane_id")
    if sorted(observed_kinds) != sorted(kinds):
        raise ScheduledV2FactualPipelineError(
            "lane_inputs do not exactly cover expected_lane_kinds"
        )
    if len(valuation_dates) != 1:
        raise ScheduledV2FactualPipelineError(
            "all factual lanes must use one valuation_date"
        )
    if next(iter(valuation_dates)) != decisions["trade_date"]:
        raise ScheduledV2FactualPipelineError(
            "factual valuation_date must equal decision emission trade_date"
        )
    factual_rows.sort(key=lambda row: (row["lane_kind"], row["lane_id"]))

    if write_enabled:
        status = "PERSISTENCE_REQUESTED"
    elif schedule_enabled:
        status = "ENABLED_NO_WRITE_REHEARSAL"
    else:
        status = "DISABLED_NO_WRITE_REHEARSAL"
    body = {
        "schema_version": PIPELINE_RESULT_SCHEMA,
        "run_id": "scheduled-v2-factual:" + _hash(
            {
                "decision_batch_hash": decisions["content_hash"],
                "lane_input_hashes": [
                    row["reporting_input"]["content_hash"] for row in factual_rows
                ],
                "generated_at": generated_at,
            }
        )[:24],
        "trade_date": decisions["trade_date"],
        "generated_at": generated_at,
        "session_id": decisions["session_id"],
        "session_hash": decisions["session_hash"],
        "status": status,
        "schedule_enabled": schedule_enabled,
        "write_enabled": write_enabled,
        "write_performed": False,
        "decision_emission": {
            "evaluation_batch_hash": _hash(evaluation_batch),
            "adapter": DECISION_V2_ADAPTER,
            "expected_sleeve_ids": decisions["expected_sleeve_ids"],
            "decision_batch": decisions,
        },
        "factual_lanes": factual_rows,
        "expected_lane_kinds": sorted(kinds),
        "paper_live_rehearsal": set(kinds) == {"PAPER", "LIVE"},
        "generic_execution_adapter": GENERIC_EXECUTION_ADAPTER,
        "decision_to_plan_binding": "SESSION_SCOPE_ONLY",
        "broker_call_performed": False,
        "broker_submission_allowed": False,
        "execution_authority": False,
        "activation_authority": False,
        "registry_mutation_performed": False,
        "runtime_mutation_performed": False,
        "scheduler_mutation_performed": False,
        "dashboard_mutation_performed": False,
        "official_history_mutation_performed": False,
    }
    body["content_hash"] = _hash(body)
    return body


def validate_scheduled_v2_factual_pipeline(
    payload: Mapping[str, Any],
    *,
    evaluation_batch: Mapping[str, Any],
    expected_sleeve_ids: Sequence[str],
    session_id: str,
    session_hash: str,
    generated_at: str,
    decision_inputs: Mapping[str, Mapping[str, Any]],
    lane_inputs: Sequence[Mapping[str, Any]],
    expected_lane_kinds: Sequence[str] = ("PAPER", "LIVE"),
) -> dict[str, Any]:
    """Rebuild from explicit evidence and require exact canonical equality."""

    if not isinstance(payload, Mapping):
        raise ScheduledV2FactualPipelineError("pipeline result must be an object")
    rebuilt = build_scheduled_v2_factual_pipeline(
        evaluation_batch=evaluation_batch,
        expected_sleeve_ids=expected_sleeve_ids,
        session_id=session_id,
        session_hash=session_hash,
        generated_at=generated_at,
        decision_inputs=decision_inputs,
        lane_inputs=lane_inputs,
        expected_lane_kinds=expected_lane_kinds,
        schedule_enabled=payload.get("schedule_enabled"),
        write_enabled=payload.get("write_enabled"),
    )
    if payload.get("status") == "PERSISTED_VERIFIED":
        if (
            payload.get("schedule_enabled") is not True
            or payload.get("write_enabled") is not True
            or payload.get("write_performed") is not True
        ):
            raise ScheduledV2FactualPipelineError(
                "persisted result lacks its two opt-ins and verified write claim"
            )
        rebuilt["status"] = "PERSISTED_VERIFIED"
        rebuilt["write_performed"] = True
        rebuilt["content_hash"] = _hash(rebuilt, remove_content_hash=True)
    if dict(payload) != rebuilt:
        raise ScheduledV2FactualPipelineError(
            "pipeline result differs from its explicit canonical inputs"
        )
    return copy.deepcopy(rebuilt)


def _persist_immutable(
    payload: Mapping[str, Any], output_root: Path | str
) -> tuple[str, str]:
    root = Path(output_root)
    path = (
        root
        / "scheduled_v2_factual_pipeline"
        / str(payload["trade_date"])
        / f"{payload['content_hash']}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = (canonical_json(payload) + "\n").encode("utf-8")
    lock_path = path.parent / ".pipeline.lock"
    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        temporary: str | None = None
        outcome = "CREATED"
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, delete=False
            ) as handle:
                temporary = handle.name
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                outcome = "ALREADY_EXISTS"
                if path.read_bytes() != serialized:
                    raise ScheduledV2FactualPipelineError(
                        "immutable pipeline artifact path conflict"
                    )
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            try:
                observed = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ScheduledV2FactualPipelineError(
                    "persisted pipeline artifact failed read-back"
                ) from exc
            if observed != payload or observed.get("content_hash") != _hash(
                observed, remove_content_hash=True
            ):
                raise ScheduledV2FactualPipelineError(
                    "persisted pipeline artifact failed canonical read-back validation"
                )
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    return outcome, str(path)


def run_scheduled_v2_factual_pipeline(
    *, output_root: Path | str | None = None, **inputs: Any
) -> dict[str, Any]:
    """Return an artifact plus an operational persistence receipt.

    The receipt intentionally is not part of the persisted content address:
    the first caller can accurately observe ``CREATED`` and an exact replay
    can observe ``ALREADY_EXISTS`` while both prove the same immutable bytes.
    """

    artifact = build_scheduled_v2_factual_pipeline(**inputs)
    outcome = "NOT_REQUESTED"
    path: str | None = None
    if artifact["write_enabled"]:
        if output_root is None:
            raise ScheduledV2FactualPipelineError(
                "output_root is required when persistence is requested"
            )
        artifact["status"] = "PERSISTED_VERIFIED"
        artifact["write_performed"] = True
        artifact["content_hash"] = _hash(artifact, remove_content_hash=True)
        outcome, path = _persist_immutable(artifact, output_root)
    receipt = {
        "schema_version": PIPELINE_RUN_RECEIPT_SCHEMA,
        "artifact": artifact,
        "persistence": {
            "requested": artifact["write_enabled"],
            "performed": artifact["write_performed"],
            "outcome": outcome,
            "artifact_path": path,
            "artifact_hash": artifact["content_hash"],
            "read_back_verified": artifact["write_performed"],
        },
    }
    receipt["content_hash"] = _hash(receipt)
    return json.loads(canonical_json(receipt))


def _load_cli_inputs(path: Path) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ScheduledV2FactualPipelineError(
                    f"input bundle contains duplicate JSON key: {key}"
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ScheduledV2FactualPipelineError(
            f"input bundle contains non-finite JSON constant: {value}"
        )

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ScheduledV2FactualPipelineError(
            "input bundle must be readable strict JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise ScheduledV2FactualPipelineError("input bundle must be a JSON object")
    forbidden = {"schedule_enabled", "write_enabled", "output_root"} & set(payload)
    if forbidden:
        raise ScheduledV2FactualPipelineError(
            "input bundle cannot smuggle command authority fields: "
            + ",".join(sorted(forbidden))
        )
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    """Explicit-file command boundary; never reads broker, runtime, or config."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--schedule-enabled", action="store_true")
    parser.add_argument("--write-enabled", action="store_true")
    args = parser.parse_args(argv)
    if args.write_enabled and args.output_root is None:
        parser.error("--output-root is required with --write-enabled")
    inputs = _load_cli_inputs(args.input)
    receipt = run_scheduled_v2_factual_pipeline(
        output_root=args.output_root,
        schedule_enabled=args.schedule_enabled,
        write_enabled=args.write_enabled,
        **inputs,
    )
    print(canonical_json(receipt))
    return 0


__all__ = [
    "ACCOUNTING_INPUT_SCHEMA",
    "DECISION_V2_ADAPTER",
    "HISTORY_INPUT_SCHEMA",
    "GENERIC_EXECUTION_ADAPTER",
    "PIPELINE_RESULT_SCHEMA",
    "PIPELINE_RUN_RECEIPT_SCHEMA",
    "ScheduledV2FactualPipelineError",
    "build_scheduled_v2_factual_pipeline",
    "run_scheduled_v2_factual_pipeline",
    "validate_scheduled_v2_factual_pipeline",
]


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI boundary
    raise SystemExit(main())
