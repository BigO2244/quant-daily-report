"""Test Alpha Lab V0 turnover calculations."""
from __future__ import annotations

import pytest


def test_turnover_computation():
    """Test turnover calculation with known trades and NAV."""
    from research.alpha_lab_v0.metrics import compute_turnover
    
    trades = [
        {
            "trade_date": "2026-01-05",
            "ticker": "AAPL",
            "side": "BUY",
            "qty": 100.0,
            "fill_price": 180.50
        },
        {
            "trade_date": "2026-01-05",
            "ticker": "MSFT",
            "side": "BUY",
            "qty": 80.0,
            "fill_price": 420.00
        }
    ]
    
    nav_series = [
        {"date": "2026-01-05", "nav": 100000.0, "cash": 50000.0, "equity_ex_cash": 50000.0}
    ]
    
    turnover = compute_turnover(trades, nav_series)
    
    assert len(turnover) == 1
    assert turnover[0]["date"] == "2026-01-05"
    
    # Turnover = (100 * 180.50 + 80 * 420.00) / 100000
    # = (18050 + 33600) / 100000 = 51650 / 100000 = 0.5165
    expected_turnover = (100 * 180.50 + 80 * 420.00) / 100000.0
    assert abs(turnover[0]["turnover"] - expected_turnover) < 0.001


def test_turnover_multiple_days():
    """Test turnover over multiple trading days."""
    from research.alpha_lab_v0.metrics import compute_turnover
    
    trades = [
        {"trade_date": "2026-01-05", "ticker": "AAPL", "side": "BUY", "qty": 100.0, "fill_price": 180.0},
        {"trade_date": "2026-01-06", "ticker": "AAPL", "side": "SELL", "qty": 50.0, "fill_price": 182.0},
    ]
    
    nav_series = [
        {"date": "2026-01-05", "nav": 100000.0, "cash": 82000.0, "equity_ex_cash": 18000.0},
        {"date": "2026-01-06", "nav": 101000.0, "cash": 91100.0, "equity_ex_cash": 9100.0},
    ]
    
    turnover = compute_turnover(trades, nav_series)
    
    assert len(turnover) == 2
    assert turnover[0]["date"] == "2026-01-05"
    assert turnover[1]["date"] == "2026-01-06"
    
    # Day 1: 100 * 180 / 100000 = 0.18
    assert abs(turnover[0]["turnover"] - 0.18) < 0.001
    
    # Day 2: 50 * 182 / 101000 ~= 0.0901
    assert abs(turnover[1]["turnover"] - (50 * 182 / 101000)) < 0.001


def test_empty_inputs():
    """Test turnover with empty inputs."""
    from research.alpha_lab_v0.metrics import compute_turnover
    
    result = compute_turnover([], [])
    assert result == []
    
    result = compute_turnover([{"trade_date": "2026-01-05", "ticker": "AAPL"}], [])
    assert result == []


def test_turnover_deterministic():
    """Test that turnover calculation is deterministic."""
    from research.alpha_lab_v0.metrics import compute_turnover
    
    trades = [
        {"trade_date": "2026-01-05", "ticker": "AAPL", "side": "BUY", "qty": 100.0, "fill_price": 150.0}
    ]
    
    nav_series = [
        {"date": "2026-01-05", "nav": 100000.0, "cash": 85000.0, "equity_ex_cash": 15000.0}
    ]
    
    result1 = compute_turnover(trades, nav_series)
    result2 = compute_turnover(trades, nav_series)
    
    assert result1 == result2
    assert result1[0]["turnover"] == result2[0]["turnover"]
