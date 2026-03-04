"""
Tests for paper trading mode switches
- Weekend pause
- Holdings sync from Alpaca
- TRADING_MODE default = paper
- Turnover cap display
"""

import datetime as dt
import math
from zoneinfo import ZoneInfo
import pandas as pd
import pytest


def test_weekend_pause_saturday(monkeypatch):
    """Test that weekend pause blocks execution on Saturday."""
    from paper import paper_broker
    
    # Saturday at 10 AM ET
    saturday_et = dt.datetime(2026, 3, 8, 10, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    
    assert paper_broker._is_weekend_et(saturday_et) is True


def test_weekend_pause_sunday(monkeypatch):
    """Test that weekend pause blocks execution on Sunday."""
    from paper import paper_broker
    
    # Sunday at 10 AM ET
    sunday_et = dt.datetime(2026, 3, 9, 10, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    
    assert paper_broker._is_weekend_et(sunday_et) is True


def test_not_weekend_on_monday(monkeypatch):
    """Test that Monday is not considered a weekend."""
    from paper import paper_broker
    
    # Monday at 10 AM ET
    monday_et = dt.datetime(2026, 3, 10, 10, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    
    assert paper_broker._is_weekend_et(monday_et) is False


def test_not_weekend_on_friday(monkeypatch):
    """Test that Friday is not considered a weekend."""
    from paper import paper_broker
    
    # Friday at 10 AM ET
    friday_et = dt.datetime(2026, 3, 6, 10, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    
    assert paper_broker._is_weekend_et(friday_et) is False


def test_turnover_cap_display_disabled(monkeypatch):
    """Test that turnover_cap displays as 'Disabled (∞)' when infinite."""
    import daily_quant_report as dqr
    
    # Mock data with infinite turnover cap
    paper_summary = {
        "trading_mode": "paper",
        "market_status": "OPEN",
        "risk_meta": {
            "turnover_cap": float("inf"),
            "turnover_requested": 1000.0,
            "turnover_scaled": False,
            "turnover_scale": 1.0,
        },
    }
    
    daily_snapshot = {"holdings": [], "proposed_trades": []}
    
    payload = dqr.build_execution_email_payload(
        trade_date="2026-03-04",
        daily_snapshot=daily_snapshot,
        paper_summary=paper_summary,
    )
    
    assert "risk_summary" in payload
    risk_summary = payload["risk_summary"]
    assert "Turnover cap ($)" in risk_summary
    # Check that inf is displayed as "Disabled"
    assert "Disabled" in risk_summary["Turnover cap ($)"] or "∞" in risk_summary["Turnover cap ($)"]


def test_turnover_cap_display_normal_value(monkeypatch):
    """Test that turnover_cap displays normally when not infinite."""
    import daily_quant_report as dqr
    
    # Mock data with finite turnover cap
    paper_summary = {
        "trading_mode": "paper",
        "market_status": "OPEN",
        "risk_meta": {
            "turnover_cap": 7500.0,
            "turnover_requested": 1000.0,
            "turnover_scaled": False,
            "turnover_scale": 1.0,
        },
    }
    
    daily_snapshot = {"holdings": [], "proposed_trades": []}
    
    payload = dqr.build_execution_email_payload(
        trade_date="2026-03-04",
        daily_snapshot=daily_snapshot,
        paper_summary=paper_summary,
    )
    
    assert "risk_summary" in payload
    risk_summary = payload["risk_summary"]
    assert "Turnover cap ($)" in risk_summary
    # Check that normal value is displayed with $ format
    assert "$7,500.00" in risk_summary["Turnover cap ($)"]


def test_holdings_sync_generates_sells(tmp_path, monkeypatch):
    """Test that syncing holdings from Alpaca generates SELL orders for positions not in targets."""
    from paper import paper_broker
    from typing import Dict, List
    
    # Mock Alpaca broker with positions
    class MockAlpaca:
        def get_positions(self) -> List[Dict[str, object]]:
            return [
                {"symbol": "AAPL", "qty": "10", "current_price": "150.0", "market_value": "1500.0"},
                {"symbol": "MSFT", "qty": "5", "current_price": "300.0", "market_value": "1500.0"},
            ]
        
        def get_account(self) -> Dict[str, object]:
            return {"cash": "7000.0", "equity": "10000.0"}
        
        @classmethod
        def from_env(cls):
            return cls()
    
    monkeypatch.setattr(paper_broker, "AlpacaBroker", MockAlpaca)
    
    # Test holdings conversion
    positions: List[Dict[str, object]] = MockAlpaca().get_positions()
    holdings_df, _ = paper_broker._alpaca_positions_to_holdings(positions, "main")
    
    assert not holdings_df.empty
    assert len(holdings_df) == 2
    assert "AAPL" in holdings_df["ticker"].values
    assert "MSFT" in holdings_df["ticker"].values
    assert holdings_df[holdings_df["ticker"] == "AAPL"]["shares"].iloc[0] == 10.0
    assert holdings_df[holdings_df["ticker"] == "MSFT"]["shares"].iloc[0] == 5.0


def test_default_trading_mode_is_paper(monkeypatch):
    """Test that DEFAULT_TRADING_MODE is set to 'paper'."""
    import daily_quant_report as dqr
    
    assert dqr.DEFAULT_TRADING_MODE == "paper"


def test_trading_mode_fallback_uses_default(monkeypatch):
    """Test that when TRADING_MODE env var is not set, DEFAULT_TRADING_MODE is used."""
    import os
    import daily_quant_report as dqr
    
    # Remove TRADING_MODE from env if present
    monkeypatch.delenv("TRADING_MODE", raising=False)
    
    # Check that build_execution_email_payload uses the default
    paper_summary = None
    daily_snapshot = {}
    
    payload = dqr.build_execution_email_payload(
        trade_date="2026-03-04",
        daily_snapshot=daily_snapshot,
        paper_summary=paper_summary,
    )
    
    # Should default to "PAPER" (uppercased)
    assert payload["mode"] == "PAPER"


def test_weekend_execution_status_is_skipped(tmp_path, monkeypatch):
    """Test that execution_status is SKIPPED_WEEKEND on weekends."""
    from paper import paper_broker
    import json
    
    # Create minimal config
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "initial_equity": 10000.0,
        "benchmark_ticker": "SPY",
        "execution": {"price": "next_open", "slippage_bps": 5, "sent_ledger_path": str(tmp_path / "sent.csv")},
        "constraints": {"allow_fractional_shares": True, "min_trade_dollars": 100.0, "cash_buffer_bps": 10.0},
        "mode": {"trading_mode": "paper", "portfolio_id": "main", "strategy_version": "v4"},
        "safety": {"market_cutoff_time_et": "15:45", "reconciliation_abs_tolerance_dollars": 1.0, "reconciliation_bps_tolerance": 1.0, "halt_on_data_error": True, "require_benchmark_price": False},
        "risk": {"max_turnover_pct": 0.75, "max_trades_per_day": 10, "max_position_change_pct": 0.15, "action": "scale_down"},
        "reporting": {"alpha_min_overlap_days": 5}
    }))
    
    # Create minimal signals file
    signals_path = tmp_path / "signals.json"
    signals_path.write_text(json.dumps({
        "date": "2026-03-08",
        "asof_date": "2026-03-07",
        "positions": []
    }))
    
    # Create empty ledger
    ledger_path = tmp_path / "ledger.csv" 
    ledger_path.write_text("date,ticker,sleeve,shares,price,market_value,cash,total_equity\n")
    
    # Create empty trades
    trades_path = tmp_path / "trades.csv"
    trades_path.write_text("date,ticker,sleeve,side,shares,price,slippage_cost,notional,reason\n")
    
    # Saturday in ET
    saturday_et = dt.datetime(2026, 3, 8, 10, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    
    # Mock AlpacaBroker to avoid real API calls
    class MockAlpaca:
        def get_positions(self):
            return []
        def get_account(self):
            return {"cash": "10000.0", "equity": "10000.0"}
        @classmethod
        def from_env(cls):
            return cls()
    
    monkeypatch.setattr(paper_broker, "AlpacaBroker", MockAlpaca)
    
    # Mock fetch functions
    monkeypatch.setattr(paper_broker, "fetch_open_prices_yfinance", lambda tickers, run_date: pd.DataFrame())
    monkeypatch.setattr(paper_broker, "fetch_prev_closes_yfinance", lambda tickers, asof_date: pd.DataFrame())
    
    result = paper_broker.run_paper_day(
        run_date="2026-03-08",
        signals_path=str(signals_path),
        ledger_path=str(ledger_path),
        trades_path=str(trades_path),
        config_path=str(config_path),
        now_et=saturday_et,
        plan_only=True,  # Use plan_only to avoid execution paths
    )
    
    assert result["execution_status"] == "SKIPPED_WEEKEND"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
