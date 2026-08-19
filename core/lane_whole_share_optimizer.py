"""Pure, cash-aware nearest-feasible quantity realization for advisory lanes."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
from typing import Any, Mapping, Sequence


LANE_WHOLE_SHARE_REALIZATION_SCHEMA = "caerus.lane_whole_share_realization.v1"
DEFAULT_MAX_CANDIDATES = 5_000_000
_TOLERANCE = 1e-9


class LaneWholeShareOptimizerError(ValueError):
    """Raised when no exact governed realization can be proven."""


def canonical_json(payload: Any) -> str:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LaneWholeShareOptimizerError(str(exc)) from exc


def content_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _number(value: Any, *, label: str, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise LaneWholeShareOptimizerError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise LaneWholeShareOptimizerError(f"{label} must be numeric") from exc
    if not math.isfinite(result) or (nonnegative and result < 0.0):
        raise LaneWholeShareOptimizerError(f"{label} is outside the allowed range")
    return result


def _grid(current: float, step: float) -> tuple[float, int]:
    units = math.floor((current + _TOLERANCE) / step)
    remainder = current - units * step
    if remainder < _TOLERANCE:
        remainder = 0.0
    if step - remainder < _TOLERANCE:
        units += 1
        remainder = 0.0
    return remainder, units


def optimize_cash_aware_quantities(
    *,
    target_rows: Sequence[Mapping[str, Any]],
    starting_positions: Sequence[Mapping[str, Any]],
    marks: Mapping[str, float],
    buy_prices: Mapping[str, float],
    sell_prices: Mapping[str, float],
    equity: float,
    starting_cash: float,
    target_cash_weight: float,
    minimum_cash_weight: float,
    cash_target_tolerance_usd: float,
    quantity_precision: int,
    fee_per_order_usd: float,
    minimum_order_notional_usd: float,
    maximum_order_notional_usd: float,
    maximum_total_buy_notional_usd: float,
    maximum_orders: int,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> dict[str, Any]:
    """Prove the globally best feasible precision-grid holdings in a bounded region."""

    nav = _number(equity, label="equity", nonnegative=True)
    cash0 = _number(starting_cash, label="starting_cash")
    if nav <= 0.0:
        raise LaneWholeShareOptimizerError("equity must be positive")
    if not isinstance(quantity_precision, int) or not 0 <= quantity_precision <= 6:
        raise LaneWholeShareOptimizerError("quantity_precision must be in [0, 6]")
    step = 10.0 ** (-quantity_precision)
    fee = _number(fee_per_order_usd, label="fee_per_order_usd", nonnegative=True)
    target_cash = _number(target_cash_weight, label="target_cash_weight", nonnegative=True)
    minimum_cash = _number(
        minimum_cash_weight, label="minimum_cash_weight", nonnegative=True
    )
    cash_tolerance = _number(
        cash_target_tolerance_usd,
        label="cash_target_tolerance_usd",
        nonnegative=True,
    )
    if target_cash > 1.0 or minimum_cash > target_cash:
        raise LaneWholeShareOptimizerError("cash weights are inconsistent")
    min_notional = _number(
        minimum_order_notional_usd,
        label="minimum_order_notional_usd",
        nonnegative=True,
    )
    max_notional = _number(
        maximum_order_notional_usd,
        label="maximum_order_notional_usd",
        nonnegative=True,
    )
    max_buys = _number(
        maximum_total_buy_notional_usd,
        label="maximum_total_buy_notional_usd",
        nonnegative=True,
    )
    if max_notional <= 0.0 or max_buys <= 0.0 or maximum_orders < 0:
        raise LaneWholeShareOptimizerError("order caps are invalid")

    targets: dict[str, dict[str, Any]] = {}
    for raw in target_rows:
        if not isinstance(raw, Mapping):
            raise LaneWholeShareOptimizerError("target row must be an object")
        symbol = str(raw.get("symbol") or "")
        if not symbol or symbol in targets:
            raise LaneWholeShareOptimizerError("target symbols are blank or duplicated")
        weight = _number(raw.get("target_weight"), label=f"{symbol}.target_weight")
        if weight <= 0.0:
            raise LaneWholeShareOptimizerError("target weights must be positive")
        targets[symbol] = copy.deepcopy(dict(raw))
    if not targets:
        raise LaneWholeShareOptimizerError("target rows must not be empty")
    if abs(sum(float(row["target_weight"]) for row in targets.values()) + target_cash - 1.0) > 1e-8:
        raise LaneWholeShareOptimizerError("target positions plus cash must equal one")

    current: dict[str, float] = {}
    starting_by_symbol: dict[str, Mapping[str, Any]] = {}
    for raw in starting_positions:
        if not isinstance(raw, Mapping):
            raise LaneWholeShareOptimizerError("starting position must be an object")
        symbol = str(raw.get("symbol") or "")
        if not symbol or symbol in current:
            raise LaneWholeShareOptimizerError("starting symbols are blank or duplicated")
        current[symbol] = _number(
            raw.get("quantity"), label=f"{symbol}.quantity", nonnegative=True
        )
        starting_by_symbol[symbol] = raw
    symbols = sorted(set(targets) | set(current))
    mark = {symbol: _number(marks.get(symbol), label=f"{symbol}.mark") for symbol in symbols}
    buy = {symbol: _number(buy_prices.get(symbol), label=f"{symbol}.buy_price") for symbol in symbols}
    sell = {symbol: _number(sell_prices.get(symbol), label=f"{symbol}.sell_price") for symbol in symbols}
    if any(value <= 0.0 for values in (mark, buy, sell) for value in values.values()):
        raise LaneWholeShareOptimizerError("all prices must be positive")

    grid: dict[str, tuple[float, int]] = {
        symbol: _grid(current.get(symbol, 0.0), step) for symbol in symbols
    }
    target_weights = {
        symbol: float(targets.get(symbol, {}).get("target_weight", 0.0))
        for symbol in symbols
    }

    best: tuple[tuple[Any, ...], dict[str, Any]] | None = None
    evaluated = 0
    feasible = 0

    def quantity(symbol: str, units: int) -> float:
        remainder, _ = grid[symbol]
        return round(remainder + units * step, quantity_precision + 8)

    def consider(unit_vector: tuple[int, ...]) -> None:
        nonlocal best, evaluated, feasible
        evaluated += 1
        chosen = {symbol: quantity(symbol, units) for symbol, units in zip(symbols, unit_vector, strict=True)}
        transitions: list[dict[str, Any]] = []
        total_buy = 0.0
        projected_cash = cash0
        for symbol in symbols:
            delta = chosen[symbol] - current.get(symbol, 0.0)
            if abs(delta) <= _TOLERANCE:
                continue
            scaled_delta = delta / step
            if abs(scaled_delta - round(scaled_delta)) > 1e-7:
                return
            side = "BUY" if delta > 0.0 else "SELL"
            order_quantity = abs(delta)
            price = buy[symbol] if side == "BUY" else sell[symbol]
            notional = order_quantity * price
            if notional + _TOLERANCE < min_notional or notional > max_notional + _TOLERANCE:
                return
            cash_effect = -(notional + fee) if side == "BUY" else notional - fee
            projected_cash += cash_effect
            if side == "BUY":
                total_buy += notional + fee
            transitions.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "quantity": order_quantity,
                    "reference_price": mark[symbol],
                    "enforcement_price": price,
                    "estimated_fee": fee,
                    "notional": notional,
                    "cash_effect": cash_effect,
                }
            )
        if len(transitions) > maximum_orders or total_buy > max_buys + _TOLERANCE:
            return
        if projected_cash < -_TOLERANCE or projected_cash + _TOLERANCE < minimum_cash * nav:
            return
        feasible += 1
        position_drifts = {
            symbol: chosen[symbol] * mark[symbol] / nav - target_weights[symbol]
            for symbol in symbols
        }
        cash_drift = projected_cash / nav - target_cash
        squared = sum(value * value for value in position_drifts.values()) + cash_drift * cash_drift
        absolute = sum(abs(value) for value in position_drifts.values()) + abs(cash_drift)
        turnover = sum(row["notional"] + row["estimated_fee"] for row in transitions)
        key = (
            squared,
            absolute,
            turnover,
            len(transitions),
            tuple(chosen[symbol] for symbol in symbols),
        )
        record = {
            "chosen": chosen,
            "transitions": transitions,
            "projected_cash": projected_cash,
            "position_drifts": position_drifts,
            "cash_drift": cash_drift,
            "squared": squared,
            "absolute": absolute,
            "turnover": turnover,
        }
        if best is None or key < best[0]:
            best = (key, record)

    incumbent_options: list[tuple[int, ...]] = []
    for symbol in symbols:
        remainder, _ = grid[symbol]
        ideal = target_weights[symbol] * nav / mark[symbol]
        ideal_units = max(0.0, (ideal - remainder) / step)
        lower = max(0, math.floor(ideal_units + _TOLERANCE))
        upper = max(0, math.ceil(ideal_units - _TOLERANCE))
        incumbent_options.append((lower,) if lower == upper else (lower, upper))
    for vector in itertools.product(*incumbent_options):
        consider(tuple(int(value) for value in vector))
    # Adverse execution prices or fees can make every target floor/ceiling
    # point cash-infeasible even when holding or liquidating fewer shares is
    # feasible.  Deterministic safety incumbents keep the proof construction
    # fail-closed without assuming target proximity.
    current_vector = tuple(grid[symbol][1] for symbol in symbols)
    zero_vector = tuple(0 for _ in symbols)
    consider(current_vector)
    consider(zero_vector)
    for index in range(len(symbols)):
        single_liquidation = list(current_vector)
        single_liquidation[index] = 0
        consider(tuple(single_liquidation))
    progressive = list(current_vector)
    for index in range(len(symbols)):
        progressive[index] = 0
        consider(tuple(progressive))
    if best is None:
        raise LaneWholeShareOptimizerError(
            "no feasible target-grid or deterministic liquidation incumbent"
        )

    incumbent_error = float(best[1]["squared"])
    radius = math.sqrt(max(0.0, incumbent_error)) + 1e-12
    exact_options: list[tuple[int, ...]] = []
    for symbol in symbols:
        remainder, _ = grid[symbol]
        target_weight = target_weights[symbol]
        low_quantity = max(0.0, (target_weight - radius) * nav / mark[symbol])
        high_quantity = max(0.0, (target_weight + radius) * nav / mark[symbol])
        lower = max(0, math.ceil((low_quantity - remainder) / step - 1e-12))
        upper = max(0, math.floor((high_quantity - remainder) / step + 1e-12))
        if upper < lower:
            raise LaneWholeShareOptimizerError(
                f"objective proof bound has no quantity for {symbol}"
            )
        exact_options.append(tuple(range(lower, upper + 1)))
    search_count = math.prod(len(values) for values in exact_options)
    if search_count > max_candidates:
        raise LaneWholeShareOptimizerError(
            f"bounded optimizer search exceeds cap: {search_count}>{max_candidates}"
        )
    evaluated = 0
    feasible = 0
    for vector in itertools.product(*exact_options):
        consider(tuple(int(value) for value in vector))
    assert best is not None
    record = best[1]
    cash_target_usd = target_cash * nav
    cash_error_usd = float(record["projected_cash"]) - cash_target_usd
    allocations = []
    for symbol in symbols:
        source = targets.get(symbol)
        allocations.append(
            {
                "symbol": symbol,
                "target_weight": target_weights[symbol],
                "target_quantity": record["chosen"][symbol],
                "projected_weight": record["chosen"][symbol] * mark[symbol] / nav,
                "projected_weight_drift": record["position_drifts"][symbol],
                "sleeve_contributions": copy.deepcopy(
                    (source or starting_by_symbol[symbol]).get("sleeve_contributions", [])
                ),
            }
        )
    body: dict[str, Any] = {
        "schema_version": LANE_WHOLE_SHARE_REALIZATION_SCHEMA,
        "status": "PASS",
        "proof_method": "EXHAUSTIVE_PROVABLY_BOUNDED_PRECISION_GRID",
        "objective": "MINIMIZE_SQUARED_POSITION_AND_CASH_WEIGHT_TRACKING_ERROR",
        "tie_breakers": [
            "absolute_tracking_error",
            "turnover_including_fees",
            "order_count",
            "symbol_quantity_vector",
        ],
        "quantity_precision": quantity_precision,
        "quantity_step": step,
        "equity_basis": nav,
        "starting_cash": cash0,
        "target_cash_weight": target_cash,
        "minimum_cash_weight": minimum_cash,
        "cash_target_tolerance_usd": cash_tolerance,
        "cash_target_usd": cash_target_usd,
        "projected_cash": record["projected_cash"],
        "projected_cash_weight": record["projected_cash"] / nav,
        "cash_target_error_usd": cash_error_usd,
        "cash_target_within_tolerance": abs(cash_error_usd) <= cash_tolerance + _TOLERANCE,
        "cash_target_status": (
            "WITHIN_TOLERANCE"
            if abs(cash_error_usd) <= cash_tolerance + _TOLERANCE
            else "NEAREST_FEASIBLE_OUTSIDE_TOLERANCE"
        ),
        "fee_per_order_usd": fee,
        "candidate_count_evaluated": evaluated,
        "bounded_search_space_candidate_count": search_count,
        "feasible_candidate_count": feasible,
        "incumbent_squared_tracking_error": incumbent_error,
        "objective_bound_position_error_radius": radius,
        "squared_tracking_error": record["squared"],
        "absolute_tracking_error": record["absolute"],
        "projected_turnover_including_fees": record["turnover"],
        "projected_order_count": len(record["transitions"]),
        "target_rows_hash": content_hash(target_rows),
        "starting_positions_hash": content_hash(starting_positions),
        "marks": {symbol: mark[symbol] for symbol in symbols},
        "buy_prices": {symbol: buy[symbol] for symbol in symbols},
        "sell_prices": {symbol: sell[symbol] for symbol in symbols},
        "allocations": allocations,
        "transitions": record["transitions"],
    }
    body["content_hash"] = content_hash(body)
    return body


def validate_cash_aware_realization(payload: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    if payload.get("schema_version") != LANE_WHOLE_SHARE_REALIZATION_SCHEMA:
        failures.append("lane_whole_share:schema")
    if payload.get("status") != "PASS":
        failures.append("lane_whole_share:status")
    body = copy.deepcopy(dict(payload))
    declared = body.pop("content_hash", None)
    if declared != content_hash(body):
        failures.append("lane_whole_share:content_hash")
    precision = payload.get("quantity_precision")
    step = payload.get("quantity_step")
    transitions = payload.get("transitions")
    if not isinstance(precision, int) or step != 10.0 ** (-precision):
        failures.append("lane_whole_share:precision")
    elif not isinstance(transitions, list):
        failures.append("lane_whole_share:transitions")
    else:
        for row in transitions:
            quantity = float(row.get("quantity", -1.0)) if isinstance(row, Mapping) else -1.0
            if quantity <= 0.0 or abs(quantity / step - round(quantity / step)) > 1e-7:
                failures.append("lane_whole_share:order_quantity_precision")
    if float(payload.get("projected_cash", -1.0)) < -_TOLERANCE:
        failures.append("lane_whole_share:no_leverage")
    expected_tolerance = abs(float(payload.get("cash_target_error_usd", math.inf))) <= float(
        payload.get("cash_target_tolerance_usd", -1.0)
    ) + _TOLERANCE
    if payload.get("cash_target_within_tolerance") is not expected_tolerance:
        failures.append("lane_whole_share:cash_tolerance")
    expected_status = (
        "WITHIN_TOLERANCE"
        if expected_tolerance
        else "NEAREST_FEASIBLE_OUTSIDE_TOLERANCE"
    )
    if payload.get("cash_target_status") != expected_status:
        failures.append("lane_whole_share:cash_target_status")
    return sorted(set(failures))


__all__ = [
    "LANE_WHOLE_SHARE_REALIZATION_SCHEMA",
    "LaneWholeShareOptimizerError",
    "optimize_cash_aware_quantities",
    "validate_cash_aware_realization",
]
