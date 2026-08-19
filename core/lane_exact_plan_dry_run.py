"""Off-by-default, lane-neutral builder for advisory exact execution plans.

This module is deliberately outside every executor.  It accepts three explicit,
already-governed inputs, builds and validates one v4 advisory plan, and only
persists that plan when the caller passes the literal write opt-in.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from authority.lane_exact_plan import (
    LaneExactPlanError,
    build_lane_exact_execution_plan,
    canonical_json,
    serialize_lane_exact_execution_plan,
    validate_lane_exact_execution_plan,
)


DRY_RUN_RESULT_SCHEMA = "caerus.lane_exact_plan_dry_run_result.v1"
_SAFE_PATH_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_RESULT_FIELDS = frozenset(
    {
        "schema_version", "status", "execution_authority", "approval_authority",
        "broker_call_performed", "configuration_mutated", "deployment_activated",
        "write_enabled", "write_performed", "artifact_path", "lane_id", "lane_kind",
        "account_id_hash", "deployment_version", "plan_hash", "source_hashes",
        "plan", "content_hash",
    }
)


class LaneExactPlanDryRunError(ValueError):
    """Raised when explicit advisory plan inputs or persistence are unsafe."""


def _reject_constant(value: str) -> None:
    raise LaneExactPlanDryRunError(f"non-finite JSON constant is forbidden: {value}")


def _no_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LaneExactPlanDryRunError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def read_strict_json(path: Path | str) -> dict[str, Any]:
    """Read one explicit object, rejecting duplicate keys and non-finite values."""

    artifact_path = Path(path)
    try:
        value = json.loads(
            artifact_path.read_text(encoding="utf-8"),
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except LaneExactPlanDryRunError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise LaneExactPlanDryRunError(
            f"cannot read explicit JSON artifact {artifact_path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise LaneExactPlanDryRunError(f"JSON artifact must be an object: {artifact_path}")
    return value


def _result_hash(payload: Mapping[str, Any]) -> str:
    seed = dict(payload)
    seed.pop("content_hash", None)
    return hashlib.sha256(canonical_json(seed).encode("utf-8")).hexdigest()


def validate_lane_exact_plan_dry_run_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the sealed, non-authoritative wrapper result."""

    if not isinstance(payload, Mapping) or set(payload) != _RESULT_FIELDS:
        raise LaneExactPlanDryRunError("dry-run result fields are invalid")
    if payload["schema_version"] != DRY_RUN_RESULT_SCHEMA:
        raise LaneExactPlanDryRunError("unsupported dry-run result schema")
    if payload["status"] != "ADVISORY_PLAN_VALIDATED":
        raise LaneExactPlanDryRunError("dry-run result status is invalid")
    for field in (
        "execution_authority", "approval_authority", "broker_call_performed",
        "configuration_mutated", "deployment_activated",
    ):
        if payload[field] is not False:
            raise LaneExactPlanDryRunError(f"dry-run result cannot grant or perform {field}")
    for field in ("write_enabled", "write_performed"):
        if type(payload[field]) is not bool:
            raise LaneExactPlanDryRunError(f"{field} must be a literal boolean")
    if payload["write_performed"] and not payload["write_enabled"]:
        raise LaneExactPlanDryRunError("write_performed requires write_enabled")
    if payload["artifact_path"] is not None and not isinstance(payload["artifact_path"], str):
        raise LaneExactPlanDryRunError("artifact_path must be a string or null")
    if payload["write_enabled"] and not payload["artifact_path"]:
        raise LaneExactPlanDryRunError("write-enabled result must bind its artifact path")
    if not payload["write_enabled"] and payload["artifact_path"] is not None:
        raise LaneExactPlanDryRunError("no-write result cannot claim an artifact path")
    plan = payload["plan"]
    failures = validate_lane_exact_execution_plan(plan) if isinstance(plan, Mapping) else ["missing plan"]
    if failures:
        raise LaneExactPlanDryRunError("embedded plan is invalid: " + ",".join(failures))
    bindings = {
        "lane_id": plan["lane_id"],
        "lane_kind": plan["lane_kind"],
        "account_id_hash": plan["account_id_hash"],
        "deployment_version": plan["deployment_version"],
        "plan_hash": plan["content_hash"],
    }
    for field, expected in bindings.items():
        if payload[field] != expected:
            raise LaneExactPlanDryRunError(f"dry-run result differs from plan: {field}")
    expected_sources = {
        "risk_package": plan["risk_package_hash"],
        "broker_snapshot": plan["broker_snapshot_hash"],
        "lane_policy": plan["lane_policy_hash"],
    }
    if payload["source_hashes"] != expected_sources:
        raise LaneExactPlanDryRunError("dry-run source hashes differ from plan")
    if not re.fullmatch(r"[0-9a-f]{64}", str(payload["content_hash"])):
        raise LaneExactPlanDryRunError("dry-run content_hash is invalid")
    if payload["content_hash"] != _result_hash(payload):
        raise LaneExactPlanDryRunError("dry-run content_hash mismatch")
    return dict(payload)


