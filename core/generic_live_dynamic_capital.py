"""Dynamic cash-only capital proof for generic Live v1.

Gross exposure is limited by fresh broker net-liquidation equity.  A buy is
limited separately by fresh cash after proving that pending transfers are
zero.  Buying power, margin multipliers, and unverified funds are deliberately
not accepted as inputs.
"""

from __future__ import annotations

import copy
import hashlib
import math
from typing import Any, Mapping

from authority.lane_exact_plan import canonical_json, validate_lane_exact_execution_plan
from core.generic_live_dynamic_owner_decision import CAPITAL_POLICY_VERSION


SCHEMA = "caerus.generic_live_dynamic_capital_proof.v1"


class GenericLiveDynamicCapitalError(ValueError):
    """Raised when cash-only dynamic capital cannot be proven."""


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


def derive_dynamic_capital_limits(
    *, fresh_net_liquidation_equity_usd: float, fresh_cash_usd: float,
    pending_transfer_in_usd: float, pending_transfer_out_usd: float,
    observation_age_seconds: float,
) -> dict[str, float]:
    """Derive the owner-approved limits from fresh factual broker balances."""

    equity = _finite(fresh_net_liquidation_equity_usd, label="net liquidation equity", positive=True)
    cash = _finite(fresh_cash_usd, label="cash")
    pending_in = _finite(pending_transfer_in_usd, label="pending transfer in")
    pending_out = _finite(pending_transfer_out_usd, label="pending transfer out")
    age = _finite(observation_age_seconds, label="observation age")
    if pending_in != 0 or pending_out != 0:
        raise GenericLiveDynamicCapitalError("pending transfers make settled cash unverified")
    if age >= 120:
        raise GenericLiveDynamicCapitalError("broker capital evidence is not fresher than 120 seconds")
    return {
        "fresh_net_liquidation_equity_usd": equity,
        "fresh_settled_cash_usd": cash,
        "pending_transfer_in_usd": pending_in,
        "pending_transfer_out_usd": pending_out,
        "observation_age_seconds": age,
        "dynamic_gross_cap_usd": equity * 0.95,
        "required_settled_cash_reserve_usd": equity * 0.05,
        "maximum_new_buy_cash_usd": max(0.0, cash - equity * 0.05),
    }


def build_generic_live_dynamic_capital_proof(
    *, exact_plan: Mapping[str, Any], fresh_net_liquidation_equity_usd: float,
    fresh_cash_usd: float, pending_transfer_in_usd: float,
    pending_transfer_out_usd: float, observation_age_seconds: float,
    max_fee_usd: float = 0.01,
) -> dict[str, Any]:
    """Prove cash-only worst-case post-fill exposure without buying power."""

    failures = validate_lane_exact_execution_plan(exact_plan)
    if failures:
        raise GenericLiveDynamicCapitalError("exact plan is invalid: " + ",".join(failures))
    limits = derive_dynamic_capital_limits(
        fresh_net_liquidation_equity_usd=fresh_net_liquidation_equity_usd,
        fresh_cash_usd=fresh_cash_usd,
        pending_transfer_in_usd=pending_transfer_in_usd,
        pending_transfer_out_usd=pending_transfer_out_usd,
        observation_age_seconds=observation_age_seconds,
    )
    equity = limits["fresh_net_liquidation_equity_usd"]
    cash = limits["fresh_settled_cash_usd"]
    pending_in = limits["pending_transfer_in_usd"]
    pending_out = limits["pending_transfer_out_usd"]
    age = limits["observation_age_seconds"]
    fee = _finite(max_fee_usd, label="maximum fee")
    if abs(cash - float(exact_plan["starting_cash"])) > 0.01:
        raise GenericLiveDynamicCapitalError("fresh cash differs from exact plan starting cash")
    if abs(equity - float(exact_plan["starting_equity"])) > 0.01:
        raise GenericLiveDynamicCapitalError("fresh equity differs from exact plan starting equity")

    orders = [*exact_plan["sell_orders"], *exact_plan["buy_orders"]]
    if len(orders) > 1:
        raise GenericLiveDynamicCapitalError("at most one exact order is allowed")
    marks = {str(row["symbol"]): float(row["price"]) for row in exact_plan["price_marks"]}
    positions = {str(row["symbol"]): float(row["quantity"]) for row in exact_plan["starting_positions"]}
    if any(not math.isfinite(qty) or qty < 0 for qty in positions.values()):
        raise GenericLiveDynamicCapitalError("starting positions must be finite and long-only")
    missing = sorted(symbol for symbol, qty in positions.items() if qty > 0 and symbol not in marks)
    if missing:
        raise GenericLiveDynamicCapitalError("starting position marks are missing: " + ",".join(missing))

    gross_prices = dict(marks)
    worst_cash = cash
    order_id = symbol = side = None
    quantity = limit_price = applied_fee = 0.0
    if orders:
        order = orders[0]
        order_id = order["order_id"]
        symbol = str(order["symbol"])
        side = str(order["side"])
        quantity = _finite(order["quantity"], label="quantity", positive=True)
        limit_price = _finite(order["enforcement_price"], label="enforcement price", positive=True)
        current = positions.get(symbol, 0.0)
        if side == "BUY":
            positions[symbol] = current + quantity
            worst_cash -= quantity * limit_price + fee
            applied_fee = fee
            gross_prices[symbol] = max(float(marks.get(symbol, 0.0)), limit_price)
        elif side == "SELL":
            if quantity > current + 1e-9:
                raise GenericLiveDynamicCapitalError("sell exceeds the long position")
            positions[symbol] = current - quantity
            # Sale proceeds are not treated as settled cash in the same session.
            worst_cash -= fee
            applied_fee = fee
            gross_prices[symbol] = max(float(marks.get(symbol, 0.0)), limit_price)
        else:
            raise GenericLiveDynamicCapitalError("order side is invalid")

    gross = sum(max(qty, 0.0) * gross_prices[symbol] for symbol, qty in positions.items())
    gross_cap = limits["dynamic_gross_cap_usd"]
    reserve = limits["required_settled_cash_reserve_usd"]
    body = {
        "schema_version": SCHEMA,
        "capital_policy_version": CAPITAL_POLICY_VERSION,
        "plan_hash": exact_plan["content_hash"],
        "account_id_hash": exact_plan["account_id_hash"],
        "order_id": order_id,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "limit_price": limit_price,
        "max_fee_usd": applied_fee,
        "fresh_net_liquidation_equity_usd": equity,
        "fresh_settled_cash_usd": cash,
        "pending_transfer_in_usd": pending_in,
        "pending_transfer_out_usd": pending_out,
        "observation_age_seconds": age,
        "nominal_capital_ceiling_usd": None,
        "dynamic_gross_cap_usd": gross_cap,
        "required_settled_cash_reserve_usd": reserve,
        "worst_case_posttrade_gross_usd": gross,
        "worst_case_posttrade_settled_cash_usd": worst_cash,
        "gross_limit_pass": gross <= gross_cap + 1e-9,
        "settled_cash_reserve_pass": worst_cash + 1e-9 >= reserve,
        "long_only_pass": all(qty >= -1e-9 for qty in positions.values()),
        "buying_power_used": False,
        "margin_multiplier_used": False,
        "unsettled_funds_used": False,
        "execution_authority": False,
    }
    body["content_hash"] = _hash(body)
    return body


__all__ = [
    "SCHEMA", "GenericLiveDynamicCapitalError",
    "build_generic_live_dynamic_capital_proof", "derive_dynamic_capital_limits",
]
