"""Trading audit utilities — holding period, slippage, and turnover analysis."""

from __future__ import annotations

from datetime import date as _date
from typing import Any


def calculate_holding_period(trades_by_date: dict) -> dict[str, Any]:
    """Return avg holding period = total span / num rebalances."""
    dates = sorted(trades_by_date.keys())
    if len(dates) < 2:
        return {
            "status": "N/A",
            "avg_holding_days": None,
            "reason": "Insufficient rebalance dates (need at least 2)",
        }
    parsed = []
    for d in dates:
        try:
            parsed.append(_date.fromisoformat(d))
        except ValueError:
            continue
    if len(parsed) < 2:
        return {"status": "N/A", "avg_holding_days": None, "reason": "Unparseable dates"}
    total_days = (parsed[-1] - parsed[0]).days
    avg = total_days / len(parsed)
    return {
        "status": "OK",
        "avg_holding_days": round(avg, 1),
        "num_rebalances": len(parsed),
        "period_start": parsed[0].isoformat(),
        "period_end": parsed[-1].isoformat(),
        "total_span_days": total_days,
    }


def calculate_slippage_table(trades: list[dict]) -> dict[str, Any]:
    """Generate slippage sensitivity table for bps levels {0, 5, 10, 20}."""
    if not trades:
        return {"status": "N/A", "table": None}
    total_notional = sum(abs(float(t.get("notional", 0))) for t in trades)
    actual_fees = sum(float(t.get("fees", 0)) for t in trades)
    if total_notional == 0:
        return {"status": "N/A", "table": None}
    sensitivity_table: dict[int, float] = {}
    for bps in (0, 5, 10, 20):
        slippage_cost = total_notional * bps / 10_000
        total_cost = slippage_cost + actual_fees
        sensitivity_table[bps] = round(total_cost / total_notional * 100, 6)
    return {
        "status": "OK",
        "total_notional": total_notional,
        "actual_fees": actual_fees,
        "sensitivity_table": sensitivity_table,
    }


def group_trades_by_date(trades: list[dict]) -> dict[str, list[dict]]:
    """Group a flat trade list by trade_date, returning a sorted dict."""
    grouped: dict[str, list[dict]] = {}
    for t in trades:
        d = str(t.get("trade_date", ""))
        if d:
            grouped.setdefault(d, []).append(t)
    return dict(sorted(grouped.items()))


def calculate_turnover(trades_by_date: dict) -> dict[str, Any]:
    """Compute annualised turnover and weekly stats from trades_by_date dict."""
    if not trades_by_date:
        return {"status": "N/A", "annualized_turnover_pct": None}
    weekly_notionals: list[float] = []
    for trades in trades_by_date.values():
        week_notional = sum(abs(float(t.get("notional", 0))) for t in trades)
        weekly_notionals.append(week_notional)
    if not weekly_notionals:
        return {"status": "N/A", "annualized_turnover_pct": None}
    mean_weekly = sum(weekly_notionals) / len(weekly_notionals)
    sorted_w = sorted(weekly_notionals)
    n = len(sorted_w)
    median_weekly = sorted_w[n // 2] if n % 2 else (sorted_w[n // 2 - 1] + sorted_w[n // 2]) / 2
    p90_idx = max(0, int(n * 0.90) - 1)
    p90_weekly = sorted_w[p90_idx]
    annualized = mean_weekly * 52
    return {
        "status": "OK",
        "annualized_turnover_pct": round(annualized, 2),
        "weekly_stats": {
            "mean": round(mean_weekly, 2),
            "median": round(median_weekly, 2),
            "p90": round(p90_weekly, 2),
        },
        "num_weeks": n,
    }


def main() -> int:
    print("trading_audit: calculate_holding_period | calculate_slippage_table | calculate_turnover")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
