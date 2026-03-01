"""Tests for trading audit slippage sensitivity analysis."""

from __future__ import annotations

import pytest
from trading_audit import calculate_slippage_table


def test_slippage_empty_trades() -> None:
    """Slippage should return N/A for empty trades."""
    result = calculate_slippage_table([])

    assert result["status"] == "N/A"
    assert result["table"] is None


def test_slippage_basic() -> None:
    """Slippage should generate sensitivity table for standard bps levels."""
    trades = [
        {"notional": 10000, "fees": 10},
        {"notional": 5000, "fees": 5},
    ]

    result = calculate_slippage_table(trades)

    assert result["status"] == "OK"
    assert result["total_notional"] == 15000
    assert result["actual_fees"] == 15
    assert "sensitivity_table" in result
    assert set(result["sensitivity_table"].keys()) == {0, 5, 10, 20}


def test_slippage_zero_bps() -> None:
    """Slippage table 0 bps should only include actual fees."""
    trades = [
        {"notional": 10000, "fees": 50},
    ]

    result = calculate_slippage_table(trades)

    assert result["status"] == "OK"
    # 0 bps: 50 / 10000 * 100 = 0.5%
    assert result["sensitivity_table"][0] == 0.5


def test_slippage_sensitivity_increases() -> None:
    """Slippage costs should increase with bps levels."""
    trades = [
        {"notional": 100000, "fees": 0},
    ]

    result = calculate_slippage_table(trades)

    table = result["sensitivity_table"]
    assert table[0] < table[5] < table[10] < table[20]


def test_slippage_deterministic() -> None:
    """Slippage calculation should be deterministic."""
    trades = [
        {"notional": 10000, "fees": 25},
        {"notional": 15000, "fees": 40},
        {"notional": 5000, "fees": 10},
    ]

    result1 = calculate_slippage_table(trades)
    result2 = calculate_slippage_table(trades)

    assert result1 == result2


def test_slippage_cost_formula() -> None:
    """Verify slippage calculation formula: (notional * bps / 10000 + actual_fees) / notional * 100."""
    trades = [
        {"notional": 100000, "fees": 100},
    ]

    result = calculate_slippage_table(trades)

    # At 10 bps: (100000 * 10 / 10000 + 100) / 100000 * 100 = 110 / 100000 * 100 = 0.11%
    expected_10bps = (100000 * 10 / 10000 + 100) / 100000 * 100
    assert abs(result["sensitivity_table"][10] - expected_10bps) < 0.001


def test_slippage_multiple_trades() -> None:
    """Slippage should aggregate total notional across trades."""
    trades = [
        {"notional": 10000, "fees": 20},
        {"notional": 20000, "fees": 30},
        {"notional": 5000, "fees": 10},
    ]

    result = calculate_slippage_table(trades)

    assert result["total_notional"] == 35000
    assert result["actual_fees"] == 60
