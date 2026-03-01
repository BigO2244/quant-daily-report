"""Tests for trading audit holding period calculation."""

from __future__ import annotations

import pytest
from trading_audit import calculate_holding_period


def test_holding_period_empty() -> None:
    """Holding period should return N/A for empty trades."""
    result = calculate_holding_period({})

    assert result["status"] == "N/A"
    assert result["avg_holding_days"] is None


def test_holding_period_single_date() -> None:
    """Holding period should require multiple dates."""
    trades_by_date = {
        "2026-02-13": [{"notional": 1000}],
    }

    result = calculate_holding_period(trades_by_date)

    assert result["status"] == "N/A"
    assert "Insufficient" in result.get("reason", "")


def test_holding_period_basic() -> None:
    """Holding period should calculate days between first and last rebalance."""
    trades_by_date = {
        "2026-02-13": [{"notional": 1000}],
        "2026-02-20": [{"notional": 1500}],  # 7 days later
        "2026-02-27": [{"notional": 1200}],  # 14 days later
    }

    result = calculate_holding_period(trades_by_date)

    assert result["status"] == "OK"
    assert result["num_rebalances"] == 3
    assert result["period_start"] == "2026-02-13"
    assert result["period_end"] == "2026-02-27"
    # 14 days / 3 rebalances ≈ 4.67 days
    assert abs(result["avg_holding_days"] - 4.7) < 0.1


def test_holding_period_deterministic() -> None:
    """Holding period should be deterministic across runs."""
    trades_by_date = {
        "2026-02-13": [{"notional": 1000}],
        "2026-02-20": [{"notional": 1500}],
        "2026-02-27": [{"notional": 1200}],
        "2026-03-06": [{"notional": 1400}],
    }

    result1 = calculate_holding_period(trades_by_date)
    result2 = calculate_holding_period(trades_by_date)

    assert result1 == result2


def test_holding_period_date_parsing() -> None:
    """Holding period should correctly parse YYYY-MM-DD dates."""
    trades_by_date = {
        "2026-01-01": [{"notional": 100}],
        "2026-12-31": [{"notional": 200}],
    }

    result = calculate_holding_period(trades_by_date)

    assert result["status"] == "OK"
    # 364 days in 2026 (not a leap year)
    assert result["avg_holding_days"] == 182.0  # 364 days / 2 rebalances
