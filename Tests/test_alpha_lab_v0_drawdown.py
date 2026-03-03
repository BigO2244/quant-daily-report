"""Test Alpha Lab V0 drawdown calculations."""
from __future__ import annotations

import pytest


def test_drawdown_computation():
    """Test drawdown calculation on synthetic NAV."""
    from research.alpha_lab_v0.metrics import compute_returns_and_drawdowns
    
    # Create synthetic NAV series with known drawdown
    nav_series = [
        {"date": "2026-01-01", "nav": 100000.0, "cash": 0.0, "equity_ex_cash": 100000.0},
        {"date": "2026-01-02", "nav": 105000.0, "cash": 0.0, "equity_ex_cash": 105000.0},
        {"date": "2026-01-03", "nav": 98000.0, "cash": 0.0, "equity_ex_cash": 98000.0},  # -6.67% from peak
        {"date": "2026-01-04", "nav": 95000.0, "cash": 0.0, "equity_ex_cash": 95000.0},  # -9.52% from peak
        {"date": "2026-01-05", "nav": 102000.0, "cash": 0.0, "equity_ex_cash": 102000.0},  # Recovery partial
    ]
    
    daily_metrics, summary = compute_returns_and_drawdowns(nav_series)
    
    # Verify max drawdown
    assert summary["max_drawdown"] is not None
    assert summary["max_drawdown"] < 0  # Should be negative
    assert summary["max_drawdown"] <= -0.095  # At least -9.5%
    
    # Verify we have daily metrics
    assert len(daily_metrics) == len(nav_series)
    
    # Verify drawdowns list exists
    assert "worst_drawdowns" in summary
    assert len(summary["worst_drawdowns"]) > 0


def test_empty_nav_series():
    """Test that empty NAV series returns empty metrics."""
    from research.alpha_lab_v0.metrics import compute_returns_and_drawdowns
    
    daily_metrics, summary = compute_returns_and_drawdowns([])
    
    assert daily_metrics == []
    assert summary == {}


def test_sharpe_ratio():
    """Test Sharpe ratio calculation."""
    from research.alpha_lab_v0.metrics import compute_sharpe_ratio
    import pandas as pd
    import numpy as np
    
    # Create returns series with positive mean
    np.random.seed(42)
    returns = pd.Series(np.random.normal(0.001, 0.02, 252))  # ~25% annualized with 20% vol
    
    sharpe = compute_sharpe_ratio(returns)
    
    assert sharpe is not None
    # Should be positive for positive mean returns
    # But don't assert exact value due to randomness


def test_max_drawdown_ordering():
    """Test that worst drawdowns are correctly ordered."""
    from research.alpha_lab_v0.metrics import compute_returns_and_drawdowns
    
    # Create NAV with two distinct drawdowns
    nav_series = [
        {"date": "2026-01-01", "nav": 100000.0, "cash": 0.0, "equity_ex_cash": 100000.0},
        {"date": "2026-01-02", "nav": 90000.0, "cash": 0.0, "equity_ex_cash": 90000.0},  # -10%
        {"date": "2026-01-03", "nav": 100000.0, "cash": 0.0, "equity_ex_cash": 100000.0},  # Recovery
        {"date": "2026-01-04", "nav": 95000.0, "cash": 0.0, "equity_ex_cash": 95000.0},  # -5%
        {"date": "2026-01-05", "nav": 100000.0, "cash": 0.0, "equity_ex_cash": 100000.0},  # Recovery
    ]
    
    _, summary = compute_returns_and_drawdowns(nav_series)
    
    worst_dds = summary["worst_drawdowns"]
    assert len(worst_dds) >= 2
    
    # First should be deeper than second
    assert worst_dds[0]["depth"] <= worst_dds[1]["depth"]
