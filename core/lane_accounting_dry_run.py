"""Off-by-default persistence builder for reconciled lane accounting.

The builder reads only an explicitly supplied journal path.  It never reads a
broker, deployment configuration, strategy registry, or scheduler state.  It
validates an exact v4 plan and its lane reconciliation through the pure
reconciled-fill bridge, then projects immutable journal additions.  A write is
performed only when ``write_enabled`` is the literal boolean ``True``.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from core.accounting_journal import (
    AccountingJournalError,
    append_accounting_journal,
    canonical_json,
    read_accounting_journal,
    validate_accounting_journal,
    validate_journal_batch,
)
from core.lane_valuation import accounting_journal_hash
from core.reconciled_fill_accounting import (
    ReconciledFillAccountingError,
    build_reconciled_fill_journal_entries,
)


LANE_ACCOUNTING_DRY_RUN_RESULT_SCHEMA = "caerus.lane_accounting_dry_run_result.v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "result_id",
        "status",
        "write_enabled",
        "journal_path",
        "journal_path_hash",
        "account_id_hash",
        "lane_id",
        "lane_kind",
        "deployment_version",
        "session_id",
        "plan_id",
        "plan_hash",
        "reconciliation_id",
        "reconciliation_hash",
        "reconciliation_status",
        "existing_entry_count",
        "candidate_entry_count",
        "addition_count",
        "final_entry_count",
        "existing_journal_hash",
        "projected_final_journal_hash",
        "observed_final_journal_hash",
        "candidate_record_hashes",
        "addition_record_hashes",
        "reconciliation_source_binding_count",
        "broker_call_performed",
        "execution_authority",
        "activation_authority",
        "approval_authority",
        "content_hash",
    }
)


class LaneAccountingDryRunError(ValueError):
    """Raised when accounting projection or persistence cannot be proven."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _string(value: Any, *, label: str, safe: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LaneAccountingDryRunError(f"{label} must be a non-blank string")
    if safe and (not _SAFE_ID.fullmatch(value) or ".." in value):
        raise LaneAccountingDryRunError(f"{label} is invalid")
    return value


def _sha(value: Any, *, label: str) -> str:
    result = _string(value, label=label)
    if not _SHA256.fullmatch(result):
        raise LaneAccountingDryRunError(f"{label} must be a lowercase SHA-256 digest")
    return result


def _strict_fields(payload: Mapping[str, Any]) -> None:
    missing = sorted(_RESULT_FIELDS - set(payload))
    unknown = sorted(set(payload) - _RESULT_FIELDS)
    if missing or unknown:
        raise LaneAccountingDryRunError(
            f"accounting dry-run result fields mismatch; missing={missing}, unknown={unknown}"
        )


def _journal_hash(entries: list[Mapping[str, Any]]) -> str | None:
    return accounting_journal_hash(entries) if entries else None


def _path_binding(path: Path | str) -> tuple[str, str]:
    resolved = str(Path(path).resolve())
    return resolved, hashlib.sha256(resolved.encode("utf-8")).hexdigest()


