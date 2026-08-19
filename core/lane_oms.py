"""Advisory lane-neutral OMS and write-ahead-log contracts.

The v2 foundation binds every exact-plan order to lane, deployment, account,
session, allocation, target, plan, policy, and sleeve-decision lineage.  It is
deliberately incapable of broker submission: intent, attempt, and result all
carry literal false submission and execution-authority flags.

This module performs no I/O.  ``merge_lane_oms_records`` provides append-only
idempotency semantics for callers that later add a durable storage adapter.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Sequence

from authority.lane_exact_plan import (
    LANE_EXACT_PLAN_SCHEMA,
    canonical_json,
    validate_lane_exact_execution_plan,
)


LANE_OMS_INTENT_SCHEMA = "caerus.submission_wal_intent.v2"
LANE_OMS_ATTEMPT_SCHEMA = "caerus.oms_attempt.v1"
LANE_OMS_RESULT_SCHEMA = "caerus.oms_result.v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")

_COMMON_LINEAGE_FIELDS = frozenset(
    {
        "trade_date",
        "lane_id",
        "lane_kind",
        "deployment_version",
        "account_id_hash",
        "broker_environment",
        "session_id",
        "session_hash",
        "allocation_id",
        "allocation_hash",
        "target_hash",
        "plan_id",
        "plan_hash",
        "order_id",
        "client_order_id",
    }
)
_INTENT_FIELDS = frozenset(
    {
        "schema_version",
        "intent_id",
        "created_at",
        "status",
        "lifecycle_mode",
        "execution_authority",
        "broker_submission_allowed",
        "broker_call_permitted",
        "symbol",
        "side",
        "quantity",
        "order_type",
        "time_in_force",
        "extended_hours",
        "enforcement_price",
        "notional",
        "sleeve_contributions",
        "policy_hashes",
        "source_hashes",
        "content_hash",
    }
) | _COMMON_LINEAGE_FIELDS
_ATTEMPT_FIELDS = frozenset(
    {
        "schema_version",
        "attempt_id",
        "intent_id",
        "intent_hash",
        "attempted_at",
        "attempt_kind",
        "status",
        "execution_authority",
        "broker_submission_allowed",
        "broker_call_permitted",
        "idempotency_key",
        "reason_codes",
        "content_hash",
    }
) | _COMMON_LINEAGE_FIELDS
_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "result_id",
        "intent_id",
        "intent_hash",
        "attempt_id",
        "attempt_hash",
        "result_at",
        "status",
        "terminal",
        "execution_authority",
        "broker_submission_allowed",
        "broker_call_permitted",
        "broker_order_id",
        "reason_codes",
        "content_hash",
    }
) | _COMMON_LINEAGE_FIELDS


class LaneOmsError(ValueError):
    """Raised when advisory OMS lineage or append-only identity fails."""


def _strict_fields(payload: Mapping[str, Any], expected: frozenset[str], *, label: str) -> None:
    missing = sorted(expected - set(payload))
    unknown = sorted(set(payload) - expected)
    if missing or unknown:
        raise LaneOmsError(f"{label} fields mismatch; missing={missing}, unknown={unknown}")


def _string(value: Any, *, label: str, safe: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LaneOmsError(f"{label} must be a non-blank string without surrounding whitespace")
    if safe and (not _SAFE_ID.fullmatch(value) or ".." in value):
        raise LaneOmsError(f"{label} is invalid")
    return value


def _sha(value: Any, *, label: str) -> str:
    result = _string(value, label=label)
    if not _SHA256.fullmatch(result):
        raise LaneOmsError(f"{label} must be a lowercase SHA-256 digest")
    return result


def _timestamp(value: Any, *, label: str) -> str:
    raw = _string(value, label=label)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LaneOmsError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LaneOmsError(f"{label} must include a timezone")
    return raw


def _finite(value: Any, *, label: str, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise LaneOmsError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise LaneOmsError(f"{label} must be numeric") from exc
    if not (result == result and abs(result) != float("inf")):
        raise LaneOmsError(f"{label} must be finite")
    if positive and result <= 0.0:
        raise LaneOmsError(f"{label} must be positive")
    return result


def lane_oms_content_hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _lineage(plan: Mapping[str, Any], order: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "trade_date": plan["trade_date"],
        "lane_id": plan["lane_id"],
        "lane_kind": plan["lane_kind"],
        "deployment_version": plan["deployment_version"],
        "account_id_hash": plan["account_id_hash"],
        "broker_environment": plan["broker_environment"],
        "session_id": plan["session_id"],
        "session_hash": plan["session_hash"],
        "allocation_id": plan["allocation_id"],
        "allocation_hash": plan["allocation_hash"],
        "target_hash": plan["target_hash"],
        "plan_id": plan["plan_id"],
        "plan_hash": plan["content_hash"],
        "order_id": order["order_id"],
        "client_order_id": order["client_order_id"],
    }


def build_lane_oms_intents(exact_plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build one immutable, submission-disabled WAL intent per exact order."""

    failures = validate_lane_exact_execution_plan(exact_plan)
    if failures:
        raise LaneOmsError("exact plan is invalid: " + ",".join(failures))
    if exact_plan.get("schema_version") != LANE_EXACT_PLAN_SCHEMA:
        raise LaneOmsError("unsupported exact plan schema")
    if exact_plan.get("execution_authority") is not False:
        raise LaneOmsError("advisory OMS requires a non-authoritative exact plan")
    intents: list[dict[str, Any]] = []
    policy_hashes = {
        "lane": exact_plan["lane_policy_hash"],
        "allocator": exact_plan["allocator_policy_hash"],
        "risk": exact_plan["risk_policy_hash"],
        "capital": exact_plan["capital_policy_hash"],
        "execution": exact_plan["execution_policy_hash"],
        "reconciliation": exact_plan["reconciliation_policy_hash"],
    }
    for order in [*exact_plan["sell_orders"], *exact_plan["buy_orders"]]:
        seed = hashlib.sha256(
            canonical_json(
                {
                    "plan_hash": exact_plan["content_hash"],
                    "order_id": order["order_id"],
                    "client_order_id": order["client_order_id"],
                }
            ).encode("utf-8")
        ).hexdigest()
        body = {
            "schema_version": LANE_OMS_INTENT_SCHEMA,
            "intent_id": f"lane-intent:{exact_plan['lane_id']}:{seed[:24]}",
            "created_at": exact_plan["planned_at"],
            "status": "PREPARED",
            "lifecycle_mode": "ADVISORY_ONLY",
            "execution_authority": False,
            "broker_submission_allowed": False,
            "broker_call_permitted": False,
            **_lineage(exact_plan, order),
            "symbol": order["symbol"],
            "side": order["side"],
            "quantity": order["quantity"],
            "order_type": order["order_type"],
            "time_in_force": order["time_in_force"],
            "extended_hours": order["extended_hours"],
            "enforcement_price": order["enforcement_price"],
            "notional": order["notional"],
            "sleeve_contributions": copy.deepcopy(order["sleeve_contributions"]),
            "policy_hashes": policy_hashes,
            "source_hashes": {
                "plan": exact_plan["content_hash"],
                "execution_policy": exact_plan["execution_policy_hash"],
                "reconciliation_policy": exact_plan["reconciliation_policy_hash"],
            },
        }
        body["content_hash"] = lane_oms_content_hash(body)
        intents.append(validate_lane_oms_intent(body))
    return intents


