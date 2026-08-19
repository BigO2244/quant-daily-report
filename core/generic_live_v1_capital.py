"""Worst-case capital proof for the owner-approved generic Live v1 lane."""

from __future__ import annotations

import copy
import hashlib
import math
from typing import Any, Mapping

from authority.lane_exact_plan import canonical_json, validate_lane_exact_execution_plan


GENERIC_LIVE_V1_CAPITAL_PROOF_SCHEMA = "caerus.generic_live_v1_capital_proof.v1"


class GenericLiveV1CapitalError(ValueError):
    """Raised when worst-case post-fill capital cannot be proven."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def build_generic_live_v1_capital_proof(
    *, exact_plan: Mapping[str, Any], fresh_equity_usd: float,
    fresh_cash_usd: float, max_fee_usd: float = 0.01,
) -> dict[str, Any]:
    """Prove dynamic gross and cash limits at the governed limit price."""

    failures = validate_lane_exact_execution_plan(exact_plan)
    if failures:
        raise GenericLiveV1CapitalError("exact plan is invalid: " + ",".join(failures))
    try:
        equity = float(fresh_equity_usd)
        cash = float(fresh_cash_usd)
        fee = float(max_fee_usd)
    except (TypeError, ValueError) as exc:
        raise GenericLiveV1CapitalError("capital inputs must be numeric") from exc
    if not all(math.isfinite(value) for value in (equity, cash, fee)) or equity <= 0 or cash < 0 or fee < 0:
        raise GenericLiveV1CapitalError("capital inputs must be finite and nonnegative")
    if abs(cash - float(exact_plan["starting_cash"])) > 0.01:
        raise GenericLiveV1CapitalError("fresh cash differs from exact plan starting cash")
    orders = [*exact_plan["sell_orders"], *exact_plan["buy_orders"]]
    if len(orders) > 1:
        raise GenericLiveV1CapitalError("Live v1 permits at most one exact order")
    marks = {str(row["symbol"]): float(row["price"]) for row in exact_plan["price_marks"]}
    positions = {
        str(row["symbol"]): float(row["quantity"])
        for row in exact_plan["starting_positions"]
    }
    if any(not math.isfinite(quantity) or quantity < 0 for quantity in positions.values()):
        raise GenericLiveV1CapitalError("starting positions are not finite long-only quantities")
    gross_prices = dict(marks)
    worst_cash = cash
    order_id = None
    side = None
    symbol = None
    quantity = 0.0
    limit_price = 0.0
    applied_fee = 0.0
    starting_symbol_quantity = 0.0
    starting_other_gross = sum(
        quantity_value * marks[symbol_value]
        for symbol_value, quantity_value in positions.items()
    )
    gross_valuation_price = 0.0
    expected_symbol_quantity = 0.0
    if orders:
        order = orders[0]
        order_id = order["order_id"]
        side = order["side"]
        symbol = str(order["symbol"])
        quantity = float(order["quantity"])
        limit_price = float(order["enforcement_price"])
        if not all(math.isfinite(value) and value > 0 for value in (quantity, limit_price)):
            raise GenericLiveV1CapitalError("exact order quantity/limit price is invalid")
        current = positions.get(symbol, 0.0)
        starting_symbol_quantity = current
        starting_other_gross = sum(
            quantity_value * marks[symbol_value]
            for symbol_value, quantity_value in positions.items()
            if symbol_value != symbol
        )
        if side == "BUY":
            positions[symbol] = current + quantity
            worst_cash -= quantity * limit_price + fee
        elif side == "SELL":
            if quantity > current + 1e-9:
                raise GenericLiveV1CapitalError("exact sell exceeds the starting long position")
            positions[symbol] = current - quantity
            worst_cash += quantity * limit_price - fee
        else:
            raise GenericLiveV1CapitalError("exact order side is invalid")
        applied_fee = fee
        gross_prices[symbol] = max(float(marks.get(symbol, 0.0)), limit_price)
        gross_valuation_price = gross_prices[symbol]
        expected_symbol_quantity = positions[symbol]
    missing = sorted(symbol for symbol, qty in positions.items() if qty > 0 and symbol not in gross_prices)
    if missing:
        raise GenericLiveV1CapitalError("worst-case gross marks are missing: " + ",".join(missing))
    worst_gross = sum(
        max(quantity_value, 0.0) * gross_prices[symbol]
        for symbol, quantity_value in positions.items()
    )
    effective = min(460.0, equity)
    gross_cap = effective * 0.95
    cash_reserve = effective * 0.05
    body = {
        "schema_version": GENERIC_LIVE_V1_CAPITAL_PROOF_SCHEMA,
        "plan_hash": exact_plan["content_hash"],
        "account_id_hash": exact_plan["account_id_hash"],
        "order_id": order_id,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "limit_price": limit_price,
        "max_fee_usd": applied_fee,
        "starting_symbol_quantity": starting_symbol_quantity,
        "starting_other_gross_usd": starting_other_gross,
        "gross_valuation_price": gross_valuation_price,
        "expected_posttrade_symbol_quantity": expected_symbol_quantity,
        "fresh_equity_usd": equity,
        "fresh_cash_usd": cash,
        "effective_capital_usd": effective,
        "dynamic_gross_cap_usd": gross_cap,
        "required_cash_reserve_usd": cash_reserve,
        "worst_case_posttrade_gross_usd": worst_gross,
        "worst_case_posttrade_cash_usd": worst_cash,
        "gross_limit_pass": worst_gross <= gross_cap + 1e-9,
        "cash_reserve_pass": worst_cash + 1e-9 >= cash_reserve,
        "long_only_pass": all(quantity_value >= -1e-9 for quantity_value in positions.values()),
        "execution_authority": False,
    }
    body["content_hash"] = _hash(body)
    return body


__all__ = [
    "GENERIC_LIVE_V1_CAPITAL_PROOF_SCHEMA", "GenericLiveV1CapitalError",
    "build_generic_live_v1_capital_proof",
]