def validate_lane_accounting_dry_run_result(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneAccountingDryRunError("accounting dry-run result must be an object")
    _strict_fields(payload)
    if payload["schema_version"] != LANE_ACCOUNTING_DRY_RUN_RESULT_SCHEMA:
        raise LaneAccountingDryRunError("unsupported accounting dry-run result schema")
    _string(payload["result_id"], label="result_id", safe=True)
    if payload["status"] not in {"VALIDATED_NO_WRITE", "WRITTEN", "IDEMPOTENT"}:
        raise LaneAccountingDryRunError("unsupported accounting dry-run status")
    if type(payload["write_enabled"]) is not bool:
        raise LaneAccountingDryRunError("write_enabled must be boolean")
    if payload["status"] == "VALIDATED_NO_WRITE" and payload["write_enabled"] is not False:
        raise LaneAccountingDryRunError("no-write status cannot have writes enabled")
    if payload["status"] in {"WRITTEN", "IDEMPOTENT"} and payload["write_enabled"] is not True:
        raise LaneAccountingDryRunError("write status requires explicit write flag")
    journal_path = _string(payload["journal_path"], label="journal_path")
    if payload["journal_path_hash"] != hashlib.sha256(journal_path.encode("utf-8")).hexdigest():
        raise LaneAccountingDryRunError("journal path hash mismatch")
    for field in ("account_id_hash", "plan_hash", "reconciliation_hash"):
        _sha(payload[field], label=field)
    for field in (
        "lane_id",
        "deployment_version",
        "session_id",
        "plan_id",
        "reconciliation_id",
    ):
        _string(payload[field], label=field, safe=True)
    if payload["lane_kind"] not in {"PAPER", "LIVE"}:
        raise LaneAccountingDryRunError("lane_kind must be PAPER or LIVE")
    if payload["reconciliation_status"] not in {"PASS", "PARTIAL"}:
        raise LaneAccountingDryRunError("reconciliation_status must be PASS or PARTIAL")
    counts = (
        "existing_entry_count",
        "candidate_entry_count",
        "addition_count",
        "final_entry_count",
        "reconciliation_source_binding_count",
    )
    for field in counts:
        if isinstance(payload[field], bool) or not isinstance(payload[field], int) or payload[field] < 0:
            raise LaneAccountingDryRunError(f"{field} must be a nonnegative integer")
    if payload["addition_count"] > payload["candidate_entry_count"]:
        raise LaneAccountingDryRunError("addition_count exceeds candidate entries")
    if payload["final_entry_count"] != payload["existing_entry_count"] + payload["addition_count"]:
        raise LaneAccountingDryRunError("final entry count does not reconcile")
    if payload["reconciliation_source_binding_count"] != payload["candidate_entry_count"]:
        raise LaneAccountingDryRunError("reconciliation source binding count mismatch")
    for field, count_field in (
        ("candidate_record_hashes", "candidate_entry_count"),
        ("addition_record_hashes", "addition_count"),
    ):
        values = payload[field]
        if not isinstance(values, list) or len(values) != payload[count_field]:
            raise LaneAccountingDryRunError(f"{field} count mismatch")
        if values != sorted(set(values)):
            raise LaneAccountingDryRunError(f"{field} must be sorted and unique")
        for value in values:
            _sha(value, label=f"{field} item")
    for field in (
        "existing_journal_hash",
        "projected_final_journal_hash",
        "observed_final_journal_hash",
    ):
        if payload[field] is not None:
            _sha(payload[field], label=field)
    if payload["projected_final_journal_hash"] is None:
        raise LaneAccountingDryRunError("projected final journal hash is required")
    if payload["write_enabled"]:
        if payload["observed_final_journal_hash"] != payload["projected_final_journal_hash"]:
            raise LaneAccountingDryRunError("observed journal hash does not match projection")
    elif payload["observed_final_journal_hash"] is not None:
        raise LaneAccountingDryRunError("no-write result cannot claim an observed final hash")
    if payload["status"] == "WRITTEN" and payload["addition_count"] == 0:
        raise LaneAccountingDryRunError("WRITTEN requires at least one addition")
    if payload["status"] == "IDEMPOTENT" and payload["addition_count"] != 0:
        raise LaneAccountingDryRunError("IDEMPOTENT cannot contain additions")
    for field in (
        "broker_call_performed",
        "execution_authority",
        "activation_authority",
        "approval_authority",
    ):
        if payload[field] is not False:
            raise LaneAccountingDryRunError(f"{field} must remain false")
    if _sha(payload["content_hash"], label="content_hash") != _hash(payload):
        raise LaneAccountingDryRunError("accounting dry-run result content_hash mismatch")
    return json.loads(canonical_json(payload))


def run_lane_accounting_dry_run(
    *,
    exact_plan: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    journal_path: Path | str,
    write_enabled: bool = False,
) -> dict[str, Any]:
    """Validate a projected journal append and optionally persist it."""

    if type(write_enabled) is not bool:
        raise LaneAccountingDryRunError("write_enabled must be an explicit boolean")
    path = Path(journal_path)
    try:
        existing = read_accounting_journal(path)
        candidates = build_reconciled_fill_journal_entries(
            reconciliation, exact_plan=exact_plan
        )
        additions = validate_journal_batch(candidates, existing_entries=existing)
        projected = validate_accounting_journal([*existing, *additions])
    except (AccountingJournalError, ReconciledFillAccountingError) as exc:
        raise LaneAccountingDryRunError(f"accounting dry run failed: {exc}") from exc
    if not candidates:
        raise LaneAccountingDryRunError("accounting-ready reconciliation produced no candidates")
    reconciliation_hash = reconciliation["content_hash"]
    bound = [row for row in candidates if row["source_hash"] == reconciliation_hash]
    if len(bound) != len(candidates):
        raise LaneAccountingDryRunError("candidate entries lack reconciliation source binding")
    projected_hash = _journal_hash(projected)
    assert projected_hash is not None
    observed_hash: str | None = None
    if write_enabled:
        try:
            written = append_accounting_journal(path, additions)
            if written != len(additions):
                raise LaneAccountingDryRunError("journal append count mismatch")
            observed = read_accounting_journal(path)
        except AccountingJournalError as exc:
            raise LaneAccountingDryRunError(f"journal persistence failed: {exc}") from exc
        if len(observed) != len(projected):
            raise LaneAccountingDryRunError("journal read-back count mismatch")
        observed_hash = _journal_hash(observed)
        if observed_hash != projected_hash:
            raise LaneAccountingDryRunError("journal read-back hash mismatch")
        by_id = {row["journal_entry_id"]: row for row in observed}
        for candidate in candidates:
            persisted = by_id.get(candidate["journal_entry_id"])
            if (
                persisted is None
                or persisted["record_hash"] != candidate["record_hash"]
                or persisted["source_hash"] != reconciliation_hash
            ):
                raise LaneAccountingDryRunError(
                    "journal read-back lost reconciliation-bound candidate"
                )
    resolved_path, path_hash = _path_binding(path)
    status = (
        "VALIDATED_NO_WRITE"
        if not write_enabled
        else ("WRITTEN" if additions else "IDEMPOTENT")
    )
    seed = hashlib.sha256(
        canonical_json(
            {
                "reconciliation_hash": reconciliation_hash,
                "journal_path_hash": path_hash,
                "projected_final_journal_hash": projected_hash,
                "write_enabled": write_enabled,
            }
        ).encode("utf-8")
    ).hexdigest()
    body = {
        "schema_version": LANE_ACCOUNTING_DRY_RUN_RESULT_SCHEMA,
        "result_id": f"lane-accounting-dry-run:{reconciliation['lane_id']}:{seed[:24]}",
        "status": status,
        "write_enabled": write_enabled,
        "journal_path": resolved_path,
        "journal_path_hash": path_hash,
        "account_id_hash": reconciliation["account_id_hash"],
        "lane_id": reconciliation["lane_id"],
        "lane_kind": reconciliation["lane_kind"],
        "deployment_version": reconciliation["deployment_version"],
        "session_id": reconciliation["session_id"],
        "plan_id": reconciliation["plan_id"],
        "plan_hash": reconciliation["plan_hash"],
        "reconciliation_id": reconciliation["reconciliation_id"],
        "reconciliation_hash": reconciliation_hash,
        "reconciliation_status": reconciliation["status"],
        "existing_entry_count": len(existing),
        "candidate_entry_count": len(candidates),
        "addition_count": len(additions),
        "final_entry_count": len(projected),
        "existing_journal_hash": _journal_hash(existing),
        "projected_final_journal_hash": projected_hash,
        "observed_final_journal_hash": observed_hash,
        "candidate_record_hashes": sorted(row["record_hash"] for row in candidates),
        "addition_record_hashes": sorted(row["record_hash"] for row in additions),
        "reconciliation_source_binding_count": len(bound),
        "broker_call_performed": False,
        "execution_authority": False,
        "activation_authority": False,
        "approval_authority": False,
    }
    body["content_hash"] = _hash(body)
    return validate_lane_accounting_dry_run_result(body)


def read_strict_json(path: Path | str) -> dict[str, Any]:
    """Read one JSON object with duplicate-key and non-finite rejection."""

    def no_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LaneAccountingDryRunError(f"duplicate JSON object key: {key}")
            result[key] = value
        return result

    def no_constant(value: str) -> None:
        raise LaneAccountingDryRunError(f"non-finite JSON constant: {value}")

    source = Path(path)
    try:
        payload = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=no_duplicates,
            parse_constant=no_constant,
        )
    except LaneAccountingDryRunError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise LaneAccountingDryRunError(f"cannot read JSON artifact {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise LaneAccountingDryRunError(f"JSON artifact must be an object: {source}")
    return payload


__all__ = [
    "LANE_ACCOUNTING_DRY_RUN_RESULT_SCHEMA",
    "LaneAccountingDryRunError",
    "read_strict_json",
    "run_lane_accounting_dry_run",
    "validate_lane_accounting_dry_run_result",
]
