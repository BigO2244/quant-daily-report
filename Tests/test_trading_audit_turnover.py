"""Tests for trading audit turnover calculation."""

from __future__ import annotations

import pytest
from trading_audit import calculate_turnover, group_trades_by_date


def test_turnover_empty_trades() -> None:
    """Turnover should return N/A for empty trades."""
    result = calculate_turnover({})

    assert result["status"] == "N/A"
    assert result["annualized_turnover_pct"] is None


def test_turnover_single_date() -> None:
    """Turnover should handle single trade date."""
    trades_by_date = {
        "2026-02-27": [
            {"notional": 1000, "quantity": 10, "fill_price": 100},
            {"notional": 500, "quantity": 5, "fill_price": 100},
        ]
    }

    result = calculate_turnover(trades_by_date)

    assert result["status"] == "OK"
    assert result["annualized_turnover_pct"] is not None
    assert result["annualized_turnover_pct"] > 0


def test_turnover_multiple_dates() -> None:
    """Turnover should aggregate weekly data."""
    trades_by_date = {
        "2026-02-13": [{"notional": 1000}],
        "2026-02-20": [{"notional": 1500}],
        "2026-02-27": [{"notional": 1200}],
    }

    result = calculate_turnover(trades_by_date)

    assert result["status"] == "OK"
    assert "weekly_stats" in result
    assert "mean" in result["weekly_stats"]
    assert "median" in result["weekly_stats"]
    assert "p90" in result["weekly_stats"]


def test_turnover_weekly_stats_ordering() -> None:
    """Weekly stats should be consistent across runs (deterministic)."""
    trades_by_date = {
        "2026-02-13": [{"notional": 1000}],
        "2026-02-14": [{"notional": 1100}],
        "2026-02-15": [{"notional": 900}],
        "2026-02-20": [{"notional": 1500}],
        "2026-02-27": [{"notional": 1200}],
    }

    result1 = calculate_turnover(trades_by_date)
    result2 = calculate_turnover(trades_by_date)

    # Should produce identical results (deterministic)
    assert result1 == result2


def test_group_trades_by_date_sorting() -> None:
    """Group trades by date should be deterministically sorted."""
    trades = [
        {"trade_date": "2026-02-27", "notional": 100},
        {"trade_date": "2026-02-13", "notional": 200},
        {"trade_date": "2026-02-20", "notional": 150},
    ]

    result = group_trades_by_date(trades)

    dates = list(result.keys())
    assert dates == ["2026-02-13", "2026-02-20", "2026-02-27"]


def test_turnover_with_negative_notional() -> None:
    """Turnover should treat sells (negative) as absolute value."""
    trades_by_date = {
        "2026-02-27": [
            {"notional": 1000, "side": "BUY"},
            {"notional": -500, "side": "SELL"},
        ]
    }

    result = calculate_turnover(trades_by_date)

    assert result["status"] == "OK"
    # Both buys and sells count toward turnover (absolute values)
    assert result["annualized_turnover_pct"] is not None
