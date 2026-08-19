"""Canonical advisory lifecycle recommendation contract.

Lifecycle recommendations are evidence packages, never execution authority.
They may propose an exact deployment-policy patch, but only a separately
validated owner decision may authorize that patch.  This module deliberately
has no dependency on the strategy registry, runtime, or broker integrations.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


LIFECYCLE_RECOMMENDATION_SCHEMA = "caerus.lifecycle_recommendation.v1"
LIFECYCLE_ACTIONS = frozenset({"PROMOTE", "RETAIN", "DEPRECATE", "HOLD"})

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "recommendation_id",
        "generated_at",
        "expires_at",
        "action",
        "sleeve_id",
        "current_stage",
        "proposed_stage",
        "source_lane",
        "destination_lane",
        "proposed_capital_change",
        "evidence_refs",
        "evidence_hashes",
        "gate_results",
        "confidence",
        "reason_codes",
        "proposed_policy_patch",
        "requires_owner_approval",
        "execution_authority",
        "content_hash",
    }
)
_PATCH_OPERATIONS = frozenset({"add", "remove", "replace", "test"})


class LifecycleRecommendationError(ValueError):
    """Raised when a lifecycle recommendation cannot be trusted."""


def canonical_json(payload: Any) -> str:
    """Return the one canonical JSON representation used for content hashes."""

    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LifecycleRecommendationError(
            f"lifecycle recommendation is not canonical JSON: {exc}"
        ) from exc


def recommendation_content_hash(payload: Mapping[str, Any]) -> str:
    """Hash a recommendation body, excluding its self-referential hash field."""

    body = dict(payload)
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _strict_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LifecycleRecommendationError(
            f"{label} must be a non-blank string without surrounding whitespace"
        )
    return value


def _safe_id(value: Any, *, label: str) -> str:
    normalized = _strict_string(value, label=label)
    if not _SAFE_ID.fullmatch(normalized) or ".." in normalized:
        raise LifecycleRecommendationError(f"{label} is invalid: {value!r}")
    return normalized


def _timestamp(value: Any, *, label: str) -> dt.datetime:
    raw = _strict_string(value, label=label)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LifecycleRecommendationError(
            f"{label} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LifecycleRecommendationError(f"{label} must include a timezone")
    return parsed


def _as_datetime(value: str | dt.datetime, *, label: str) -> dt.datetime:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise LifecycleRecommendationError(f"{label} must include a timezone")
        return value
    return _timestamp(value, label=label)


def _strict_string_list(
    value: Any,
    *,
    label: str,
    allow_empty: bool,
) -> list[str]:
    if not isinstance(value, list):
        raise LifecycleRecommendationError(f"{label} must be an array")
    if not allow_empty and not value:
        raise LifecycleRecommendationError(f"{label} must not be empty")
    result = [_strict_string(item, label=f"{label} item") for item in value]
    if len(set(result)) != len(result):
        raise LifecycleRecommendationError(f"{label} must not contain duplicates")
    return result


def _validate_json_pointer(value: Any, *, label: str) -> str:
    pointer = _strict_string(value, label=label)
    if not pointer.startswith("/"):
        raise LifecycleRecommendationError(
            f"{label} must be a non-root JSON Pointer"
        )
    index = 0
    while index < len(pointer):
        if pointer[index] == "~":
            if index + 1 >= len(pointer) or pointer[index + 1] not in {"0", "1"}:
                raise LifecycleRecommendationError(
                    f"{label} contains an invalid JSON Pointer escape"
                )
            index += 2
        else:
            index += 1
    return pointer


def _validate_policy_patch(value: Any) -> None:
    if not isinstance(value, list):
        raise LifecycleRecommendationError(
            "proposed_policy_patch must be an RFC 6902-style operation array"
        )
    for index, operation in enumerate(value):
        label = f"proposed_policy_patch[{index}]"
        if not isinstance(operation, Mapping):
            raise LifecycleRecommendationError(f"{label} must be an object")
        op = _strict_string(operation.get("op"), label=f"{label}.op")
        if op not in _PATCH_OPERATIONS:
            raise LifecycleRecommendationError(
                f"{label}.op must be one of {sorted(_PATCH_OPERATIONS)}"
            )
        required = {"op", "path"}
        if op in {"add", "replace", "test"}:
            required.add("value")
        if set(operation) != required:
            raise LifecycleRecommendationError(
                f"{label} fields must be exactly {sorted(required)}"
            )
        _validate_json_pointer(operation.get("path"), label=f"{label}.path")
        if "value" in operation:
            canonical_json(operation["value"])


def validate_lifecycle_recommendation(
    payload: Mapping[str, Any],
    *,
    as_of: str | dt.datetime | None = None,
) -> dict[str, Any]:
    """Validate and return a detached recommendation object.

    ``as_of`` is optional so historical recommendations remain readable after
    expiry.  When supplied, a recommendation expiring at or before ``as_of`` is
    rejected as no longer actionable.
    """

    if not isinstance(payload, Mapping):
        raise LifecycleRecommendationError("lifecycle recommendation must be an object")
    if set(payload) != _REQUIRED_FIELDS:
        missing = sorted(_REQUIRED_FIELDS - set(payload))
        unknown = sorted(set(payload) - _REQUIRED_FIELDS)
        raise LifecycleRecommendationError(
            f"lifecycle recommendation fields mismatch; missing={missing}, unknown={unknown}"
        )
    if payload.get("schema_version") != LIFECYCLE_RECOMMENDATION_SCHEMA:
        raise LifecycleRecommendationError("unsupported lifecycle recommendation schema")

    _safe_id(payload.get("recommendation_id"), label="recommendation_id")
    _safe_id(payload.get("sleeve_id"), label="sleeve_id")
    generated_at = _timestamp(payload.get("generated_at"), label="generated_at")
    expires_at = _timestamp(payload.get("expires_at"), label="expires_at")
    if expires_at <= generated_at:
        raise LifecycleRecommendationError("expires_at must be after generated_at")
    if as_of is not None and expires_at <= _as_datetime(as_of, label="as_of"):
        raise LifecycleRecommendationError("lifecycle recommendation is expired")

    action = _strict_string(payload.get("action"), label="action")
    if action not in LIFECYCLE_ACTIONS:
        raise LifecycleRecommendationError(
            f"action must be one of {sorted(LIFECYCLE_ACTIONS)}"
        )
    for label in (
        "current_stage",
        "proposed_stage",
        "source_lane",
        "destination_lane",
    ):
        _strict_string(payload.get(label), label=label)

    capital_change = payload.get("proposed_capital_change")
    if not isinstance(capital_change, Mapping):
        raise LifecycleRecommendationError("proposed_capital_change must be an object")
    canonical_json(capital_change)

    evidence_refs = _strict_string_list(
        payload.get("evidence_refs"), label="evidence_refs", allow_empty=False
    )
    evidence_hashes = _strict_string_list(
        payload.get("evidence_hashes"), label="evidence_hashes", allow_empty=False
    )
    if len(evidence_refs) != len(evidence_hashes):
        raise LifecycleRecommendationError(
            "evidence_refs and evidence_hashes must have one-to-one cardinality"
        )
    if any(not _SHA256.fullmatch(value) for value in evidence_hashes):
        raise LifecycleRecommendationError(
            "every evidence_hashes item must be a lowercase SHA-256 digest"
        )

    gate_results = payload.get("gate_results")
    if not isinstance(gate_results, Mapping) or not gate_results:
        raise LifecycleRecommendationError("gate_results must be a non-empty object")
    if any(not isinstance(key, str) or not key.strip() for key in gate_results):
        raise LifecycleRecommendationError("gate_results keys must be non-blank strings")
    canonical_json(gate_results)

    confidence = payload.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise LifecycleRecommendationError("confidence must be numeric")
    confidence_number = float(confidence)
    if not math.isfinite(confidence_number) or not 0.0 <= confidence_number <= 1.0:
        raise LifecycleRecommendationError("confidence must be within [0, 1]")

    _strict_string_list(
        payload.get("reason_codes"), label="reason_codes", allow_empty=False
    )
    _validate_policy_patch(payload.get("proposed_policy_patch"))

    if payload.get("requires_owner_approval") is not True:
        raise LifecycleRecommendationError("requires_owner_approval must be true")
    if payload.get("execution_authority") is not False:
        raise LifecycleRecommendationError("execution_authority must be false")

    declared_hash = payload.get("content_hash")
    if not isinstance(declared_hash, str) or not _SHA256.fullmatch(declared_hash):
        raise LifecycleRecommendationError("content_hash must be a lowercase SHA-256 digest")
    if declared_hash != recommendation_content_hash(payload):
        raise LifecycleRecommendationError("lifecycle recommendation content hash mismatch")

    # Round-trip through JSON to detach nested mutable containers from callers.
    return json.loads(canonical_json(payload))


def build_lifecycle_recommendation(
    *,
    recommendation_id: str,
    generated_at: str,
    expires_at: str,
    action: str,
    sleeve_id: str,
    current_stage: str,
    proposed_stage: str,
    source_lane: str,
    destination_lane: str,
    proposed_capital_change: Mapping[str, Any],
    evidence_refs: Sequence[str],
    evidence_hashes: Sequence[str],
    gate_results: Mapping[str, Any],
    confidence: float,
    reason_codes: Sequence[str],
    proposed_policy_patch: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a validated advisory recommendation with no execution authority."""

    body: dict[str, Any] = {
        "schema_version": LIFECYCLE_RECOMMENDATION_SCHEMA,
        "recommendation_id": recommendation_id,
        "generated_at": generated_at,
        "expires_at": expires_at,
        "action": action,
        "sleeve_id": sleeve_id,
        "current_stage": current_stage,
        "proposed_stage": proposed_stage,
        "source_lane": source_lane,
        "destination_lane": destination_lane,
        "proposed_capital_change": dict(proposed_capital_change),
        "evidence_refs": list(evidence_refs),
        "evidence_hashes": list(evidence_hashes),
        "gate_results": dict(gate_results),
        "confidence": confidence,
        "reason_codes": list(reason_codes),
        "proposed_policy_patch": (
            [dict(row) for row in proposed_policy_patch]
            if isinstance(proposed_policy_patch, (list, tuple))
            and all(isinstance(row, Mapping) for row in proposed_policy_patch)
            else proposed_policy_patch
        ),
        "requires_owner_approval": True,
        "execution_authority": False,
    }
    body["content_hash"] = recommendation_content_hash(body)
    return validate_lifecycle_recommendation(body)


