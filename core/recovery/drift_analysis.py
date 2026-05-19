from __future__ import annotations

from typing import Any


def _sum_abs(values: dict[str, float]) -> float:
    return sum(abs(float(value)) for value in values.values())


def analyze_portfolio_drift(
    *,
    current_positions: dict[str, float],
    target_positions: dict[str, float],
    current_market_values: dict[str, float] | None = None,
    target_market_values: dict[str, float] | None = None,
    current_cash: float | None = None,
    target_cash: float | None = None,
    current_equity: float | None = None,
) -> dict[str, Any]:
    current_market_values = dict(current_market_values or {})
    target_market_values = dict(target_market_values or {})
    rows: list[dict[str, Any]] = []
    missing_count = 0
    over_count = 0
    matched_count = 0
    total_abs_target_qty = _sum_abs(target_positions)
    total_abs_qty_gap = 0.0

    for symbol in sorted(set(current_positions) | set(target_positions)):
        current_qty = float(current_positions.get(symbol, 0.0))
        target_qty = float(target_positions.get(symbol, 0.0))
        delta_qty = target_qty - current_qty
        total_abs_qty_gap += abs(delta_qty)
        if abs(delta_qty) <= 1e-9:
            classification = "MATCH"
            matched_count += 1
        elif delta_qty > 0:
            classification = "MISSING_OR_UNDERWEIGHT"
            missing_count += 1
        else:
            classification = "OVERWEIGHT"
            over_count += 1
        rows.append(
            {
                "symbol": symbol,
                "current_qty": current_qty,
                "target_qty": target_qty,
                "delta_qty": delta_qty,
                "current_market_value": current_market_values.get(symbol),
                "target_market_value": target_market_values.get(symbol),
                "classification": classification,
            }
        )

    current_gross = sum(abs(float(value)) for value in current_market_values.values())
    target_gross = sum(abs(float(value)) for value in target_market_values.values())
    gross_exposure_drift = None
    cash_drift = None
    exposure_discontinuity_pct = None
    if current_equity:
        gross_exposure_drift = (target_gross - current_gross) / float(current_equity)
        if current_cash is not None and target_cash is not None:
            cash_drift = (target_cash - current_cash) / float(current_equity)
        exposure_discontinuity_pct = abs(gross_exposure_drift)

    target_convergence = 1.0
    if total_abs_target_qty > 0:
        target_convergence = max(0.0, 1.0 - (total_abs_qty_gap / total_abs_target_qty))

    intended_symbols = len([qty for qty in target_positions.values() if abs(float(qty)) > 1e-9])
    current_target_symbols = len(
        [
            symbol
            for symbol, qty in target_positions.items()
            if abs(float(qty)) > 1e-9 and abs(float(current_positions.get(symbol, 0.0))) > 1e-9
        ]
    )
    partial_rebalance_completeness = (
        current_target_symbols / intended_symbols if intended_symbols else 1.0
    )

    concentrations = {
        "current_max_position_pct": None,
        "target_max_position_pct": None,
    }
    if current_equity:
        concentrations["current_max_position_pct"] = max(
            (abs(float(value)) / float(current_equity) for value in current_market_values.values()),
            default=0.0,
        )
        concentrations["target_max_position_pct"] = max(
            (abs(float(value)) / float(current_equity) for value in target_market_values.values()),
            default=0.0,
        )

    return {
        "rows": rows,
        "missing_position_count": missing_count,
        "overweight_position_count": over_count,
        "matched_position_count": matched_count,
        "target_convergence_pct": round(target_convergence * 100.0, 6),
        "partial_rebalance_completeness_pct": round(partial_rebalance_completeness * 100.0, 6),
        "gross_exposure_drift_pct": gross_exposure_drift,
        "cash_drift_pct": cash_drift,
        "exposure_discontinuity_pct": exposure_discontinuity_pct,
        "concentration": concentrations,
    }

