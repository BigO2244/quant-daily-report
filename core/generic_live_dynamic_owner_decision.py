"""Strict owner authority for dynamic-balance generic Live v1.

This record supersedes the earlier fixed-dollar candidate.  It grants policy
authority only: execution remains impossible without a fresh, session-bound
decision, plan, broker snapshot, capital proof, and all operational gates.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import re
from typing import Any, Mapping


SCHEMA = "caerus.generic_live_dynamic_owner_decision.v1"
CAPITAL_POLICY_VERSION = "broker_net_liquidation_and_settled_cash_v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_FIELDS = frozenset(
    {
        "schema_version", "owner_decision_id", "owner", "decision",
        "decided_at", "effective_session", "expires_at", "lane_id",
        "lane_kind", "eligible_sleeve_ids", "adapter_contract",
        "capital_policy", "trading_constraints", "preflight_requirements",
        "automatic_rearm_and_rollback_triggers",
        "supersedes_fixed_capital_artifact_hashes", "paper_authority_changed",
        "legacy_live_executor_allowed", "execution_authority",
        "activation_authority", "content_hash",
    }
)
_CAPITAL_FIELDS = frozenset(
    {
        "policy_version", "nominal_capital_ceiling_usd",
        "gross_capital_basis", "buy_cash_basis", "maximum_gross_fraction",
        "minimum_cash_reserve_fraction", "buying_power_allowed",
        "margin_multiplier_allowed", "borrowing_allowed",
        "unsettled_funds_allowed", "unverified_pending_funds_allowed",
        "deposit_treatment", "withdrawal_or_equity_decline_treatment",
        "freshness_seconds",
    }
)
_TRADING_FIELDS = frozenset(
    {
        "minimum_trade_usd", "maximum_orders_per_session",
        "whole_share_only", "long_only", "leverage_allowed",
        "shorting_allowed", "opportunistic_test_orders_allowed",
        "no_trade_when_plan_has_no_order",
    }
)
_PREFLIGHT = frozenset(
    {
        "ACCOUNT_PIN_MATCH", "ACCOUNTING_GREEN",
        "APPROVED_LYRA_V2_DECISION", "APPROVED_LYRA_ONLY_V4_PLAN",
        "ASSET_EVIDENCE_FRESH_LT_120_SECONDS", "BROKER_ACCOUNT_EVIDENCE_FRESH_LT_120_SECONDS",
        "BROKER_OPEN_ORDERS_EVIDENCE_FRESH_LT_120_SECONDS",
        "BROKER_POSITIONS_EVIDENCE_FRESH_LT_120_SECONDS",
        "DYNAMIC_GROSS_AND_CASH_PROOF_GREEN", "GENERIC_ADAPTER_ONLY",
        "NO_OPEN_ORDERS", "ORDER_LIFECYCLE_GREEN", "RECONCILIATION_GREEN",
        "REPORTING_GREEN", "ROLLBACK_REARM_PROVEN", "SETTLED_CASH_PROVEN",
    }
)
_ROLLBACK = frozenset(
    {
        "PREFLIGHT_BREAK", "SUBMISSION_BREAK", "ORDER_BREAK",
        "RECONCILIATION_BREAK", "ACCOUNTING_BREAK", "REPORTING_BREAK",
    }
)
EXPECTED_FIXED_CAPITAL_ARTIFACT_HASHES = frozenset(
    {
        "4b697447a9f5760c10df7ba0e65cbef2ab627feb177a3ec4f1437ace57b8068b",
        "5ebc0a035c6436509646200b5f69ee26e515c44280071832a3a0b0aa272dd0e7",
        "8a07df4852a30358761e2be62054cf202b406bb4ca096c70be55e3b1e60cd641",
        "bd056ac57670f31016adea140d9ed8951f832bb6ebb030d6028f47c91714aaca",
        "c523bc96aae5e45688449750dd7fb995564958545972411f0510ed15cf434eeb",
        "cc182585a35b6f2b842ebb20f832ee51c5366278b0380bd10a50f2a5a26f1070",
        "e70f35069c8fde796be9118cfc115c3458b54c6df653b2078c5403eaca33184f",
        "ef5d9ae5ab9833208c0c3979a4ce743fbd0c36fa5676c3910af799444bc94ba7",
    }
)


class GenericLiveDynamicOwnerDecisionError(ValueError):
    """Raised when dynamic Live owner authority is malformed or altered."""


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise GenericLiveDynamicOwnerDecisionError("decision is not canonical JSON") from exc


def content_hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def _timestamp(value: Any, label: str) -> dt.datetime:
    if not isinstance(value, str) or value != value.strip() or not value:
        raise GenericLiveDynamicOwnerDecisionError(f"{label} is invalid")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GenericLiveDynamicOwnerDecisionError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise GenericLiveDynamicOwnerDecisionError(f"{label} needs a timezone")
    return parsed


def validate_generic_live_dynamic_owner_decision(
    payload: Mapping[str, Any],
    *,
    expected_content_hash: str | None = None,
    as_of: str | None = None,
    require_effective_session: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _FIELDS:
        raise GenericLiveDynamicOwnerDecisionError("owner decision fields are invalid")
    if payload.get("schema_version") != SCHEMA:
        raise GenericLiveDynamicOwnerDecisionError("owner decision schema is invalid")
    if not isinstance(payload.get("owner_decision_id"), str) or not _ID.fullmatch(payload["owner_decision_id"]):
        raise GenericLiveDynamicOwnerDecisionError("owner_decision_id is invalid")
    if payload.get("owner") != "Brett Olson" or payload.get("decision") != "APPROVE":
        raise GenericLiveDynamicOwnerDecisionError("owner APPROVE authority is missing")
    decided = _timestamp(payload.get("decided_at"), "decided_at")
    expires = _timestamp(payload.get("expires_at"), "expires_at")
    try:
        effective = dt.date.fromisoformat(str(payload.get("effective_session")))
    except ValueError as exc:
        raise GenericLiveDynamicOwnerDecisionError("effective_session is invalid") from exc
    if effective < decided.date() or expires <= decided or expires.date() < effective:
        raise GenericLiveDynamicOwnerDecisionError("decision chronology is invalid")
    if require_effective_session is not None and payload.get("effective_session") != require_effective_session:
        raise GenericLiveDynamicOwnerDecisionError("owner decision is for a different session")
    if as_of is not None:
        evaluated = _timestamp(as_of, "as_of")
        if evaluated < decided or evaluated > expires:
            raise GenericLiveDynamicOwnerDecisionError("owner decision is not current at as_of")
    if payload.get("lane_id") != "generic-live-v1" or payload.get("lane_kind") != "LIVE":
        raise GenericLiveDynamicOwnerDecisionError("lane scope is invalid")
    if payload.get("eligible_sleeve_ids") != ["caerus_lyra"]:
        raise GenericLiveDynamicOwnerDecisionError("only Lyra may be eligible")
    if payload.get("adapter_contract") != "CAERUS_GENERIC_LANE_V4":
        raise GenericLiveDynamicOwnerDecisionError("generic v4 adapter is required")

    capital = payload.get("capital_policy")
    if not isinstance(capital, Mapping) or set(capital) != _CAPITAL_FIELDS:
        raise GenericLiveDynamicOwnerDecisionError("capital policy fields are invalid")
    expected_capital = {
        "policy_version": CAPITAL_POLICY_VERSION,
        "nominal_capital_ceiling_usd": None,
        "gross_capital_basis": "FRESH_FACTUAL_BROKER_NET_LIQUIDATION_EQUITY",
        "buy_cash_basis": "FRESH_FACTUAL_BROKER_SETTLED_CASH",
        "maximum_gross_fraction": 0.95,
        "minimum_cash_reserve_fraction": 0.05,
        "buying_power_allowed": False,
        "margin_multiplier_allowed": False,
        "borrowing_allowed": False,
        "unsettled_funds_allowed": False,
        "unverified_pending_funds_allowed": False,
        "deposit_treatment": "INCLUDE_ONLY_AFTER_FACTUAL_NET_LIQUIDATION_AND_SETTLED_CASH_OBSERVATION",
        "withdrawal_or_equity_decline_treatment": "REDUCE_LIMITS_AUTOMATICALLY_FROM_FRESH_FACTUAL_VALUES",
        "freshness_seconds": 120,
    }
    if dict(capital) != expected_capital:
        raise GenericLiveDynamicOwnerDecisionError("capital policy differs from owner terms")

    trading = payload.get("trading_constraints")
    if not isinstance(trading, Mapping) or set(trading) != _TRADING_FIELDS:
        raise GenericLiveDynamicOwnerDecisionError("trading constraint fields are invalid")
    expected_trading = {
        "minimum_trade_usd": 100.0,
        "maximum_orders_per_session": 1,
        "whole_share_only": True,
        "long_only": True,
        "leverage_allowed": False,
        "shorting_allowed": False,
        "opportunistic_test_orders_allowed": False,
        "no_trade_when_plan_has_no_order": True,
    }
    if dict(trading) != expected_trading:
        raise GenericLiveDynamicOwnerDecisionError("trading constraints differ from owner terms")
    if frozenset(payload.get("preflight_requirements") or ()) != _PREFLIGHT:
        raise GenericLiveDynamicOwnerDecisionError("preflight requirements differ")
    if frozenset(payload.get("automatic_rearm_and_rollback_triggers") or ()) != _ROLLBACK:
        raise GenericLiveDynamicOwnerDecisionError("rollback triggers differ")
    supersedes = payload.get("supersedes_fixed_capital_artifact_hashes")
    if not isinstance(supersedes, list) or supersedes != sorted(EXPECTED_FIXED_CAPITAL_ARTIFACT_HASHES):
        raise GenericLiveDynamicOwnerDecisionError("superseded artifact hashes are not the exact governed set")
    for field in (
        "paper_authority_changed", "legacy_live_executor_allowed",
        "execution_authority", "activation_authority",
    ):
        if payload.get(field) is not False:
            raise GenericLiveDynamicOwnerDecisionError(f"{field} must remain false")
    if not isinstance(payload.get("content_hash"), str) or not _SHA.fullmatch(payload["content_hash"]):
        raise GenericLiveDynamicOwnerDecisionError("content_hash is invalid")
    if payload["content_hash"] != content_hash(payload):
        raise GenericLiveDynamicOwnerDecisionError("content_hash mismatch")
    if expected_content_hash is not None:
        if not isinstance(expected_content_hash, str) or not _SHA.fullmatch(expected_content_hash):
            raise GenericLiveDynamicOwnerDecisionError("trusted owner decision hash is invalid")
        if payload["content_hash"] != expected_content_hash:
            raise GenericLiveDynamicOwnerDecisionError("owner decision differs from trusted authority")
    return copy.deepcopy(dict(payload))


def build_generic_live_dynamic_owner_decision(
    *, decided_at: str, effective_session: str, expires_at: str,
    supersedes_fixed_capital_artifact_hashes: list[str] | None = None,
) -> dict[str, Any]:
    body = {
        "schema_version": SCHEMA,
        "owner_decision_id": f"owner-decision:generic-live-v1-dynamic:{effective_session.replace('-', '')}",
        "owner": "Brett Olson",
        "decision": "APPROVE",
        "decided_at": decided_at,
        "effective_session": effective_session,
        "expires_at": expires_at,
        "lane_id": "generic-live-v1",
        "lane_kind": "LIVE",
        "eligible_sleeve_ids": ["caerus_lyra"],
        "adapter_contract": "CAERUS_GENERIC_LANE_V4",
        "capital_policy": {
            "policy_version": CAPITAL_POLICY_VERSION,
            "nominal_capital_ceiling_usd": None,
            "gross_capital_basis": "FRESH_FACTUAL_BROKER_NET_LIQUIDATION_EQUITY",
            "buy_cash_basis": "FRESH_FACTUAL_BROKER_SETTLED_CASH",
            "maximum_gross_fraction": 0.95,
            "minimum_cash_reserve_fraction": 0.05,
            "buying_power_allowed": False,
            "margin_multiplier_allowed": False,
            "borrowing_allowed": False,
            "unsettled_funds_allowed": False,
            "unverified_pending_funds_allowed": False,
            "deposit_treatment": "INCLUDE_ONLY_AFTER_FACTUAL_NET_LIQUIDATION_AND_SETTLED_CASH_OBSERVATION",
            "withdrawal_or_equity_decline_treatment": "REDUCE_LIMITS_AUTOMATICALLY_FROM_FRESH_FACTUAL_VALUES",
            "freshness_seconds": 120,
        },
        "trading_constraints": {
            "minimum_trade_usd": 100.0,
            "maximum_orders_per_session": 1,
            "whole_share_only": True,
            "long_only": True,
            "leverage_allowed": False,
            "shorting_allowed": False,
            "opportunistic_test_orders_allowed": False,
            "no_trade_when_plan_has_no_order": True,
        },
        "preflight_requirements": sorted(_PREFLIGHT),
        "automatic_rearm_and_rollback_triggers": sorted(_ROLLBACK),
        "supersedes_fixed_capital_artifact_hashes": sorted(
            EXPECTED_FIXED_CAPITAL_ARTIFACT_HASHES
            if supersedes_fixed_capital_artifact_hashes is None
            else supersedes_fixed_capital_artifact_hashes
        ),
        "paper_authority_changed": False,
        "legacy_live_executor_allowed": False,
        "execution_authority": False,
        "activation_authority": False,
    }
    body["content_hash"] = content_hash(body)
    return validate_generic_live_dynamic_owner_decision(body)


__all__ = [
    "CAPITAL_POLICY_VERSION", "EXPECTED_FIXED_CAPITAL_ARTIFACT_HASHES", "SCHEMA",
    "GenericLiveDynamicOwnerDecisionError",
    "build_generic_live_dynamic_owner_decision", "content_hash",
    "validate_generic_live_dynamic_owner_decision",
]
