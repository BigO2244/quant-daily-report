import json
from pathlib import Path

import pandas as pd

import daily_quant_report as dqr


def test_daily_report_smoke_runs_without_charlie(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(dqr, "run_sleeve_1", lambda: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(dqr, "run_sleeve_trend", lambda: (pd.DataFrame(), pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(dqr, "run_sleeve_2", lambda: {"equity_df": pd.DataFrame(), "trades_df": pd.DataFrame(), "target_weights": pd.DataFrame()})

    dates = pd.to_datetime(["2026-01-02", "2026-01-09", "2026-01-16"])
    cm_target = pd.DataFrame({"AAPL": [0.0, 0.5, 0.5], "MSFT": [0.0, 0.5, 0.5]}, index=dates)
    cm_equity = pd.DataFrame({"date": dates, "equity": [10000.0, 10100.0, 10200.0]})
    monkeypatch.setattr(dqr, "download_prices", lambda *args, **kwargs: pd.DataFrame([{"date": dates[-1], "ticker": "AAPL", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1_000_000}]))
    monkeypatch.setattr(dqr, "load_benchmark_prices", lambda **kwargs: pd.Series([500.0], index=pd.to_datetime(["2026-01-16"])))
    monkeypatch.setattr(dqr, "calc_alpha_stats", lambda *args, **kwargs: None)
    monkeypatch.setattr(dqr, "run_paper_day", lambda **kwargs: {"date": "2026-01-16", "trading_mode": "SHADOW", "shadow_orders": [], "run_id": "r1", "blocked_reasons": ["market_guard_closed"], "market_status": "CLOSED", "total_equity": 10000.0, "cash": 10000.0, "num_trades": 0, "turnover_notional": 0.0})

    dqr.main([])

    # should complete even with Charlie disabled


def test_charlie_quarterly_rebalance_qe_alias_maps_to_supported_rule(monkeypatch):
    import sleeves.sleeve_charlie_munger as cm

    captured = {}

    class DummyCfg:
        enabled = True
        target_holdings = 2
        min_holdings = 1
        benchmark = "SPY"
        ma_weeks = 2
        entry_band = 1.0
        use_cross_above = False
        weighting = "equal"
        max_weight_per_name = 1.0
        quality_min_score = 0.0
        allow_missing_fundamentals = True
        rebalance_freq = "QE"

    dates = pd.to_datetime(["2026-01-02", "2026-01-09", "2026-01-16"])
    rows = []
    for ticker, closes in {"AAPL": [100, 101, 102], "MSFT": [200, 201, 202], "SPY": [300, 301, 302]}.items():
        for d, c in zip(dates, closes):
            rows.append({"date": d, "ticker": ticker, "close": c})
    price_df = pd.DataFrame(rows)

    monkeypatch.setattr(cm, "load_config", lambda: DummyCfg())
    monkeypatch.setattr(cm, "_fetch_sp500_universe", lambda: ["AAPL", "MSFT"])
    monkeypatch.setattr(cm, "_download_prices", lambda *args, **kwargs: price_df)
    monkeypatch.setattr(cm, "_daily_to_weekly", lambda x: x)
    monkeypatch.setattr(cm, "compute_200w_sma", lambda series, window=200: series.rolling(window=2, min_periods=2).mean())
    monkeypatch.setattr(cm, "_fetch_fundamentals", lambda t: {})

    def fake_engine_run_backtest(**kwargs):
        captured["rebal_rule"] = kwargs.get("rebal_rule")
        return {"equity_curve": pd.DataFrame(), "trades": pd.DataFrame(), "weights": pd.DataFrame()}

    monkeypatch.setattr(cm, "engine_run_backtest", fake_engine_run_backtest)

    cm.run_backtest_with_details()

    assert captured["rebal_rule"] == "Q"