def _safe_part(value: Any, *, label: str) -> str:
    part = str(value or "")
    if not _SAFE_PATH_PART.fullmatch(part) or part in {".", ".."}:
        raise LaneExactPlanDryRunError(f"unsafe {label} for advisory plan path")
    return part


def advisory_exact_plan_path(output_root: Path | str, plan: Mapping[str, Any]) -> Path:
    """Return the only permitted, content-addressed plan location."""

    trade_date = _safe_part(plan.get("trade_date"), label="trade_date")
    lane_id = _safe_part(plan.get("lane_id"), label="lane_id")
    content_hash = str(plan.get("content_hash") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        raise LaneExactPlanDryRunError("plan content_hash is invalid")
    return Path(output_root) / "exact_plans" / trade_date / lane_id / f"{content_hash}.json"


def _persist_immutable_plan(path: Path, plan: Mapping[str, Any]) -> tuple[Path, bool]:
    """Atomically publish exact bytes; an identical retry is a no-op."""

    serialized = serialize_lane_exact_execution_plan(plan).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise LaneExactPlanDryRunError(f"cannot verify existing plan {path}: {exc}") from exc
        if existing != serialized:
            raise LaneExactPlanDryRunError(f"immutable plan path conflict: {path}")
        return path, False

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".plan-", suffix=".tmp", dir=path.parent, delete=False
        ) as handle:
            temporary_name = handle.name
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_name, path)
            created = True
        except FileExistsError:
            created = False
            if path.read_bytes() != serialized:
                raise LaneExactPlanDryRunError(f"immutable plan path conflict: {path}")
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return path, created
    except (OSError, LaneExactPlanError) as exc:
        if isinstance(exc, LaneExactPlanDryRunError):
            raise
        raise LaneExactPlanDryRunError(f"cannot persist advisory exact plan: {exc}") from exc
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def build_lane_exact_plan_dry_run(
    *,
    lane_risk_package: Mapping[str, Any],
    broker_snapshot: Mapping[str, Any],
    governed_lane_policy: Mapping[str, Any],
    planned_at: str,
    write_enabled: bool = False,
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    """Build a fully source-bound plan and optionally persist only that plan."""

    if type(write_enabled) is not bool:
        raise LaneExactPlanDryRunError("write_enabled must be a literal boolean")
    if not isinstance(planned_at, str) or not planned_at.strip():
        raise LaneExactPlanDryRunError("planned_at is required and must be explicit")
    if write_enabled and output_root is None:
        raise LaneExactPlanDryRunError("output_root is required when advisory write is enabled")

    try:
        plan = build_lane_exact_execution_plan(
            lane_risk_package=lane_risk_package,
            broker_snapshot=broker_snapshot,
            governed_lane_policy=governed_lane_policy,
            planned_at=planned_at,
        )
    except LaneExactPlanError as exc:
        raise LaneExactPlanDryRunError(str(exc)) from exc
    failures = validate_lane_exact_execution_plan(
        plan,
        lane_risk_package=lane_risk_package,
        broker_snapshot=broker_snapshot,
        governed_lane_policy=governed_lane_policy,
        as_of=planned_at,
    )
    if failures:
        raise LaneExactPlanDryRunError("built exact plan failed validation: " + ",".join(failures))

    persisted_path: Path | None = None
    write_performed = False
    if write_enabled:
        assert output_root is not None
        intended_path = advisory_exact_plan_path(output_root, plan)
        persisted_path, write_performed = _persist_immutable_plan(intended_path, plan)
        if persisted_path.read_bytes() != serialize_lane_exact_execution_plan(plan).encode("utf-8"):
            raise LaneExactPlanDryRunError("persisted exact plan read-back differs")

    result: dict[str, Any] = {
        "schema_version": DRY_RUN_RESULT_SCHEMA,
        "status": "ADVISORY_PLAN_VALIDATED",
        "execution_authority": False,
        "approval_authority": False,
        "broker_call_performed": False,
        "configuration_mutated": False,
        "deployment_activated": False,
        "write_enabled": write_enabled,
        "write_performed": write_performed,
        "artifact_path": str(persisted_path) if persisted_path is not None else None,
        "lane_id": plan["lane_id"],
        "lane_kind": plan["lane_kind"],
        "account_id_hash": plan["account_id_hash"],
        "deployment_version": plan["deployment_version"],
        "plan_hash": plan["content_hash"],
        "source_hashes": {
            "risk_package": plan["risk_package_hash"],
            "broker_snapshot": plan["broker_snapshot_hash"],
            "lane_policy": plan["lane_policy_hash"],
        },
        "plan": plan,
    }
    result["content_hash"] = _result_hash(result)
    return validate_lane_exact_plan_dry_run_result(result)


__all__ = [
    "DRY_RUN_RESULT_SCHEMA",
    "LaneExactPlanDryRunError",
    "advisory_exact_plan_path",
    "build_lane_exact_plan_dry_run",
    "read_strict_json",
    "validate_lane_exact_plan_dry_run_result",
]
