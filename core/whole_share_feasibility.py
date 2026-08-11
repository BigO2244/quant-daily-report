"""Deterministic nearest-feasible whole-share portfolio construction.

This module translates an approved weight vector into mechanical integer share
quantities. It does not select symbols or alter target weights. A feasible
floor/ceiling incumbent establishes an objective upper bound. Because every
individual position-error square is a non-negative term in that objective, the
bound defines a finite integer interval for every symbol containing every
candidate that could equal or improve the incumbent. That complete bounded
Cartesian region is then evaluated subject to the governed cash floor and
executable-order constraints.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from typing import Any, Mapping

import pandas as pd

from core.target_attainment_policy import validate_target_attainment_policy
from paper.paper_broker import apply_slippage


SCHEMA_VERSION = "caerus.whole_share_feasibility.v1"
MAX_EXHAUSTIVE_TARGETS = 12
MAX_EXACT_CANDIDATES = 5_000_000
TRADE_COLUMNS = [
    "ticker",
    "side",
    "shares",
    "price",
    "slippage_cost",
    "notional",
    "reason",
]


class WholeShareFeasibilityError(ValueError):
    """Raised when no governed whole-share allocation can be proven."""


def whole_share_proof_content_hash(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("proof_content_hash", None)
    encoded = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def seal_whole_share_proof(payload: Mapping[str, Any]) -> dict[str, Any]:
    sealed = dict(payload)
    sealed["proof_content_hash"] = whole_share_proof_content_hash(sealed)
    return sealed


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _holdings_map(holdings: pd.DataFrame | None) -> dict[str, float]:
    out: dict[str, float] = {}
    if holdings is None or holdings.empty:
        return out
    for _, row in holdings.iterrows():
        symbol = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
        if symbol:
            out[symbol] = out.get(symbol, 0.0) + _finite(
                row.get("shares") if row.get("shares") is not None else row.get("qty")
            )
    return out


def _target_rows(targets: pd.DataFrame, prices: pd.Series) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    for _, row in targets.copy().sort_values("ticker").iterrows():
        symbol = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
        weight = _finite(row.get("target_weight"), -1.0)
        price = _finite(prices.get(symbol) if symbol else None, 0.0)
        if not symbol or weight < 0.0 or price <= 0.0:
            raise WholeShareFeasibilityError(
                f"invalid whole-share target input for {symbol or 'UNKNOWN'}"
            )
        rows.append({"symbol": symbol, "target_weight": weight, "price": price})
    if not rows:
        raise WholeShareFeasibilityError("whole-share optimizer requires targets")
    if len(rows) > MAX_EXHAUSTIVE_TARGETS:
        raise WholeShareFeasibilityError(
            f"whole-share optimizer target count exceeds {MAX_EXHAUSTIVE_TARGETS}"
        )
    return rows


def _quantity_options(target_quantity: float) -> tuple[int, ...]:
    lower = max(0, int(math.floor(target_quantity)))
    upper = max(0, int(math.ceil(target_quantity)))
    return (lower,) if lower == upper else (lower, upper)


def _transition_rows(
    *,
    chosen: Mapping[str, int],
    holdings: Mapping[str, float],
    prices: Mapping[str, float],
    cfg: Any,
) -> list[dict[str, Any]] | None:
    rows: list[dict[str, Any]] = []
    all_symbols = sorted(set(chosen).union(holdings))
    for symbol in all_symbols:
        target_quantity = float(chosen.get(symbol, 0))
        current_quantity = float(holdings.get(symbol, 0.0))
        delta = target_quantity - current_quantity
        if abs(delta) <= 1e-9:
            continue
        price = _finite(prices.get(symbol), 0.0)
        if price <= 0.0:
            return None
        transitions: list[tuple[str, float]]
        current_fraction = current_quantity - math.floor(max(0.0, current_quantity))
        if delta > 0.0 and current_fraction > 1e-9:
            # A legacy fractional holding cannot reach an integer target with a
            # fractional net BUY. Mechanically liquidate only its fractional
            # remainder in the sell phase, then buy whole shares from the
            # resulting integer quantity.
            transitions = [
                ("SELL", current_fraction),
                ("BUY", target_quantity - math.floor(current_quantity)),
            ]
        else:
            transitions = [("BUY" if delta > 0.0 else "SELL", abs(delta))]

        for side, shares in transitions:
            if shares <= 1e-9:
                continue
            fractional = not math.isclose(shares, round(shares), abs_tol=1e-9)
            if side == "BUY" and fractional:
                return None
            if side == "SELL" and fractional and not bool(cfg.allow_fractional_sells):
                return None
            slipped_price, slippage_per_share = apply_slippage(
                price,
                side,
                float(cfg.slippage_bps or 0.0),
            )
            notional = shares * slipped_price
            minimum = (
                float(cfg.fractional_sell_min_trade_dollars)
                if side == "SELL" and fractional
                else float(cfg.min_trade_dollars)
            )
            if notional + 1e-9 < minimum:
                return None
            rows.append(
                {
                    "ticker": symbol,
                    "side": side,
                    "shares": float(shares),
                    "price": float(slipped_price),
                    "slippage_cost": float(slippage_per_share * shares),
                    "notional": float(notional),
                    "reason": "whole_share_nearest_feasible",
                }
            )
    return sorted(rows, key=lambda row: (0 if row["side"] == "SELL" else 1, row["ticker"]))


def build_nearest_feasible_whole_share_trades(
    *,
    holdings: pd.DataFrame,
    targets: pd.DataFrame,
    prices: pd.Series,
    total_equity: float,
    cfg: Any,
    policy: Mapping[str, Any],
    max_orders: int | None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    normalized_policy = validate_target_attainment_policy(policy)
    equity = _finite(total_equity, 0.0)
    if equity <= 0.0:
        raise WholeShareFeasibilityError("whole-share optimizer requires positive equity")
    target_rows = _target_rows(targets, prices)
    holdings_by_symbol = _holdings_map(holdings)
    price_by_symbol = {
        str(symbol): float(price)
        for symbol, price in prices.items()
        if _finite(price, 0.0) > 0.0
    }
    options = [
        _quantity_options(
            float(row["target_weight"]) * equity / float(row["price"])
        )
        for row in target_rows
    ]
    symbols = [str(row["symbol"]) for row in target_rows]
    target_weights = {
        str(row["symbol"]): float(row["target_weight"]) for row in target_rows
    }
    target_cash = float(normalized_policy["target_cash_weight"])
    minimum_cash = float(normalized_policy["minimum_cash_weight"])
    order_limit = None if max_orders is None else max(0, int(max_orders))

    best: tuple[tuple[Any, ...], dict[str, Any]] | None = None
    evaluated = 0
    feasible = 0

    def consider(quantities: tuple[int, ...]) -> None:
        nonlocal best, evaluated, feasible
        evaluated += 1
        chosen = dict(zip(symbols, (int(value) for value in quantities)))
        invested = sum(chosen[symbol] * price_by_symbol[symbol] for symbol in symbols)
        cash = equity - invested
        cash_weight = cash / equity
        if cash_weight + 1e-12 < minimum_cash:
            return
        transition_rows = _transition_rows(
            chosen=chosen,
            holdings=holdings_by_symbol,
            prices=price_by_symbol,
            cfg=cfg,
        )
        if transition_rows is None:
            return
        if order_limit is not None and len(transition_rows) > order_limit:
            return
        feasible += 1
        position_drifts = {
            symbol: chosen[symbol] * price_by_symbol[symbol] / equity
            - target_weights[symbol]
            for symbol in symbols
        }
        cash_drift = cash_weight - target_cash
        squared_tracking_error = sum(value * value for value in position_drifts.values()) + cash_drift * cash_drift
        absolute_tracking_error = sum(abs(value) for value in position_drifts.values()) + abs(cash_drift)
        turnover = sum(float(row["notional"]) for row in transition_rows)
        key = (
            squared_tracking_error,
            absolute_tracking_error,
            turnover,
            tuple(chosen[symbol] for symbol in symbols),
        )
        record = {
            "chosen": chosen,
            "cash": cash,
            "cash_weight": cash_weight,
            "cash_drift": cash_drift,
            "position_drifts": position_drifts,
            "squared_tracking_error": squared_tracking_error,
            "absolute_tracking_error": absolute_tracking_error,
            "turnover": turnover,
            "transition_rows": transition_rows,
        }
        if best is None or key < best[0]:
            best = (key, record)

    for quantities in itertools.product(*options):
        consider(tuple(int(value) for value in quantities))

    if best is None:
        raise WholeShareFeasibilityError(
            "no floor/ceiling whole-share incumbent satisfies cash, order-count, "
            "and minimum-notional constraints; exact nearest-feasible proof unavailable"
        )

    incumbent_error = float(best[1]["squared_tracking_error"])
    error_radius = math.sqrt(max(0.0, incumbent_error)) + 1e-12
    exact_options: list[tuple[int, ...]] = []
    individual_cash_cap = max(0.0, (1.0 - minimum_cash) * equity)
    for row in target_rows:
        symbol = str(row["symbol"])
        weight = float(row["target_weight"])
        price = float(row["price"])
        lower_quantity = max(
            0,
            int(math.ceil(((weight - error_radius) * equity / price) - 1e-12)),
        )
        upper_quantity = min(
            int(math.floor(((weight + error_radius) * equity / price) + 1e-12)),
            int(math.floor((individual_cash_cap / price) + 1e-12)),
        )
        if upper_quantity < lower_quantity:
            raise WholeShareFeasibilityError(
                f"objective bound produced no integer candidates for {symbol}"
            )
        exact_options.append(tuple(range(lower_quantity, upper_quantity + 1)))

    exact_search_space = math.prod(len(values) for values in exact_options)
    if exact_search_space > MAX_EXACT_CANDIDATES:
        raise WholeShareFeasibilityError(
            "exact nearest-feasible proof search exceeds deterministic safety limit: "
            f"{exact_search_space}>{MAX_EXACT_CANDIDATES}"
        )

    # Retain the feasible incumbent as the initial best. Every allocation able
    # to equal or improve its objective lies inside these intervals because each
    # individual position-error square is bounded by the complete incumbent
    # objective. Evaluating the full product proves the constrained optimum.
    evaluated = 0
    feasible = 0
    for quantities in itertools.product(*exact_options):
        consider(tuple(int(value) for value in quantities))

    record = best[1]
    chosen = record["chosen"]
    allocation_rows = []
    for symbol in symbols:
        actual_weight = chosen[symbol] * price_by_symbol[symbol] / equity
        allocation_rows.append(
            {
                "symbol": symbol,
                "target_weight": round(target_weights[symbol], 10),
                "target_quantity": int(chosen[symbol]),
                "price": round(price_by_symbol[symbol], 10),
                "projected_weight": round(actual_weight, 10),
                "projected_weight_drift": round(
                    actual_weight - target_weights[symbol], 10
                ),
            }
        )
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "proof_method": "EXHAUSTIVE_PROVABLY_BOUNDED_INTEGER_CARTESIAN",
        "objective": "MINIMIZE_SQUARED_POSITION_AND_CASH_WEIGHT_TRACKING_ERROR",
        "tie_breakers": ["absolute_tracking_error", "turnover", "symbol_quantity_vector"],
        "candidate_count_evaluated": evaluated,
        "bounded_search_space_candidate_count": exact_search_space,
        "feasible_candidate_count": feasible,
        "incumbent_squared_tracking_error": round(incumbent_error, 15),
        "objective_bound_position_error_radius": round(error_radius, 15),
        "target_count": len(symbols),
        "equity_basis": round(equity, 10),
        "target_cash_weight": round(target_cash, 10),
        "minimum_cash_weight": round(minimum_cash, 10),
        "projected_cash": round(float(record["cash"]), 10),
        "projected_cash_weight": round(float(record["cash_weight"]), 10),
        "projected_cash_drift": round(float(record["cash_drift"]), 10),
        "squared_tracking_error": round(float(record["squared_tracking_error"]), 15),
        "absolute_tracking_error": round(float(record["absolute_tracking_error"]), 15),
        "projected_turnover": round(float(record["turnover"]), 10),
        "projected_order_count": len(record["transition_rows"]),
        "pretrade_holdings": [
            {
                "symbol": symbol,
                "quantity": round(float(quantity), 10),
            }
            for symbol, quantity in sorted(holdings_by_symbol.items())
        ],
        "allocation": allocation_rows,
        "policy": normalized_policy,
    }
    return (
        pd.DataFrame(record["transition_rows"], columns=TRADE_COLUMNS),
        seal_whole_share_proof(evidence),
    )
