"""Fail-closed rehearsal for the requested Monday Lyra Live session."""

from __future__ import annotations

import copy
import hashlib
from typing import Any, Mapping

from authority.lane_exact_plan import canonical_json
from core.generic_live_dynamic_owner_decision import (
    validate_generic_live_dynamic_owner_decision,
)
from core.governed_xnys_calendar import next_xnys_session, previous_xnys_session


SCHEMA = "caerus.generic_live_monday_rehearsal.v1"
_FIELDS = frozenset(
    {
        "schema_version", "rehearsed_at", "requested_effective_session",
        "completed_data_session_at_monday_precompute", "first_governed_signal_session",
        "first_executable_session_under_canonical_lyra_economics",
        "owner_decision_hash", "universe_freeze_hash", "status", "reason_codes",
        "same_session_lyra_v2_available", "exact_v4_plan_available", "order_count",
        "broker_call_performed", "broker_write_performed", "submission_allowed",
        "schedule_enable_allowed", "generic_kill_switch_required_state",
        "legacy_kill_switch_required_state", "paper_authority_changed",
        "execution_authority", "activation_authority", "content_hash",
    }
)


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def build_generic_live_monday_rehearsal(
    *, owner_decision: Mapping[str, Any], universe_freeze_hash: str,
    rehearsed_at: str,
) -> dict[str, Any]:
    owner = validate_generic_live_dynamic_owner_decision(owner_decision)
    requested = owner["effective_session"]
    requested_day = __import__("datetime").date.fromisoformat(requested)
    if requested_day.weekday() != 0:
        raise ValueError("Monday rehearsal requires a Monday effective session")
    completed_data_session = previous_xnys_session(requested)
    first_governed_signal_session = requested
    first_executable_session = next_xnys_session(first_governed_signal_session)
    reasons = [
        "MONDAY_PRECOMPUTE_PRECEDES_MONDAY_COMPLETED_CLOSE",
        "MONDAY_SESSION_HAS_NO_POST_FREEZE_WEEKLY_LYRA_TARGET",
        "SAME_SESSION_READY_LYRA_V2_UNAVAILABLE_WITHOUT_LOOKAHEAD",
        "EXACT_LYRA_ONLY_V4_PLAN_UNAVAILABLE",
        "NO_ORDER_PERMITTED",
    ]
    body = {
        "schema_version": SCHEMA,
        "rehearsed_at": rehearsed_at,
        "requested_effective_session": requested,
        "completed_data_session_at_monday_precompute": completed_data_session,
        "first_governed_signal_session": first_governed_signal_session,
        "first_executable_session_under_canonical_lyra_economics": first_executable_session,
        "owner_decision_hash": owner["content_hash"],
        "universe_freeze_hash": universe_freeze_hash,
        "status": "BLOCKED_NO_TRADE_REARMED",
        "reason_codes": reasons,
        "same_session_lyra_v2_available": False,
        "exact_v4_plan_available": False,
        "order_count": 0,
        "broker_call_performed": False,
        "broker_write_performed": False,
        "submission_allowed": False,
        "schedule_enable_allowed": False,
        "generic_kill_switch_required_state": "ARMED",
        "legacy_kill_switch_required_state": "ARMED",
        "paper_authority_changed": False,
        "execution_authority": False,
        "activation_authority": False,
    }
    body["content_hash"] = _hash(body)
    return validate_generic_live_monday_rehearsal(body)


def validate_generic_live_monday_rehearsal(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _FIELDS or payload.get("schema_version") != SCHEMA:
        raise ValueError("Monday rehearsal fields are invalid")
    if payload.get("status") != "BLOCKED_NO_TRADE_REARMED":
        raise ValueError("Monday rehearsal must remain blocked")
    requested = str(payload.get("requested_effective_session"))
    if payload.get("completed_data_session_at_monday_precompute") != previous_xnys_session(requested):
        raise ValueError("Monday completed-data session is invalid")
    if payload.get("first_governed_signal_session") != requested:
        raise ValueError("Monday signal session is invalid")
    if payload.get("first_executable_session_under_canonical_lyra_economics") != next_xnys_session(requested):
        raise ValueError("next executable session is invalid")
    if payload.get("order_count") != 0:
        raise ValueError("Monday rehearsal cannot contain an order")
    for field in (
        "same_session_lyra_v2_available", "exact_v4_plan_available",
        "broker_call_performed", "broker_write_performed", "submission_allowed",
        "schedule_enable_allowed", "paper_authority_changed", "execution_authority",
        "activation_authority",
    ):
        if payload.get(field) is not False:
            raise ValueError(f"Monday rehearsal safety flag differs: {field}")
    if payload.get("generic_kill_switch_required_state") != "ARMED" or payload.get("legacy_kill_switch_required_state") != "ARMED":
        raise ValueError("both kill gates must remain armed")
    if payload.get("content_hash") != _hash(payload):
        raise ValueError("Monday rehearsal content hash differs")
    return copy.deepcopy(dict(payload))


__all__ = ["SCHEMA", "build_generic_live_monday_rehearsal", "validate_generic_live_monday_rehearsal"]
