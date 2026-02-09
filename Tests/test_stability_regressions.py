import json
from pathlib import Path

import pandas as pd

import daily_quant_report as dqr
from paper.build_execution_email import build_execution_email_text


def test_main_prefers_patchable_charlie_runner_and_uses_inferred_trade_date(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)

    monkeypatch.delenv("REPORT_DATE", raising=False)
    monkeypatch.setattr(dqr, "run_sleeve_1", lambda: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(dqr, "run_sleeve_trend", lambda: (pd.DataFrame(), pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(
        dqr,
        "run_sleeve_2",
        lambda: {"equity_df": pd.DataFrame(), "trades_df": pd.DataFrame(), "target_weights": pd.DataFrame()},
    )

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
                rows.append(
                    {
                        "date": d,
                        "ticker": t,
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.0,
                        "volume": 1_000_000,
                    }
                )
        return pd.DataFrame(rows)

    monkeypatch.setattr(dqr, "download_prices", fake_prices)
    monkeypatch.setattr(
        dqr,
        "load_benchmark_prices",
        lambda **kwargs: pd.Series([500.0, 501.0], index=pd.to_datetime(["2026-01-15", "2026-01-16"])),
    )
    monkeypatch.setattr(dqr, "calc_alpha_stats", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dqr,
        "run_paper_day",
        lambda **kwargs: {
            "date": "2026-01-16",
            "trading_mode": "SHADOW",
            "shadow_orders": [],
            "run_id": "r1",
            "blocked_reasons": ["market_guard_closed"],
            "market_status": "CLOSED",
            "total_equity": 10000.0,
            "cash": 10000.0,
            "num_trades": 0,
            "turnover_notional": 0.0,
        },
    )

    dqr.main([])

    signal_file = tmp_path / "signals" / "2026-01-16.json"
    assert signal_file.exists()
    payload = json.loads(signal_file.read_text())
    sleeves = {row.get("sleeve") for row in payload.get("signals", [])}
    assert "charlie_munger" in sleeves


def test_shadow_zero_trades_payload_status_contract():
    payload = dqr.build_execution_email_payload(
        trade_date="2026-02-05",
        daily_snapshot={"risk_levels": [], "holdings": []},
        paper_summary={
            "trading_mode": "shadow",
            "shadow_orders": [],
            "blocked_reasons": ["max_position_change_pct exceeded ticker=ADBE"],
            "run_id": "rid",
        },
    )

    assert payload["execution_payload_status"] == "NOT GENERATED (Expected in SHADOW)"


def test_execution_email_has_blocked_ticker_summary_line():
    _, body = build_execution_email_text(
        {
            "trade_date": "2026-02-05",
            "mode": "SHADOW",
            "execution_status": "READY",
            "trades": [],
            "blocked_tickers": {"MMC": ["missing_open_prices"]},
            "execution_payload_status": "NOT GENERATED (Expected in SHADOW)",
        }
    )

    assert "• Execution Payload: NOT GENERATED (EXPECTED IN SHADOW)" in body
    assert "Blocked tickers: MMC (missing_open_prices)" in body
