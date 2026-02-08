import json
from pathlib import Path

import pandas as pd

import daily_quant_report as dqr
from sleeves.sleeve_charlie_munger import (
    compute_200w_sma,
    is_entry_signal,
    score_quality,
)


def test_ma_200w_calculation():
    series = pd.Series(range(1, 241), dtype=float)
    sma = compute_200w_sma(series, 200)
    assert sma.iloc[198] != sma.iloc[198]  # NaN before full window
    assert sma.iloc[199] == sum(range(1, 201)) / 200.0
    assert sma.iloc[239] == sum(range(41, 241)) / 200.0


def test_entry_band_logic():
    assert is_entry_signal(1.03, None, 0.05, False)
    assert is_entry_signal(0.97, None, 0.05, False)
    assert not is_entry_signal(1.08, None, 0.05, False)


def test_cross_above_logic():
    assert is_entry_signal(1.01, 0.99, 0.05, True)
    assert not is_entry_signal(0.99, 1.01, 0.05, True)


def test_quality_scoring():
    fundamentals_ok = {
        "market_cap": 20_000_000_000,
        "roic": 0.15,
        "roe": 0.2,
        "fcf_positive_years": 9,
        "net_debt_to_ebitda": 1.8,
    }
    score_ok, _ = score_quality(fundamentals_ok)
    assert score_ok == 100

    fundamentals_bad = {
        "market_cap": 5_000_000_000,
        "roic": 0.05,
        "roe": 0.08,
        "fcf_positive_years": 2,
        "net_debt_to_ebitda": 6.0,
    }
    score_bad, _ = score_quality(fundamentals_bad)
    assert score_bad == 0


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
            "benchmark": {"ticker": "SPY", "cumulative_return": 0.1, "max_drawdown": -0.2},
            "sleeve_stats": {"cumulative_return": 0.2},
        },
    )

    def fake_prices(tickers, period="6mo", interval="1d"):
        idx = pd.to_datetime(["2026-01-14", "2026-01-15", "2026-01-16"])
        rows = []
        for t in tickers:
            for d in idx:
                rows.append({"date": d, "ticker": t, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1_000_000})
        return pd.DataFrame(rows)

    monkeypatch.setattr(dqr, "download_prices", fake_prices)
    monkeypatch.setattr(dqr, "load_benchmark_prices", lambda **kwargs: pd.Series([500.0, 501.0], index=pd.to_datetime(["2026-01-15", "2026-01-16"])))
    monkeypatch.setattr(dqr, "calc_alpha_stats", lambda *args, **kwargs: None)
    monkeypatch.setattr(dqr, "run_paper_day", lambda **kwargs: {"date": "2026-01-16", "trading_mode": "SHADOW", "shadow_orders": [], "run_id": "r1", "blocked_reasons": ["market_guard_closed"], "market_status": "CLOSED", "total_equity": 10000.0, "cash": 10000.0, "num_trades": 0, "turnover_notional": 0.0})

    dqr.main([])

    signal_file = tmp_path / "signals" / "2026-01-16.json"
    assert signal_file.exists()
    payload = json.loads(signal_file.read_text())
    sleeves = {row.get("sleeve") for row in payload.get("signals", [])}
    assert "charlie_munger" in sleeves