def _validate_common(payload: Mapping[str, Any], *, label: str) -> None:
    for field in (
        "lane_id",
        "deployment_version",
        "session_id",
        "allocation_id",
        "plan_id",
        "order_id",
        "client_order_id",
        "broker_environment",
    ):
        _string(payload[field], label=f"{label}.{field}", safe=True)
    if payload["lane_kind"] not in {"PAPER", "LIVE"}:
        raise LaneOmsError(f"{label}.lane_kind must be PAPER or LIVE")
    for field in ("account_id_hash", "session_hash", "allocation_hash", "target_hash", "plan_hash"):
        _sha(payload[field], label=f"{label}.{field}")
    if payload["execution_authority"] is not False:
        raise LaneOmsError(f"{label} cannot carry execution authority")
    if payload["broker_submission_allowed"] is not False or payload["broker_call_permitted"] is not False:
        raise LaneOmsError(f"{label} must make broker submission impossible")


def validate_lane_oms_intent(
    payload: Mapping[str, Any], *, exact_plan: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneOmsError("OMS intent must be an object")
    _strict_fields(payload, _INTENT_FIELDS, label="OMS intent")
    if payload["schema_version"] != LANE_OMS_INTENT_SCHEMA:
        raise LaneOmsError("unsupported OMS intent schema")
    _validate_common(payload, label="OMS intent")
    _string(payload["intent_id"], label="intent_id", safe=True)
    _timestamp(payload["created_at"], label="created_at")
    if payload["status"] != "PREPARED" or payload["lifecycle_mode"] != "ADVISORY_ONLY":
        raise LaneOmsError("OMS intent must remain PREPARED and ADVISORY_ONLY")
    symbol = _string(payload["symbol"], label="symbol")
    if not _SYMBOL.fullmatch(symbol) or payload["side"] not in {"BUY", "SELL"}:
        raise LaneOmsError("OMS intent symbol or side is invalid")
    quantity = _finite(payload["quantity"], label="quantity", positive=True)
    price = _finite(payload["enforcement_price"], label="enforcement_price", positive=True)
    notional = _finite(payload["notional"], label="notional", positive=True)
    if abs(notional - quantity * price) > 0.01:
        raise LaneOmsError("OMS intent notional mismatch")
    if not isinstance(payload["extended_hours"], bool):
        raise LaneOmsError("extended_hours must be boolean")
    for field in ("order_type", "time_in_force"):
        _string(payload[field], label=field)
    contributions = payload["sleeve_contributions"]
    if not isinstance(contributions, list) or not contributions:
        raise LaneOmsError("OMS intent requires sleeve contributions")
    contribution_quantity = 0.0
    for row in contributions:
        if not isinstance(row, Mapping):
            raise LaneOmsError("OMS sleeve contributions must be objects")
        _string(row.get("sleeve_id"), label="sleeve_id", safe=True)
        _string(row.get("decision_id"), label="decision_id", safe=True)
        _sha(row.get("decision_hash"), label="decision_hash")
        contribution_quantity += _finite(
            row.get("order_quantity"), label="order_quantity", positive=True
        )
    if abs(contribution_quantity - quantity) > 1e-8:
        raise LaneOmsError("OMS contribution quantities do not sum to order")
    policy_hashes = payload["policy_hashes"]
    if not isinstance(policy_hashes, Mapping) or set(policy_hashes) != {
        "lane", "allocator", "risk", "capital", "execution", "reconciliation"
    }:
        raise LaneOmsError("OMS policy hashes are incomplete")
    for value in policy_hashes.values():
        _sha(value, label="policy_hash")
    expected_sources = {
        "plan": payload["plan_hash"],
        "execution_policy": policy_hashes["execution"],
        "reconciliation_policy": policy_hashes["reconciliation"],
    }
    if payload["source_hashes"] != expected_sources:
        raise LaneOmsError("OMS source hash lineage mismatch")
    if _sha(payload["content_hash"], label="content_hash") != lane_oms_content_hash(payload):
        raise LaneOmsError("OMS intent content_hash mismatch")
    if exact_plan is not None:
        failures = validate_lane_exact_execution_plan(exact_plan)
        if failures:
            raise LaneOmsError("exact plan is invalid: " + ",".join(failures))
        plan_order = next(
            (
                row
                for row in [*exact_plan["sell_orders"], *exact_plan["buy_orders"]]
                if row["order_id"] == payload["order_id"]
            ),
            None,
        )
        if plan_order is None:
            raise LaneOmsError("OMS intent order is absent from exact plan")
        expected_lineage = _lineage(exact_plan, plan_order)
        if any(payload[field] != value for field, value in expected_lineage.items()):
            raise LaneOmsError("OMS intent lineage differs from exact plan")
        economic_fields = (
            "symbol", "side", "quantity", "order_type", "time_in_force",
            "extended_hours", "enforcement_price", "notional", "sleeve_contributions",
        )
        if any(payload[field] != plan_order[field] for field in economic_fields):
            raise LaneOmsError("OMS intent economics differ from exact plan")
        expected_policy_hashes = {
            "lane": exact_plan["lane_policy_hash"],
            "allocator": exact_plan["allocator_policy_hash"],
            "risk": exact_plan["risk_policy_hash"],
            "capital": exact_plan["capital_policy_hash"],
            "execution": exact_plan["execution_policy_hash"],
            "reconciliation": exact_plan["reconciliation_policy_hash"],
        }
        if payload["policy_hashes"] != expected_policy_hashes:
            raise LaneOmsError("OMS intent policy hashes differ from exact plan")
    return json.loads(canonical_json(payload))


def build_lane_oms_attempt(intent: Mapping[str, Any]) -> dict[str, Any]:
    """Record the sole permitted attempt: validation with submission disabled."""

    validated = validate_lane_oms_intent(intent)
    key = hashlib.sha256(
        canonical_json({"intent_hash": validated["content_hash"], "mode": "VALIDATION_ONLY"}).encode("utf-8")
    ).hexdigest()
    body = {
        "schema_version": LANE_OMS_ATTEMPT_SCHEMA,
        "attempt_id": f"lane-attempt:{validated['lane_id']}:{key[:24]}",
        "intent_id": validated["intent_id"],
        "intent_hash": validated["content_hash"],
        "attempted_at": validated["created_at"],
        "attempt_kind": "VALIDATION_ONLY",
        "status": "BROKER_SUBMISSION_BLOCKED",
        "execution_authority": False,
        "broker_submission_allowed": False,
        "broker_call_permitted": False,
        "idempotency_key": key,
        "reason_codes": ["ADVISORY_CONTRACT_NO_EXECUTION_AUTHORITY"],
        **{field: validated[field] for field in _COMMON_LINEAGE_FIELDS},
    }
    body["content_hash"] = lane_oms_content_hash(body)
    return validate_lane_oms_attempt(body, intent=validated)


def validate_lane_oms_attempt(
    payload: Mapping[str, Any], *, intent: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneOmsError("OMS attempt must be an object")
    _strict_fields(payload, _ATTEMPT_FIELDS, label="OMS attempt")
    if payload["schema_version"] != LANE_OMS_ATTEMPT_SCHEMA:
        raise LaneOmsError("unsupported OMS attempt schema")
    _validate_common(payload, label="OMS attempt")
    for field in ("attempt_id", "intent_id", "idempotency_key"):
        _string(payload[field], label=field, safe=True)
    _sha(payload["intent_hash"], label="intent_hash")
    _timestamp(payload["attempted_at"], label="attempted_at")
    if (
        payload["attempt_kind"] != "VALIDATION_ONLY"
        or payload["status"] != "BROKER_SUBMISSION_BLOCKED"
        or payload["reason_codes"] != ["ADVISORY_CONTRACT_NO_EXECUTION_AUTHORITY"]
    ):
        raise LaneOmsError("OMS attempt is not the advisory blocked transition")
    if _sha(payload["content_hash"], label="content_hash") != lane_oms_content_hash(payload):
        raise LaneOmsError("OMS attempt content_hash mismatch")
    if intent is not None:
        validated = validate_lane_oms_intent(intent)
        if payload["intent_id"] != validated["intent_id"] or payload["intent_hash"] != validated["content_hash"]:
            raise LaneOmsError("OMS attempt intent lineage mismatch")
        if any(payload[field] != validated[field] for field in _COMMON_LINEAGE_FIELDS):
            raise LaneOmsError("OMS attempt order lineage mismatch")
    return json.loads(canonical_json(payload))


def build_lane_oms_result(
    intent: Mapping[str, Any], attempt: Mapping[str, Any]
) -> dict[str, Any]:
    """Close an advisory validation attempt as terminal and not submitted."""

    validated_intent = validate_lane_oms_intent(intent)
    validated_attempt = validate_lane_oms_attempt(attempt, intent=validated_intent)
    seed = hashlib.sha256(
        canonical_json({"attempt_hash": validated_attempt["content_hash"], "status": "NOT_SUBMITTED"}).encode("utf-8")
    ).hexdigest()
    body = {
        "schema_version": LANE_OMS_RESULT_SCHEMA,
        "result_id": f"lane-result:{validated_intent['lane_id']}:{seed[:24]}",
        "intent_id": validated_intent["intent_id"],
        "intent_hash": validated_intent["content_hash"],
        "attempt_id": validated_attempt["attempt_id"],
        "attempt_hash": validated_attempt["content_hash"],
        "result_at": validated_attempt["attempted_at"],
        "status": "NOT_SUBMITTED",
        "terminal": True,
        "execution_authority": False,
        "broker_submission_allowed": False,
        "broker_call_permitted": False,
        "broker_order_id": None,
        "reason_codes": ["BROKER_SUBMISSION_STRUCTURALLY_DISABLED"],
        **{field: validated_intent[field] for field in _COMMON_LINEAGE_FIELDS},
    }
    body["content_hash"] = lane_oms_content_hash(body)
    return validate_lane_oms_result(body, intent=validated_intent, attempt=validated_attempt)


def validate_lane_oms_result(
    payload: Mapping[str, Any], *, intent: Mapping[str, Any] | None = None,
    attempt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneOmsError("OMS result must be an object")
    _strict_fields(payload, _RESULT_FIELDS, label="OMS result")
    if payload["schema_version"] != LANE_OMS_RESULT_SCHEMA:
        raise LaneOmsError("unsupported OMS result schema")
    _validate_common(payload, label="OMS result")
    for field in ("result_id", "intent_id", "attempt_id"):
        _string(payload[field], label=field, safe=True)
    _sha(payload["intent_hash"], label="intent_hash")
    _sha(payload["attempt_hash"], label="attempt_hash")
    _timestamp(payload["result_at"], label="result_at")
    if (
        payload["status"] != "NOT_SUBMITTED"
        or payload["terminal"] is not True
        or payload["broker_order_id"] is not None
        or payload["reason_codes"] != ["BROKER_SUBMISSION_STRUCTURALLY_DISABLED"]
    ):
        raise LaneOmsError("OMS result must terminate as structurally NOT_SUBMITTED")
    if _sha(payload["content_hash"], label="content_hash") != lane_oms_content_hash(payload):
        raise LaneOmsError("OMS result content_hash mismatch")
    if intent is not None:
        validated_intent = validate_lane_oms_intent(intent)
        if payload["intent_id"] != validated_intent["intent_id"] or payload["intent_hash"] != validated_intent["content_hash"]:
            raise LaneOmsError("OMS result intent lineage mismatch")
        if any(payload[field] != validated_intent[field] for field in _COMMON_LINEAGE_FIELDS):
            raise LaneOmsError("OMS result order lineage mismatch")
    if attempt is not None:
        validated_attempt = validate_lane_oms_attempt(attempt, intent=intent)
        if payload["attempt_id"] != validated_attempt["attempt_id"] or payload["attempt_hash"] != validated_attempt["content_hash"]:
            raise LaneOmsError("OMS result attempt lineage mismatch")
    return json.loads(canonical_json(payload))


def merge_lane_oms_records(
    existing: Iterable[Mapping[str, Any]], additions: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Apply append-only idempotency: exact replay is a no-op, conflicts fail."""

    validators = {
        LANE_OMS_INTENT_SCHEMA: ("intent_id", validate_lane_oms_intent),
        LANE_OMS_ATTEMPT_SCHEMA: ("attempt_id", validate_lane_oms_attempt),
        LANE_OMS_RESULT_SCHEMA: ("result_id", validate_lane_oms_result),
    }
    result: list[dict[str, Any]] = []
    by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in [*existing, *additions]:
        if not isinstance(raw, Mapping) or raw.get("schema_version") not in validators:
            raise LaneOmsError("unsupported OMS WAL record")
        identity_field, validator = validators[str(raw["schema_version"])]
        row = validator(raw)
        identity = (row["schema_version"], row[identity_field])
        prior = by_identity.get(identity)
        if prior is not None:
            if prior["content_hash"] != row["content_hash"]:
                raise LaneOmsError(f"OMS WAL identity conflict: {identity[1]}")
            continue
        by_identity[identity] = row
        result.append(row)
    return result


def validate_lane_oms_lifecycle(
    intents: Sequence[Mapping[str, Any]], attempts: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]], *, exact_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one complete advisory lifecycle per intent."""

    intent_rows = [
        validate_lane_oms_intent(row, exact_plan=exact_plan) for row in intents
    ]
    if len({row["intent_id"] for row in intent_rows}) != len(intent_rows):
        raise LaneOmsError("duplicate OMS intent identity")
    attempt_by_intent: dict[str, dict[str, Any]] = {}
    for raw in attempts:
        intent_id = str(raw.get("intent_id") or "")
        intent = next((row for row in intent_rows if row["intent_id"] == intent_id), None)
        if intent is None or intent_id in attempt_by_intent:
            raise LaneOmsError("OMS attempt coverage is missing or duplicated")
        attempt_by_intent[intent_id] = validate_lane_oms_attempt(raw, intent=intent)
    result_by_intent: dict[str, dict[str, Any]] = {}
    for raw in results:
        intent_id = str(raw.get("intent_id") or "")
        intent = next((row for row in intent_rows if row["intent_id"] == intent_id), None)
        attempt = attempt_by_intent.get(intent_id)
        if intent is None or attempt is None or intent_id in result_by_intent:
            raise LaneOmsError("OMS result coverage is missing or duplicated")
        result_by_intent[intent_id] = validate_lane_oms_result(raw, intent=intent, attempt=attempt)
    expected = {row["intent_id"] for row in intent_rows}
    if set(attempt_by_intent) != expected or set(result_by_intent) != expected:
        raise LaneOmsError("every OMS intent requires one blocked attempt and terminal result")
    return {
        "status": "PASS",
        "intent_count": len(intent_rows),
        "attempt_count": len(attempt_by_intent),
        "result_count": len(result_by_intent),
        "broker_submission_possible": False,
    }


__all__ = [
    "LANE_OMS_ATTEMPT_SCHEMA",
    "LANE_OMS_INTENT_SCHEMA",
    "LANE_OMS_RESULT_SCHEMA",
    "LaneOmsError",
    "build_lane_oms_attempt",
    "build_lane_oms_intents",
    "build_lane_oms_result",
    "lane_oms_content_hash",
    "merge_lane_oms_records",
    "validate_lane_oms_attempt",
    "validate_lane_oms_intent",
    "validate_lane_oms_lifecycle",
    "validate_lane_oms_result",
]
