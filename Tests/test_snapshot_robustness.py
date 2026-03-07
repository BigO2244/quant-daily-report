from types import SimpleNamespace

import pandas as pd

import daily_quant_report as dqr


def test_build_daily_snapshot_survives_stop_calc_error(monkeypatch):
    report_date = pd.Timestamp("2026-02-19")
    alloc_result = SimpleNamespace(
        combined_weights=pd.DataFrame(
            [{"ticker": "AAPL", "target_weight": 0.25, "sleeve_name": "sleeve_2"}]
        ),
        cash_weight=0.75,
        sleeve_allocations={"sleeve_2": 0.25},
        skipped_trades=[],
    )

    monkeypatch.setattr(dqr, "write_signals_snapshot", lambda **kwargs: "signals/2026-02-19.json")
    monkeypatch.setattr(dqr, "persist_signal_snapshot", lambda *args, **kwargs: None)

    def fake_prices(tickers, period="6mo", interval="1d"):
        return pd.DataFrame(
            [
                {
                    "date": report_date,
                    "ticker": "AAPL",
                    "open": 100.0,
                    "high": 102.0,
                    "low": 99.0,
                    "close": 101.0,
                }
            ]
        )

    monkeypatch.setattr(dqr, "download_prices", fake_prices)
    monkeypatch.setattr(dqr, "_build_atr_map", lambda prices, asof: {"AAPL": "bad_atr"})

    snapshot = dqr.build_daily_snapshot(
        report_date=report_date,
        alloc_result=alloc_result,
        portfolio_stats={"equity": 10000.0},
        st_equity=pd.DataFrame(),
        s2_equity=pd.DataFrame(),
        st_signals=pd.DataFrame(),
        s2_details={},
        cm_details={},
    )

    assert snapshot["risk_levels"][0]["ticker"] == "AAPL"
    assert snapshot["risk_levels"][0]["stop_loss"] is None
    assert snapshot["risk_levels"][0]["take_profit"] is None
    assert snapshot["risk_levels"][0]["atr"] is None
    assert "market_analyzer" in snapshot
    assert set(snapshot["market_analyzer"].keys()) >= {
        "date",
        "premarket_score",
        "bearish_flag",
        "signal_bucket",
        "analyzer_version",
        "notes",
    }
