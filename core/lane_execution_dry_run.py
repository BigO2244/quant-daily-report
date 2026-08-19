"""Lane-neutral, broker-incapable execution safety dry run.

The module proves that an advisory v4 plan is bound to explicit safety
evidence, then exercises the submission-disabled OMS lifecycle.  It has no
broker adapter or credential reader.  Optional WAL persistence requires an
explicit flag and still stores only records whose contracts make submission
impossible.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from authority.lane_exact_plan import (
    canonical_json,
    validate_lane_exact_execution_plan,
)
from core.lane_oms import (
    build_lane_oms_attempt,
    build_lane_oms_intents,
    build_lane_oms_result,
    validate_lane_oms_lifecycle,
)
from core.lane_oms_store import append_lane_oms_store, read_lane_oms_store


LANE_EXECUTION_SAFETY_EVIDENCE_SCHEMA = "caerus.lane_execution_safety_evidence.v1"
LANE_EXECUTION_DRY_RUN_SCHEMA = "caerus.lane_execution_dry_run.v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_SAFETY_FIELDS = frozenset(
    {
        "schema_version",
        "evidence_id",
        "checked_at",
        "trade_date",
        "lane_id",
        "lane_kind",
        "deployment_version",
        "account_id_hash",
        "broker_environment",
        "plan_id",
        "plan_hash",
        "kill_switch_state",
        "account_pin_status",
        "deployment_sha_status",
        "open_order_status",
        "leverage_status",
        "shorting_status",
        "capital_ceiling_status",
        "credential_mode",
        "broker_call_performed",
        "execution_authority",
        "activation_authority",
        "source_hashes",
        "content_hash",
    }
)
_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "dry_run_id",
        "evaluated_at",
        "status",
        "write_enabled",
        "trade_date",
        "lane_id",
        "lane_kind",
        "deployment_version",
        "account_id_hash",
        "broker_environment",
        "plan_id",
        "plan_hash",
        "safety_evidence_id",
        "safety_evidence_hash",
        "gate_results",
        "reason_codes",
        "oms_intent_hashes",
        "oms_attempt_hashes",
        "oms_result_hashes",
        "wal_path_hash",
        "written_record_count",
        "recovered_record_count",
        "broker_call_performed",
        "broker_submission_allowed",
        "execution_authority",
        "activation_authority",
        "content_hash",
    }
)
_GATES = {
    "kill_switch_state": "ENGAGED",
    "account_pin_status": "MATCH",
    "deployment_sha_status": "MATCH",
    "open_order_status": "CLEAR",
    "leverage_status": "DISABLED",
    "shorting_status": "DISABLED",
    "capital_ceiling_status": "WITHIN_LIMIT",
    "credential_mode": "READ_ONLY_OR_NONE",
}


class LaneExecutionDryRunError(ValueError):
    """Raised when an advisory execution dry run is unsafe or malformed."""


def _strict_fields(payload: Mapping[str, Any], expected: frozenset[str], *, label: str) -> None:
    missing = sorted(expected - set(payload))
    unknown = sorted(set(payload) - expected)
    if missing or unknown:
        raise LaneExecutionDryRunError(
            f"{label} fields mismatch; missing={missing}, unknown={unknown}"
        )


def _string(value: Any, *, label: str, safe: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LaneExecutionDryRunError(f"{label} must be a non-blank string")
    if safe and (not _SAFE_ID.fullmatch(value) or ".." in value):
        raise LaneExecutionDryRunError(f"{label} is invalid")
    return value


def _sha(value: Any, *, label: str) -> str:
    result = _string(value, label=label)
    if not _SHA256.fullmatch(result):
        raise LaneExecutionDryRunError(f"{label} must be a lowercase SHA-256 digest")
    return result


def _timestamp(value: Any, *, label: str) -> tuple[str, dt.datetime]:
    raw = _string(value, label=label)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LaneExecutionDryRunError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LaneExecutionDryRunError(f"{label} must include a timezone")
    return raw, parsed


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def build_lane_execution_safety_evidence(
    *,
    exact_plan: Mapping[str, Any],
    checked_at: str,
    source_hashes: Sequence[str],
    kill_switch_state: str = "ENGAGED",
    account_pin_status: str = "MATCH",
    deployment_sha_status: str = "MATCH",
    open_order_status: str = "CLEAR",
    leverage_status: str = "DISABLED",
    shorting_status: str = "DISABLED",
    capital_ceiling_status: str = "WITHIN_LIMIT",
    credential_mode: str = "READ_ONLY_OR_NONE",
) -> dict[str, Any]:
    """Seal explicit safety claims for one advisory exact plan."""

    failures = validate_lane_exact_execution_plan(exact_plan)
    if failures:
        raise LaneExecutionDryRunError("exact plan is invalid: " + ",".join(failures))
    seed = hashlib.sha256(
        canonical_json(
            {
                "plan_hash": exact_plan["content_hash"],
                "checked_at": checked_at,
                "source_hashes": sorted(set(source_hashes)),
            }
        ).encode("utf-8")
    ).hexdigest()
    body = {
        "schema_version": LANE_EXECUTION_SAFETY_EVIDENCE_SCHEMA,
        "evidence_id": f"lane-safety:{exact_plan['lane_id']}:{seed[:24]}",
        "checked_at": checked_at,
        "trade_date": exact_plan["trade_date"],
        "lane_id": exact_plan["lane_id"],
        "lane_kind": exact_plan["lane_kind"],
        "deployment_version": exact_plan["deployment_version"],
        "account_id_hash": exact_plan["account_id_hash"],
        "broker_environment": exact_plan["broker_environment"],
        "plan_id": exact_plan["plan_id"],
        "plan_hash": exact_plan["content_hash"],
        "kill_switch_state": kill_switch_state,
        "account_pin_status": account_pin_status,
        "deployment_sha_status": deployment_sha_status,
        "open_order_status": open_order_status,
        "leverage_status": leverage_status,
        "shorting_status": shorting_status,
        "capital_ceiling_status": capital_ceiling_status,
        "credential_mode": credential_mode,
        "broker_call_performed": False,
        "execution_authority": False,
        "activation_authority": False,
        "source_hashes": sorted(set(source_hashes)),
    }
    body["content_hash"] = _hash(body)
    return validate_lane_execution_safety_evidence(body, exact_plan=exact_plan)


def validate_lane_execution_safety_evidence(
    payload: Mapping[str, Any], *, exact_plan: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneExecutionDryRunError("safety evidence must be an object")
    _strict_fields(payload, _SAFETY_FIELDS, label="safety evidence")
    if payload["schema_version"] != LANE_EXECUTION_SAFETY_EVIDENCE_SCHEMA:
        raise LaneExecutionDryRunError("unsupported safety evidence schema")
    _string(payload["evidence_id"], label="evidence_id", safe=True)
    _timestamp(payload["checked_at"], label="checked_at")
    for field in ("lane_id", "deployment_version", "plan_id", "broker_environment"):
        _string(payload[field], label=field, safe=True)
    if payload["lane_kind"] not in {"PAPER", "LIVE"}:
        raise LaneExecutionDryRunError("execution safety evidence supports PAPER or LIVE")
    for field in ("account_id_hash", "plan_hash"):
        _sha(payload[field], label=field)
    for field in _GATES:
        _string(payload[field], label=field)
    sources = payload["source_hashes"]
    if not isinstance(sources, list) or not sources or sources != sorted(set(sources)):
        raise LaneExecutionDryRunError("source_hashes must be a non-empty sorted unique array")
    for value in sources:
        _sha(value, label="source_hash")
    if (
        payload["broker_call_performed"] is not False
        or payload["execution_authority"] is not False
        or payload["activation_authority"] is not False
    ):
        raise LaneExecutionDryRunError("safety evidence cannot call a broker or grant authority")
    if _sha(payload["content_hash"], label="content_hash") != _hash(payload):
        raise LaneExecutionDryRunError("safety evidence content_hash mismatch")
    if exact_plan is not None:
        failures = validate_lane_exact_execution_plan(exact_plan)
        if failures:
            raise LaneExecutionDryRunError("exact plan is invalid: " + ",".join(failures))
        bindings = (
            "trade_date",
            "lane_id",
            "lane_kind",
            "deployment_version",
            "account_id_hash",
            "broker_environment",
            "plan_id",
        )
        if any(payload[field] != exact_plan[field] for field in bindings) or payload[
            "plan_hash"
        ] != exact_plan["content_hash"]:
            raise LaneExecutionDryRunError("safety evidence scope differs from exact plan")
        _, checked_at = _timestamp(payload["checked_at"], label="checked_at")
        _, planned_at = _timestamp(exact_plan["planned_at"], label="planned_at")
        _, expires_at = _timestamp(exact_plan["expires_at"], label="expires_at")
        if checked_at < planned_at or checked_at > expires_at:
            raise LaneExecutionDryRunError(
                "safety evidence was not checked within the exact-plan validity window"
            )
    return json.loads(canonical_json(payload))


def _gate_results(evidence: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    results: dict[str, str] = {}
    blockers: list[str] = []
    for field, expected in _GATES.items():
        passed = evidence[field] == expected
        results[field] = "PASS" if passed else "BLOCK"
        if not passed:
            blockers.append(f"{field}:{evidence[field]}")
    return results, sorted(blockers)


def run_lane_execution_dry_run(
    *,
    exact_plan: Mapping[str, Any],
    safety_evidence: Mapping[str, Any],
    wal_path: Path | str | None = None,
    write_enabled: bool = False,
) -> dict[str, Any]:
    """Exercise the broker-disabled OMS lifecycle, with optional advisory WAL."""

    if type(write_enabled) is not bool:
        raise LaneExecutionDryRunError("write_enabled must be an explicit boolean")
    failures = validate_lane_exact_execution_plan(exact_plan)
    if failures:
        raise LaneExecutionDryRunError("exact plan is invalid: " + ",".join(failures))
    evidence = validate_lane_execution_safety_evidence(
        safety_evidence, exact_plan=exact_plan
    )
    gates, blockers = _gate_results(evidence)
    intents: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    written = 0
    recovered = 0
    wal_hash = None
    if not blockers:
        intents = build_lane_oms_intents(exact_plan)
        attempts = [build_lane_oms_attempt(row) for row in intents]
        results = [
            build_lane_oms_result(intent, attempt)
            for intent, attempt in zip(intents, attempts, strict=True)
        ]
        validate_lane_oms_lifecycle(
            intents, attempts, results, exact_plan=exact_plan
        )
        if write_enabled:
            if wal_path is None:
                raise LaneExecutionDryRunError(
                    "advisory WAL persistence requires an explicit wal_path"
                )
            path = Path(wal_path)
            wal_hash = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
            records = [*intents, *attempts, *results]
            written = append_lane_oms_store(
                path,
                records,
                write=True,
                exact_plan=exact_plan,
                require_complete_lifecycle=True,
            )
            recovered_rows = read_lane_oms_store(
                path,
                exact_plan=exact_plan,
                require_complete_lifecycle=True,
            )
            recovered = len(recovered_rows)
            if recovered != len(records):
                raise LaneExecutionDryRunError("advisory WAL recovery count mismatch")
    elif write_enabled:
        raise LaneExecutionDryRunError("blocked safety evidence cannot persist an OMS lifecycle")

    evaluated_at = evidence["checked_at"]
    if blockers:
        status = "BLOCKED"
    elif write_enabled:
        status = "ADVISORY_WAL_WRITTEN" if written else "ADVISORY_WAL_IDEMPOTENT"
    else:
        status = "VALIDATED_NO_WRITE"
    body = {
        "schema_version": LANE_EXECUTION_DRY_RUN_SCHEMA,
        "dry_run_id": "pending",
        "evaluated_at": evaluated_at,
        "status": status,
        "write_enabled": write_enabled,
        "trade_date": exact_plan["trade_date"],
        "lane_id": exact_plan["lane_id"],
        "lane_kind": exact_plan["lane_kind"],
        "deployment_version": exact_plan["deployment_version"],
        "account_id_hash": exact_plan["account_id_hash"],
        "broker_environment": exact_plan["broker_environment"],
        "plan_id": exact_plan["plan_id"],
        "plan_hash": exact_plan["content_hash"],
        "safety_evidence_id": evidence["evidence_id"],
        "safety_evidence_hash": evidence["content_hash"],
        "gate_results": gates,
        "reason_codes": blockers or ["BROKER_SUBMISSION_STRUCTURALLY_DISABLED"],
        "oms_intent_hashes": [row["content_hash"] for row in intents],
        "oms_attempt_hashes": [row["content_hash"] for row in attempts],
        "oms_result_hashes": [row["content_hash"] for row in results],
        "wal_path_hash": wal_hash,
        "written_record_count": written,
        "recovered_record_count": recovered,
        "broker_call_performed": False,
        "broker_submission_allowed": False,
        "execution_authority": False,
        "activation_authority": False,
    }
    seed = hashlib.sha256(
        canonical_json(
            {
                "plan_hash": body["plan_hash"],
                "safety_hash": body["safety_evidence_hash"],
                "status": status,
                "wal_path_hash": wal_hash,
            }
        ).encode("utf-8")
    ).hexdigest()
    body["dry_run_id"] = f"lane-execution-dry-run:{exact_plan['lane_id']}:{seed[:24]}"
    body["content_hash"] = _hash(body)
    return validate_lane_execution_dry_run_result(body)


def validate_lane_execution_dry_run_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneExecutionDryRunError("execution dry-run result must be an object")
    _strict_fields(payload, _RESULT_FIELDS, label="execution dry-run result")
    if payload["schema_version"] != LANE_EXECUTION_DRY_RUN_SCHEMA:
        raise LaneExecutionDryRunError("unsupported execution dry-run schema")
    _string(payload["dry_run_id"], label="dry_run_id", safe=True)
    _timestamp(payload["evaluated_at"], label="evaluated_at")
    if payload["status"] not in {
        "BLOCKED",
        "VALIDATED_NO_WRITE",
        "ADVISORY_WAL_WRITTEN",
        "ADVISORY_WAL_IDEMPOTENT",
    }:
        raise LaneExecutionDryRunError("unsupported execution dry-run status")
    if type(payload["write_enabled"]) is not bool:
        raise LaneExecutionDryRunError("write_enabled must be boolean")
    for field in (
        "lane_id",
        "deployment_version",
        "plan_id",
        "broker_environment",
        "safety_evidence_id",
    ):
        _string(payload[field], label=field, safe=True)
    if payload["lane_kind"] not in {"PAPER", "LIVE"}:
        raise LaneExecutionDryRunError("dry-run lane_kind is invalid")
    for field in ("account_id_hash", "plan_hash", "safety_evidence_hash"):
        _sha(payload[field], label=field)
    gates = payload["gate_results"]
    if not isinstance(gates, Mapping) or set(gates) != set(_GATES):
        raise LaneExecutionDryRunError("gate_results are incomplete")
    if any(value not in {"PASS", "BLOCK"} for value in gates.values()):
        raise LaneExecutionDryRunError("gate result is invalid")
    reasons = payload["reason_codes"]
    if not isinstance(reasons, list) or not reasons or reasons != sorted(set(reasons)):
        raise LaneExecutionDryRunError("reason_codes must be sorted and unique")
    for field in ("oms_intent_hashes", "oms_attempt_hashes", "oms_result_hashes"):
        hashes = payload[field]
        if not isinstance(hashes, list):
            raise LaneExecutionDryRunError(f"{field} must be an array")
        for value in hashes:
            _sha(value, label=field)
    counts = {
        len(payload["oms_intent_hashes"]),
        len(payload["oms_attempt_hashes"]),
        len(payload["oms_result_hashes"]),
    }
    if len(counts) != 1:
        raise LaneExecutionDryRunError("OMS lifecycle hash counts differ")
    if isinstance(payload["written_record_count"], bool) or not isinstance(
        payload["written_record_count"], int
    ) or payload["written_record_count"] < 0:
        raise LaneExecutionDryRunError("written_record_count must be nonnegative")
    if isinstance(payload["recovered_record_count"], bool) or not isinstance(
        payload["recovered_record_count"], int
    ) or payload["recovered_record_count"] < 0:
        raise LaneExecutionDryRunError("recovered_record_count must be nonnegative")
    blocked = any(value == "BLOCK" for value in gates.values())
    if blocked != (payload["status"] == "BLOCKED"):
        raise LaneExecutionDryRunError("status does not match safety gates")
    if blocked and any(counts):
        raise LaneExecutionDryRunError("blocked dry run cannot contain OMS lifecycle")
    if payload["write_enabled"]:
        if payload["status"] == "BLOCKED":
            raise LaneExecutionDryRunError("blocked dry run cannot enable writes")
        if payload["wal_path_hash"] is None:
            raise LaneExecutionDryRunError("write-enabled dry run requires WAL path hash")
        _sha(payload["wal_path_hash"], label="wal_path_hash")
        lifecycle_records = 3 * len(payload["oms_intent_hashes"])
        if payload["recovered_record_count"] != lifecycle_records:
            raise LaneExecutionDryRunError("write-enabled WAL recovery count mismatch")
        if payload["status"] == "ADVISORY_WAL_WRITTEN":
            if payload["written_record_count"] != lifecycle_records:
                raise LaneExecutionDryRunError("written WAL record count mismatch")
        elif payload["status"] == "ADVISORY_WAL_IDEMPOTENT":
            if payload["written_record_count"] != 0:
                raise LaneExecutionDryRunError("idempotent WAL result wrote records")
        else:
            raise LaneExecutionDryRunError("write_enabled status is invalid")
    else:
        if (
            payload["wal_path_hash"] is not None
            or payload["written_record_count"] != 0
            or payload["recovered_record_count"] != 0
        ):
            raise LaneExecutionDryRunError("no-write dry run cannot report WAL persistence")
        if payload["status"] not in {"BLOCKED", "VALIDATED_NO_WRITE"}:
            raise LaneExecutionDryRunError("no-write status is invalid")
    if (
        payload["broker_call_performed"] is not False
        or payload["broker_submission_allowed"] is not False
        or payload["execution_authority"] is not False
        or payload["activation_authority"] is not False
    ):
        raise LaneExecutionDryRunError("execution dry run cannot call a broker or grant authority")
    if _sha(payload["content_hash"], label="content_hash") != _hash(payload):
        raise LaneExecutionDryRunError("execution dry-run content_hash mismatch")
    return json.loads(canonical_json(payload))


__all__ = [
    "LANE_EXECUTION_DRY_RUN_SCHEMA",
    "LANE_EXECUTION_SAFETY_EVIDENCE_SCHEMA",
    "LaneExecutionDryRunError",
    "build_lane_execution_safety_evidence",
    "run_lane_execution_dry_run",
    "validate_lane_execution_dry_run_result",
    "validate_lane_execution_safety_evidence",
]
