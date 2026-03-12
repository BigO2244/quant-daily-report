from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import daily_quant_report as dqr


def test_market_closed_alpaca_run_uses_planner_trade_plan_count_for_summary(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPORT_DATE", "2026-03-12")
    monkeypatch.setenv("MODE", "alpaca")
    monkeypatch.setenv("TRADING_MODE", "alpaca")
    monkeypatch.setenv("ALPACA_PAPER", "1")
    monkeypatch.setenv("RECON_V2", "1")
    step_summary_path = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary_path))
    monkeypatch.setattr(dqr, "_today_et_str", lambda: "2026-03-12")

    monkeypatch.setattr(dqr, "run_sleeve_1", lambda: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(
        dqr,
        "run_sleeve_trend",
        lambda: (
            pd.DataFrame(
                {
                    "date": pd.to_datetime(["2026-03-11", "2026-03-12"]),
                    "equity": [10000.0, 10050.0],
                }
            ),
            pd.DataFrame(),
            pd.DataFrame(),
        ),
    )
    monkeypatch.setattr(
        dqr,
        "run_sleeve_2",
        lambda: {
            "equity_df": pd.DataFrame(
                {
                    "date": pd.to_datetime(["2026-03-11", "2026-03-12"]),
                    "equity": [10000.0, 10025.0],
                }
            ),
            "trades_df": pd.DataFrame(),
            "target_weights": pd.DataFrame(),
        },
    )

    def _fake_prices(tickers, period="6mo", interval="1d"):
        rows = []
        for ticker in tickers:
            for date in pd.to_datetime(["2026-03-11", "2026-03-12"]):
                rows.append(
                    {
                        "date": date,
                        "ticker": ticker,
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.0,
                        "volume": 1_000_000,
                    }
                )
        return pd.DataFrame(rows)

    monkeypatch.setattr(dqr, "download_prices", _fake_prices)
    monkeypatch.setattr(
        dqr,
        "load_benchmark_prices",
        lambda **kwargs: pd.Series(
            [500.0, 501.0],
            index=pd.to_datetime(["2026-03-11", "2026-03-12"]),
        ),
    )
    monkeypatch.setattr(dqr, "compute_alpha_attribution", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dqr,
        "_capture_pretrade_broker_snapshot",
        lambda **kwargs: {"snapshot": {"ok": True}},
    )
    monkeypatch.setattr(
        dqr,
        "pre_trade_reconcile_and_classify",
        lambda **kwargs: {"reconciliation_decision": "SELF_HEAL"},
    )
    monkeypatch.setattr(
        dqr,
        "refresh_canonical_snapshot_from_posttrade_snapshot",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(dqr, "refresh_canonical_snapshot_from_broker", lambda **kwargs: True)
    monkeypatch.setattr(dqr, "post_trade_validate", lambda **kwargs: None)

    signals_path = tmp_path / "signals" / "2026-03-12.json"

    def _fake_daily_snapshot(**kwargs):
        signals_path.parent.mkdir(parents=True, exist_ok=True)
        signals_path.write_text("{\"signals\": []}\n", encoding="utf-8")
        return {
            "signals_snapshot_path": str(signals_path),
            "proposed_trades": [
                {"ticker": "AAPL", "side": "BUY", "shares": 1, "price": 100.0},
                {"ticker": "MSFT", "side": "SELL", "shares": 2, "price": 200.0},
            ],
            "risk_levels": [],
            "holdings": [],
            "performance_summary": {},
            "performance_diagnostics": {},
            "inception_metrics": {},
            "allocation_diagnostics": {},
            "nav_metrics": {},
            "s2_no_picks": False,
        }

    monkeypatch.setattr(dqr, "build_daily_snapshot", _fake_daily_snapshot)
    monkeypatch.setattr(
        dqr,
        "run_paper_day",
        lambda **kwargs: {
            "date": "2026-03-12",
            "trading_mode": "ALPACA",
            "run_id": "r1",
            "blocked_reasons": ["market_guard_closed"],
            "market_status": "CLOSED",
            "market_guard": {"is_trading_session": False, "status": "CLOSED"},
            "total_equity": 10000.0,
            "cash": 10000.0,
            "num_trades": 0,
            "turnover_notional": 0.0,
            "turnover_pct": 0.0,
            "trade_plan": [
                {"ticker": "AAPL", "side": "BUY", "shares": 1, "price": 100.0}
            ],
            "signals": [],
        },
    )

    dqr.main([])
    captured = capsys.readouterr()

    latest_run = json.loads((tmp_path / "outputs" / "latest_run.json").read_text(encoding="utf-8"))
    run_root = Path(str(latest_run["run_root"]))
    execution_payload = json.loads((run_root / "execution_payload.json").read_text(encoding="utf-8"))
    operator_summary = json.loads((run_root / "operator_summary.json").read_text(encoding="utf-8"))
    step_summary = step_summary_path.read_text(encoding="utf-8")

    assert execution_payload["executable_trades_count"] == 1
    assert operator_summary["planner_completed"] is True
    assert operator_summary["execution_payload_written"] is True
    assert operator_summary["proposed_trades_count"] == 1
    assert "proposed=1" in captured.out
    assert "- proposed_trades_count: `1`" in step_summary
