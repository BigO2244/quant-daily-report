"""Durable append-only store for advisory lane OMS/WAL records.

The store persists only the validated intent/attempt/result contracts from
``core.lane_oms``.  It owns no broker adapter, runtime configuration, or order
submission capability.  Every mutation requires ``write=True`` explicitly;
all persisted records independently prove that broker submission and execution
authority are false.

The JSONL file is locked across read, validation, idempotency comparison, and
append.  Exact identity replay is a no-op.  Reusing an immutable identity for
different content, an out-of-order lifecycle transition, malformed JSON,
duplicate object keys, or non-finite data fails closed.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from core.lane_oms import (
    LANE_OMS_ATTEMPT_SCHEMA,
    LANE_OMS_INTENT_SCHEMA,
    LANE_OMS_RESULT_SCHEMA,
    LaneOmsError,
    validate_lane_oms_attempt,
    validate_lane_oms_intent,
    validate_lane_oms_lifecycle,
    validate_lane_oms_result,
)
from authority.lane_exact_plan import canonical_json, validate_lane_exact_execution_plan


_SCHEMA_IDENTITY = {
    LANE_OMS_INTENT_SCHEMA: "intent_id",
    LANE_OMS_ATTEMPT_SCHEMA: "attempt_id",
    LANE_OMS_RESULT_SCHEMA: "result_id",
}


class LaneOmsStoreError(RuntimeError):
    """Raised when durable OMS/WAL evidence cannot be safely read or appended."""


def _reject_json_constant(value: str) -> None:
    raise LaneOmsStoreError(f"non-finite JSON constant is forbidden: {value}")


def _object_without_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LaneOmsStoreError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _decode_line(line: str, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            line,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except (json.JSONDecodeError, LaneOmsStoreError) as exc:
        raise LaneOmsStoreError(f"cannot decode {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise LaneOmsStoreError(f"{label} must contain a JSON object")
    return payload


def _read_handle(handle: Any, *, label: str) -> list[dict[str, Any]]:
    handle.seek(0)
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(handle.read().splitlines(), 1):
        if not line.strip():
            continue
        rows.append(_decode_line(line, label=f"{label}:{line_number}"))
    return rows


def _validate_record(
    raw: Mapping[str, Any], *, exact_plan: Mapping[str, Any] | None
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise LaneOmsStoreError("OMS WAL record must be an object")
    schema = raw.get("schema_version")
    try:
        if schema == LANE_OMS_INTENT_SCHEMA:
            return validate_lane_oms_intent(raw, exact_plan=exact_plan)
        if schema == LANE_OMS_ATTEMPT_SCHEMA:
            return validate_lane_oms_attempt(raw)
        if schema == LANE_OMS_RESULT_SCHEMA:
            return validate_lane_oms_result(raw)
    except LaneOmsError as exc:
        raise LaneOmsStoreError(f"OMS WAL record is invalid: {exc}") from exc
    raise LaneOmsStoreError(f"unsupported OMS WAL schema: {schema!r}")


def validate_lane_oms_store(
    records: Iterable[Mapping[str, Any]],
    *,
    exact_plan: Mapping[str, Any] | None = None,
    require_complete_lifecycle: bool = False,
) -> list[dict[str, Any]]:
    """Validate append order, immutable identities, and causal lifecycle links."""

    if exact_plan is not None:
        failures = validate_lane_exact_execution_plan(exact_plan)
        if failures:
            raise LaneOmsStoreError("exact plan is invalid: " + ",".join(failures))

    result: list[dict[str, Any]] = []
    by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    intents: dict[str, dict[str, Any]] = {}
    attempts: dict[str, dict[str, Any]] = {}
    attempts_by_intent: dict[str, str] = {}
    results: dict[str, dict[str, Any]] = {}
    results_by_intent: dict[str, str] = {}

    for raw in records:
        row = _validate_record(raw, exact_plan=exact_plan)
        schema = row["schema_version"]
        identity_field = _SCHEMA_IDENTITY[schema]
        identity = (schema, row[identity_field])
        prior = by_identity.get(identity)
        if prior is not None:
            if prior["content_hash"] != row["content_hash"]:
                raise LaneOmsStoreError(
                    f"OMS WAL identity is bound to conflicting content: {identity[1]}"
                )
            continue

        try:
            if schema == LANE_OMS_INTENT_SCHEMA:
                intent_id = row["intent_id"]
                if intent_id in intents:
                    raise LaneOmsStoreError(f"duplicate OMS intent: {intent_id}")
                intents[intent_id] = row
            elif schema == LANE_OMS_ATTEMPT_SCHEMA:
                intent_id = row["intent_id"]
                intent = intents.get(intent_id)
                if intent is None:
                    raise LaneOmsStoreError(
                        f"OMS attempt precedes or lacks its intent: {row['attempt_id']}"
                    )
                if intent_id in attempts_by_intent:
                    raise LaneOmsStoreError(
                        f"OMS intent has multiple attempts: {intent_id}"
                    )
                row = validate_lane_oms_attempt(row, intent=intent)
                attempts[row["attempt_id"]] = row
                attempts_by_intent[intent_id] = row["attempt_id"]
            else:
                intent_id = row["intent_id"]
                intent = intents.get(intent_id)
                attempt = attempts.get(row["attempt_id"])
                if intent is None or attempt is None:
                    raise LaneOmsStoreError(
                        f"OMS result precedes or lacks intent/attempt: {row['result_id']}"
                    )
                if intent_id in results_by_intent:
                    raise LaneOmsStoreError(
                        f"OMS intent has multiple results: {intent_id}"
                    )
                row = validate_lane_oms_result(row, intent=intent, attempt=attempt)
                results[row["result_id"]] = row
                results_by_intent[intent_id] = row["result_id"]
        except LaneOmsError as exc:
            raise LaneOmsStoreError(f"OMS lifecycle lineage is invalid: {exc}") from exc

        by_identity[identity] = row
        result.append(row)

    if require_complete_lifecycle:
        if not intents:
            raise LaneOmsStoreError("OMS lifecycle is incomplete: no intents")
        try:
            validate_lane_oms_lifecycle(
                list(intents.values()),
                list(attempts.values()),
                list(results.values()),
                exact_plan=exact_plan,
            )
        except LaneOmsError as exc:
            raise LaneOmsStoreError(f"OMS lifecycle is incomplete: {exc}") from exc
        if exact_plan is not None:
            plan_order_ids = {
                row["order_id"]
                for row in [*exact_plan["sell_orders"], *exact_plan["buy_orders"]]
            }
            intent_order_ids = {row["order_id"] for row in intents.values()}
            if intent_order_ids != plan_order_ids:
                raise LaneOmsStoreError(
                    "complete OMS lifecycle does not cover every exact-plan order"
                )
    return result


def read_lane_oms_store(
    path: Path | str,
    *,
    exact_plan: Mapping[str, Any] | None = None,
    require_complete_lifecycle: bool = False,
) -> list[dict[str, Any]]:
    """Read, recover, deduplicate, and validate one advisory OMS/WAL file."""

    store_path = Path(path)
    if not store_path.exists():
        return validate_lane_oms_store(
            [],
            exact_plan=exact_plan,
            require_complete_lifecycle=require_complete_lifecycle,
        )
    try:
        with store_path.open("r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                rows = _read_handle(handle, label=str(store_path))
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise LaneOmsStoreError(f"cannot read OMS WAL store {store_path}: {exc}") from exc
    return validate_lane_oms_store(
        rows,
        exact_plan=exact_plan,
        require_complete_lifecycle=require_complete_lifecycle,
    )


def append_lane_oms_store(
    path: Path | str,
    records: Iterable[Mapping[str, Any]],
    *,
    write: bool = False,
    exact_plan: Mapping[str, Any] | None = None,
    require_complete_lifecycle: bool = False,
) -> int:
    """Append only new identities and return the number durably written.

    ``write=True`` is mandatory.  This explicit capability flag prevents a
    read/validation call from being silently widened into filesystem mutation.
    """

    if write is not True:
        raise LaneOmsStoreError("OMS WAL persistence requires explicit write=True")
    candidates = list(records)
    # Validate the proposed sequence itself before creating directories/files.
    # A partial batch may rely on already-persisted lineage, so standalone
    # record validation is used here; the locked combined validation below is
    # authoritative.
    for raw in candidates:
        _validate_record(raw, exact_plan=exact_plan)
    if not candidates:
        if require_complete_lifecycle:
            read_lane_oms_store(
                path,
                exact_plan=exact_plan,
                require_complete_lifecycle=True,
            )
        return 0

    store_path = Path(path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with store_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                existing_raw = _read_handle(handle, label=str(store_path))
                existing = validate_lane_oms_store(
                    existing_raw,
                    exact_plan=exact_plan,
                    require_complete_lifecycle=False,
                )
                combined = validate_lane_oms_store(
                    [*existing, *candidates],
                    exact_plan=exact_plan,
                    require_complete_lifecycle=require_complete_lifecycle,
                )
                existing_ids = {
                    (row["schema_version"], row[_SCHEMA_IDENTITY[row["schema_version"]]])
                    for row in existing
                }
                additions = [
                    row
                    for row in combined
                    if (
                        row["schema_version"],
                        row[_SCHEMA_IDENTITY[row["schema_version"]]],
                    )
                    not in existing_ids
                ]
                if additions:
                    handle.seek(0, os.SEEK_END)
                    serialized = "".join(canonical_json(row) + "\n" for row in additions)
                    handle.write(serialized)
                    handle.flush()
                    os.fsync(handle.fileno())
                return len(additions)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                _fsync_directory(store_path.parent)
    except OSError as exc:
        raise LaneOmsStoreError(f"cannot append OMS WAL store {store_path}: {exc}") from exc


def _fsync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


__all__ = [
    "LaneOmsStoreError",
    "append_lane_oms_store",
    "read_lane_oms_store",
    "validate_lane_oms_store",
]
