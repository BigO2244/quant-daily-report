import json
from pathlib import Path

import pandas as pd

import daily_quant_report as dqr


def test_daily_report_smoke_includes_charlie(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(dqr, "run_sleeve_1", lambda: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(dqr, "run_sleeve_trend", lambda: (pd.DataFrame(), pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(dqr, "run_sleeve_2", lambda: {"equity_df": pd.DataFrame(), "trades_df": pd.DataFrame(), "target_weights": pd.DataFrame()})

    dates = pd.to_datetime(["2026-01-02", "2026-01-09", "2026-01-16"])
    cm_target = pd.DataFrame({"AAPL": [0.0, 0.5, 0.5], "MSFT": [0.0, 0.5, 0.5]}, index=dates)
    cm_equity = pd.DataFrame({"date": dates, "equity": [10000.0, 10100.0, 10200.0]})
    monkeypatch.setattr(
        dqr,
        "run_sleeve_charlie_munger",
        lambda: {
            "equity_df": cm_equity,
            "trades_df": pd.DataFrame(),
            "target_weights": cm_target,
            "asof": dates[-1],
            "signals": {"selected": [{"ticker": "AAPL"}], "sell": [], "meta": {"near_ma_candidates": 3}},
        },
    )
    monkeypatch.setattr(dqr, "download_prices", lambda *args, **kwargs: pd.DataFrame([{"date": dates[-1], "ticker": "AAPL", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1_000_000}]))
    monkeypatch.setattr(dqr, "load_benchmark_prices", lambda **kwargs: pd.Series([500.0], index=pd.to_datetime(["2026-01-16"])))
    monkeypatch.setattr(dqr, "calc_alpha_stats", lambda *args, **kwargs: None)
    monkeypatch.setattr(dqr, "run_paper_day", lambda **kwargs: {"date": "2026-01-16", "trading_mode": "SHADOW", "shadow_orders": [], "run_id": "r1", "blocked_reasons": ["market_guard_closed"], "market_status": "CLOSED", "total_equity": 10000.0, "cash": 10000.0, "num_trades": 0, "turnover_notional": 0.0})

    dqr.main([])

    signal_file = tmp_path / "signals" / "2026-01-16.json"
    assert signal_file.exists()
    payload = json.loads(signal_file.read_text())
    sleeves = {row.get("sleeve") for row in payload.get("signals", [])}
    assert "charlie_munger" in sleeves
