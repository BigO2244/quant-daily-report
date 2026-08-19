"""Strict dynamic cash-only capital and worst-case fee proof."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import math
import re
from typing import Any, Mapping

from authority.lane_exact_plan import canonical_json, validate_lane_exact_execution_plan
from core.generic_live_dynamic_account import validate_generic_live_dynamic_account_observation
from core.generic_live_dynamic_owner_decision import (
    CAPITAL_POLICY_VERSION,
    validate_generic_live_dynamic_owner_decision,
)
from core.generic_live_dynamic_settled_cash import (
    validate_generic_live_dynamic_settled_cash_evidence,
)


SCHEMA = "caerus.generic_live_dynamic_capital_proof.v2"
FEE_SCHEDULE_SCHEMA = "caerus.generic_live_governed_fee_schedule.v1"
FEE_PROOF_SCHEMA = "caerus.generic_live_worst_case_fee_proof.v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")


class GenericLiveDynamicCapitalError(ValueError):
    pass


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def _finite(value: Any, *, label: str, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise GenericLiveDynamicCapitalError(f"{label} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GenericLiveDynamicCapitalError(f"{label} must be numeric") from exc
    if not math.isfinite(number) or number < 0 or (positive and number <= 0):
        raise GenericLiveDynamicCapitalError(f"{label} must be finite and {'positive' if positive else 'nonnegative'}")
    return number


def validate_governed_fee_schedule(
    payload: Mapping[str, Any], *, expected_content_hash: str, as_of_date: str,
) -> dict[str, Any]:
    fields = {
        "schema_version", "provider", "effective_from", "effective_through",
        "base_fee_usd", "buy_notional_bps", "sell_notional_bps",
        "sell_per_share_usd", "minimum_fee_usd", "maximum_fee_usd",
        "source_document_hash", "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields or payload.get("schema_version") != FEE_SCHEDULE_SCHEMA:
        raise GenericLiveDynamicCapitalError("governed fee schedule fields are invalid")
    if payload.get("provider") != "ALPACA_LIVE":
        raise GenericLiveDynamicCapitalError("fee schedule provider differs")
    try:
        effective = dt.date.fromisoformat(str(payload.get("effective_from")))
        through = dt.date.fromisoformat(str(payload.get("effective_through")))
        session = dt.date.fromisoformat(as_of_date)
    except ValueError as exc:
        raise GenericLiveDynamicCapitalError("fee schedule dates are invalid") from exc
    if not (effective <= session <= through):
        raise GenericLiveDynamicCapitalError("fee schedule is not effective for the session")
    for field in (
        "base_fee_usd", "buy_notional_bps", "sell_notional_bps",
        "sell_per_share_usd", "minimum_fee_usd", "maximum_fee_usd",
    ):
        _finite(payload.get(field), label=field)
    if float(payload["maximum_fee_usd"]) < float(payload["minimum_fee_usd"]):
        raise GenericLiveDynamicCapitalError("fee schedule maximum is below its minimum")
    for field in ("source_document_hash", "content_hash"):
        if not isinstance(payload.get(field), str) or not _SHA.fullmatch(payload[field]):
            raise GenericLiveDynamicCapitalError(f"{field} is invalid")
    if payload["content_hash"] != _hash(payload) or payload["content_hash"] != expected_content_hash:
        raise GenericLiveDynamicCapitalError("fee schedule is not the independently pinned schedule")
    return copy.deepcopy(dict(payload))


def build_worst_case_fee_proof(
    *, exact_plan: Mapping[str, Any], governed_fee_schedule: Mapping[str, Any],
    trusted_fee_schedule_hash: str,
) -> dict[str, Any]:
    failures = validate_lane_exact_execution_plan(exact_plan)
    if failures:
        raise GenericLiveDynamicCapitalError("exact plan is invalid: " + ",".join(failures))
    schedule = validate_governed_fee_schedule(
        governed_fee_schedule, expected_content_hash=trusted_fee_schedule_hash,
        as_of_date=exact_plan["trade_date"],
    )
    orders = [*exact_plan["sell_orders"], *exact_plan["buy_orders"]]
    if len(orders) > 1:
        raise GenericLiveDynamicCapitalError("at most one exact order is allowed")
    order = orders[0] if orders else None
    if order is None:
        fee = notional = quantity = price = 0.0
        order_id = side = None
    else:
        order_id = order["order_id"]
        side = str(order["side"])
        quantity = _finite(order["quantity"], label="quantity", positive=True)
        price = _finite(order["enforcement_price"], label="enforcement price", positive=True)
        notional = quantity * price
        rate_bps = float(schedule["buy_notional_bps"] if side == "BUY" else schedule["sell_notional_bps"])
        fee = float(schedule["base_fee_usd"]) + notional * rate_bps / 10000.0
        if side == "SELL":
            fee += quantity * float(schedule["sell_per_share_usd"])
        fee = max(fee, float(schedule["minimum_fee_usd"]))
        fee = min(fee, float(schedule["maximum_fee_usd"]))
        if side not in {"BUY", "SELL"}:
            raise GenericLiveDynamicCapitalError("order side is invalid")
    body = {
        "schema_version": FEE_PROOF_SCHEMA,
        "fee_schedule_hash": schedule["content_hash"],
        "plan_hash": exact_plan["content_hash"], "order_id": order_id,
        "side": side, "quantity": quantity, "enforcement_price": price,
        "worst_case_notional_usd": notional, "worst_case_fee_usd": fee,
        "status": "FACTUAL_GOVERNED_MAXIMUM_BOUND",
        "execution_authority": False,
    }
    body["content_hash"] = _hash(body)
    return body


def validate_worst_case_fee_proof(
    payload: Mapping[str, Any], *, exact_plan: Mapping[str, Any],
    governed_fee_schedule: Mapping[str, Any], trusted_fee_schedule_hash: str,
) -> dict[str, Any]:
    expected = build_worst_case_fee_proof(
        exact_plan=exact_plan, governed_fee_schedule=governed_fee_schedule,
        trusted_fee_schedule_hash=trusted_fee_schedule_hash,
    )
    if dict(payload) != expected:
        raise GenericLiveDynamicCapitalError("worst-case fee proof differs from governed recomputation")
    return copy.deepcopy(dict(payload))


def derive_dynamic_capital_limits(*, equity_usd: float, settled_cash_usd: float) -> dict[str, float]:
    equity = _finite(equity_usd, label="net liquidation equity", positive=True)
    settled = _finite(settled_cash_usd, label="settled cash")
    return {
        "fresh_net_liquidation_equity_usd": equity,
        "fresh_settled_cash_usd": settled,
        "dynamic_gross_cap_usd": equity * 0.95,
        "required_settled_cash_reserve_usd": equity * 0.05,
        "maximum_new_buy_cash_usd": max(0.0, settled - equity * 0.05),
    }


def build_generic_live_dynamic_capital_proof(
    *, exact_plan: Mapping[str, Any], owner_decision: Mapping[str, Any],
    trusted_owner_decision_hash: str, account_observation: Mapping[str, Any],
    raw_account_response: bytes, settled_cash_evidence: Mapping[str, Any],
    raw_order_history_source: bytes, raw_fill_history_source: bytes,
    fee_proof: Mapping[str, Any], governed_fee_schedule: Mapping[str, Any],
    trusted_fee_schedule_hash: str, governed_capital_policy: Mapping[str, Any],
    evaluated_at: str,
) -> dict[str, Any]:
    failures = validate_lane_exact_execution_plan(exact_plan)
    if failures:
        raise GenericLiveDynamicCapitalError("exact plan is invalid: " + ",".join(failures))
    owner = validate_generic_live_dynamic_owner_decision(
        owner_decision, expected_content_hash=trusted_owner_decision_hash,
        as_of=evaluated_at, require_effective_session=exact_plan["trade_date"],
    )
    expected_capital_policy = {
        "policy_id": "generic-live-v1-dynamic-capital",
        "capital_basis": "FULL_ACCOUNT_EQUITY",
        "capital_ceiling_usd": None,
    }
    if dict(governed_capital_policy) != expected_capital_policy:
        raise GenericLiveDynamicCapitalError("governed capital policy is not the dynamic no-ceiling policy")
    capital_policy_hash = hashlib.sha256(
        canonical_json(governed_capital_policy).encode()
    ).hexdigest()
    if exact_plan.get("capital_policy_hash") != capital_policy_hash or exact_plan.get("source_hashes", {}).get("capital_policy") != capital_policy_hash:
        raise GenericLiveDynamicCapitalError("exact plan still binds a stale fixed-capital policy")
    account = validate_generic_live_dynamic_account_observation(
        account_observation, raw_account_response=raw_account_response, as_of=evaluated_at,
    )
    settled = validate_generic_live_dynamic_settled_cash_evidence(
        settled_cash_evidence, account_observation=account,
        raw_account_response=raw_account_response,
        raw_order_history_source=raw_order_history_source,
        raw_fill_history_source=raw_fill_history_source,
    )
    if settled.get("evaluated_at") != evaluated_at or settled.get("as_of_date") != exact_plan["trade_date"]:
        raise GenericLiveDynamicCapitalError("settled-cash evidence is for a different boundary")
    fee = validate_worst_case_fee_proof(
        fee_proof, exact_plan=exact_plan, governed_fee_schedule=governed_fee_schedule,
        trusted_fee_schedule_hash=trusted_fee_schedule_hash,
    )
    if account["account_id_hash"] != exact_plan["account_id_hash"]:
        raise GenericLiveDynamicCapitalError("fresh account pin differs from the exact plan")
    equity = float(account["net_liquidation_equity_usd"])
    broker_cash = float(account["cash_usd"])
    settled_cash = float(settled["settled_cash_usd"])
    if abs(broker_cash - float(exact_plan["starting_cash"])) > 0.01:
        raise GenericLiveDynamicCapitalError("fresh broker cash differs from exact plan starting cash")
    if abs(equity - float(exact_plan["starting_equity"])) > 0.01:
        raise GenericLiveDynamicCapitalError("fresh equity differs from exact plan starting equity")
    orders = [*exact_plan["sell_orders"], *exact_plan["buy_orders"]]
    if len(orders) > int(owner["trading_constraints"]["maximum_orders_per_session"]):
        raise GenericLiveDynamicCapitalError("owner maximum order count is exceeded")
    marks = {str(row["symbol"]): float(row["price"]) for row in exact_plan["price_marks"]}
    positions = {str(row["symbol"]): float(row["quantity"]) for row in exact_plan["starting_positions"]}
    if any(not math.isfinite(qty) or qty < 0 for qty in positions.values()):
        raise GenericLiveDynamicCapitalError("starting positions must be finite and long-only")
    missing = sorted(symbol for symbol, qty in positions.items() if qty > 0 and symbol not in marks)
    if missing:
        raise GenericLiveDynamicCapitalError("starting position marks are missing: " + ",".join(missing))
    gross_prices = dict(marks)
    worst_cash = settled_cash
    order_id = symbol = side = None
    quantity = limit_price = 0.0
    applied_fee = float(fee["worst_case_fee_usd"])
    if orders:
        order = orders[0]
        order_id, symbol, side = order["order_id"], str(order["symbol"]), str(order["side"])
        quantity = _finite(order["quantity"], label="quantity", positive=True)
        limit_price = _finite(order["enforcement_price"], label="enforcement price", positive=True)
        if quantity != math.floor(quantity):
            raise GenericLiveDynamicCapitalError("owner requires whole-share quantity")
        if quantity * limit_price + 1e-9 < float(owner["trading_constraints"]["minimum_trade_usd"]):
            raise GenericLiveDynamicCapitalError("order is below the owner minimum trade")
        current = positions.get(symbol, 0.0)
        if side == "BUY":
            positions[symbol] = current + quantity
            worst_cash -= quantity * limit_price + applied_fee
        elif side == "SELL":
            if quantity > current + 1e-9:
                raise GenericLiveDynamicCapitalError("sell exceeds the long position")
            positions[symbol] = current - quantity
            # Same-session sale proceeds remain unavailable to the settled-cash reserve.
            worst_cash -= applied_fee
        else:
            raise GenericLiveDynamicCapitalError("order side is invalid")
        gross_prices[symbol] = max(float(marks.get(symbol, 0.0)), limit_price)
    gross = sum(max(qty, 0.0) * gross_prices[symbol] for symbol, qty in positions.items())
    limits = derive_dynamic_capital_limits(equity_usd=equity, settled_cash_usd=settled_cash)
    if gross > limits["dynamic_gross_cap_usd"] + 1e-9:
        raise GenericLiveDynamicCapitalError("worst-case gross exceeds the dynamic 95% cap")
    if worst_cash + 1e-9 < limits["required_settled_cash_reserve_usd"]:
        raise GenericLiveDynamicCapitalError("worst-case settled cash breaches the 5% reserve")
    body = {
        "schema_version": SCHEMA, "capital_policy_version": CAPITAL_POLICY_VERSION,
        "owner_decision_hash": owner["content_hash"], "plan_hash": exact_plan["content_hash"],
        "capital_policy_hash": capital_policy_hash,
        "account_id_hash": exact_plan["account_id_hash"], "account_observation_hash": account["content_hash"],
        "settled_cash_evidence_hash": settled["content_hash"], "fee_proof_hash": fee["content_hash"],
        "evaluated_at": evaluated_at, "order_id": order_id, "symbol": symbol, "side": side,
        "quantity": quantity, "limit_price": limit_price, "max_fee_usd": applied_fee,
        "fresh_net_liquidation_equity_usd": equity, "fresh_broker_cash_usd": broker_cash,
        "fresh_settled_cash_usd": settled_cash, "nominal_capital_ceiling_usd": None,
        "dynamic_gross_cap_usd": limits["dynamic_gross_cap_usd"],
        "required_settled_cash_reserve_usd": limits["required_settled_cash_reserve_usd"],
        "worst_case_posttrade_gross_usd": gross,
        "worst_case_posttrade_settled_cash_usd": worst_cash,
        "gross_limit_pass": True, "settled_cash_reserve_pass": True,
        "whole_share_pass": True, "minimum_trade_pass": True, "long_only_pass": True,
        "buying_power_used": False, "margin_multiplier_used": False,
        "unsettled_funds_used": False, "execution_authority": False,
    }
    body["content_hash"] = _hash(body)
    return body


def validate_generic_live_dynamic_capital_proof(payload: Mapping[str, Any], **sources: Any) -> dict[str, Any]:
    expected = build_generic_live_dynamic_capital_proof(**sources)
    if dict(payload) != expected:
        raise GenericLiveDynamicCapitalError("dynamic capital proof differs from factual recomputation")
    return copy.deepcopy(dict(payload))


__all__ = [
    "FEE_PROOF_SCHEMA", "FEE_SCHEDULE_SCHEMA", "SCHEMA", "GenericLiveDynamicCapitalError",
    "build_generic_live_dynamic_capital_proof", "build_worst_case_fee_proof",
    "derive_dynamic_capital_limits", "validate_generic_live_dynamic_capital_proof",
    "validate_governed_fee_schedule", "validate_worst_case_fee_proof",
]