def serialize_lifecycle_recommendation(payload: Mapping[str, Any]) -> str:
    """Validate and serialize a recommendation as canonical JSON plus newline."""

    validated = validate_lifecycle_recommendation(payload)
    return canonical_json(validated) + "\n"


def read_lifecycle_recommendation(
    path: Path | str,
    *,
    as_of: str | dt.datetime | None = None,
) -> dict[str, Any]:
    """Read and validate one recommendation artifact."""

    artifact_path = Path(path)
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleRecommendationError(
            f"cannot read lifecycle recommendation {artifact_path}: {exc}"
        ) from exc
    return validate_lifecycle_recommendation(payload, as_of=as_of)


def write_lifecycle_recommendation(
    path: Path | str,
    payload: Mapping[str, Any],
) -> Path:
    """Persist one immutable recommendation, refusing to overwrite evidence."""

    artifact_path = Path(path)
    serialized = serialize_lifecycle_recommendation(payload)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with artifact_path.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise LifecycleRecommendationError(
            f"lifecycle recommendation already exists: {artifact_path}"
        ) from exc
    directory_fd = os.open(str(artifact_path.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return artifact_path


__all__ = [
    "LIFECYCLE_ACTIONS",
    "LIFECYCLE_RECOMMENDATION_SCHEMA",
    "LifecycleRecommendationError",
    "build_lifecycle_recommendation",
    "canonical_json",
    "read_lifecycle_recommendation",
    "recommendation_content_hash",
    "serialize_lifecycle_recommendation",
    "validate_lifecycle_recommendation",
    "write_lifecycle_recommendation",
]
