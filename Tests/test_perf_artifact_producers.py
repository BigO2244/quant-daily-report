from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from paper.perf_artifact_producers import (
    build_benchmark_relative_series,
    build_concentration_history,
    build_construction_parity_artifact,
    build_trade_day_pnl_artifact,
    rebuild_premarket_analyzer_scores,
    update_benchmark_close_history,
)
from research.alpha_assessment.performance_layer_v1 import build_canonical_performance


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_benchmark_producer_schema_and_determinism(tmp_path: Path):
    out = tmp_path / "outputs" / "perf" / "benchmark_close_history.csv"

    idx = pd.to_datetime(["2026-02-24", "2026-02-25", "2026-02-25", "2026-02-27"])
    vals = pd.Series([687.72, 690.81, 690.81, 685.57], index=idx)

    def fake_fetch(_start: str, _end: str) -> pd.Series:
        return vals

    df1 = update_benchmark_close_history(
        asof_date="2026-02-27",
        output_path=out,
        inception_date="2026-02-23",
        fetch_spy_close_fn=fake_fetch,
    )
    df2 = update_benchmark_close_history(
        asof_date="2026-02-27",
        output_path=out,
        inception_date="2026-02-23",
        fetch_spy_close_fn=fake_fetch,
    )

    assert list(df1.columns) == ["date", "spy_close", "spy_return"]
    assert df1["date"].nunique() == len(df1)
    assert df1.equals(df2)


def test_benchmark_producer_carries_forward_to_non_trading_asof(tmp_path: Path):
    out = tmp_path / "outputs" / "perf" / "benchmark_close_history.csv"

    idx = pd.to_datetime(["2026-03-19", "2026-03-20"])
    vals = pd.Series([659.80, 648.57], index=idx)

    def fake_fetch(_start: str, _end: str) -> pd.Series:
        return vals

    df = update_benchmark_close_history(
        asof_date="2026-03-21",
        output_path=out,
        inception_date="2026-03-19",
        fetch_spy_close_fn=fake_fetch,
    )

    assert df["date"].tolist() == ["2026-03-19", "2026-03-20", "2026-03-21"]
    assert float(df.loc[df["date"] == "2026-03-21", "spy_close"].iloc[0]) == 648.57
    assert float(df.loc[df["date"] == "2026-03-21", "spy_return"].iloc[0]) == 0.0


def test_analyzer_producer_schema_and_one_row_per_date(tmp_path: Path):
    signals_dir = tmp_path / "signals"
    exec_dir = tmp_path / "outputs" / "execution_email"
    out = tmp_path / "outputs" / "perf" / "premarket_analyzer_scores.csv"

    _write_text(
        signals_dir / "2026-03-04.json",
        json.dumps(
            {
                "snapshot_date": "2026-03-04",
                "market_analyzer": {"score": 0.77, "signal_bucket": "RISK_ON", "version": "v1"},
                "breaker": {"mode": "off"},
            }
        ),
    )
    _write_text(
        exec_dir / "2026-03-04.json",
        json.dumps(
            {
                "trade_date": "2026-03-04",
                "breaker": {"mode": "partial", "exposure_label_today": "PARTIAL"},
            }
        ),
    )

    df = rebuild_premarket_analyzer_scores(
        signals_dir=signals_dir,
        execution_email_dir=exec_dir,
        output_path=out,
    )

    assert list(df.columns) == [
        "date",
        "premarket_score",
        "bearish_flag",
        "signal_bucket",
        "analyzer_version",
        "notes",
        "vix_component",
        "trend_component",
        "realized_vol_component",
        "gap_risk_component",
        "breadth_component",
        "macro_component",
    ]
    assert len(df) == 1
    assert df["date"].iloc[0] == "2026-03-04"
    assert abs(float(df["premarket_score"].iloc[0]) - 0.77) < 1e-12


def test_analyzer_score_falls_back_to_breaker_exposure(tmp_path: Path):
    signals_dir = tmp_path / "signals"
    out = tmp_path / "outputs" / "perf" / "premarket_analyzer_scores.csv"

    _write_text(
        signals_dir / "2026-03-05.json",
        json.dumps(
            {
                "snapshot_date": "2026-03-05",
                "breaker": {
                    "mode": "partial",
                    "exposure_label_today": "PARTIAL",
                    "exposure_multiplier_today": 0.5,
                },
            }
        ),
    )

    df = rebuild_premarket_analyzer_scores(
        signals_dir=signals_dir,
        execution_email_dir=tmp_path / "outputs" / "execution_email",
        output_path=out,
    )

    row = df[df["date"] == "2026-03-05"].iloc[0]
    assert abs(float(row["premarket_score"]) - 0.5) < 1e-12
    assert row["signal_bucket"] == "PARTIAL"


def test_producer_integration_with_canonical_layer(tmp_path: Path):
    _write_text(
        tmp_path / "outputs" / "perf" / "nav_timeseries.csv",
        "date,equity,return_1d,gross_exposure,net_exposure,cash,turnover\n"
        "2026-03-04,10000,0.00,0.50,0.50,5000,0.10\n",
    )
    _write_text(
        tmp_path / "outputs" / "perf" / "inception_nav_2026-03-04.csv",
        "date,model_nav,spy_nav\n"
        "2026-03-04,10000,20000\n",
    )
    _write_text(
        tmp_path / "outputs" / "vix_regime" / "regime_history.csv",
        "as_of,regime,vix,position_scale,max_positions\n"
        "2026-03-04,ELEVATED,22.0,0.75,7\n",
    )
    _write_text(tmp_path / "outputs" / "ledger" / "trades.csv", "trade_date,ticker,notional\n")
    _write_text(
        tmp_path / "signals" / "2026-03-04.json",
        json.dumps(
            {
                "snapshot_date": "2026-03-04",
                "breaker": {"mode": "partial", "exposure_label_today": "PARTIAL", "exposure_multiplier_today": 0.5},
                "market_analyzer": {"score": 0.66},
            }
        ),
    )

    idx = pd.to_datetime(["2026-03-04"])
    vals = pd.Series([612.34], index=idx)

    def fake_fetch(_start: str, _end: str) -> pd.Series:
        return vals

    update_benchmark_close_history(
        asof_date="2026-03-04",
        output_path=tmp_path / "outputs" / "perf" / "benchmark_close_history.csv",
        inception_date="2026-03-04",
        fetch_spy_close_fn=fake_fetch,
    )
    rebuild_premarket_analyzer_scores(
        signals_dir=tmp_path / "signals",
        execution_email_dir=tmp_path / "outputs" / "execution_email",
        output_path=tmp_path / "outputs" / "perf" / "premarket_analyzer_scores.csv",
    )

    df, _meta = build_canonical_performance(tmp_path)
    row = df[df["date"] == "2026-03-04"].iloc[0]

    assert abs(float(row["spy_close"]) - 612.34) < 1e-12
    assert abs(float(row["premarket_score"]) - 0.66) < 1e-12


def test_benchmark_relative_series_producer_outputs_expected_columns(tmp_path: Path):
    _write_text(
        tmp_path / "outputs" / "perf" / "nav_timeseries.csv",
        "date,equity,cash,gross_exposure,net_exposure,return_1d,turnover\n"
        "2026-04-01,10000,500,0.95,0.95,0.0000,0.10\n"
        "2026-04-02,10100,400,0.96,0.96,0.0100,0.12\n"
        "2026-04-03,10049.5,450,0.95,0.95,-0.0050,0.08\n",
    )
    _write_text(
        tmp_path / "outputs" / "perf" / "benchmark_close_history.csv",
        "date,spy_close,spy_return\n"
        "2026-04-01,500.0,0.0000\n"
        "2026-04-02,505.0,0.0100\n"
        "2026-04-03,507.525,0.0050\n",
    )

    out = tmp_path / "outputs" / "perf" / "benchmark_relative_series.csv"
    df = build_benchmark_relative_series(
        nav_timeseries_path=tmp_path / "outputs" / "perf" / "nav_timeseries.csv",
        benchmark_path=tmp_path / "outputs" / "perf" / "benchmark_close_history.csv",
        output_path=out,
    )

    assert out.exists()
    assert "excess_return" in df.columns
    assert "drawdown" in df.columns
    last = df.iloc[-1]
    assert abs(float(last["excess_return"]) - (-0.01)) < 1e-12
    assert abs(float(last["strategy_nav_indexed"]) - 100.495) < 1e-9


def test_concentration_history_and_parity_artifacts(tmp_path: Path):
    _write_text(
        tmp_path / "outputs" / "perf" / "nav_timeseries.csv",
        "date,equity,cash,gross_exposure,net_exposure,return_1d,turnover_pct,turnover\n"
        "2026-04-07,10000,2600,0.74,0.74,0.001,0.31,0.31\n",
    )
    _write_text(
        tmp_path / "outputs" / "perf" / "holdings_mtm_2026-04-07.csv",
        "date,ticker,shares,avg_cost,mtm_price,market_value,unrealized_pnl,realized_pnl\n"
        "2026-04-07,A,1,0,0,1800,0,0\n"
        "2026-04-07,B,1,0,0,1700,0,0\n"
        "2026-04-07,C,1,0,0,1600,0,0\n"
        "2026-04-07,D,1,0,0,1300,0,0\n"
        "2026-04-07,E,1,0,0,1000,0,0\n",
    )
    _write_text(
        tmp_path / "paper" / "config_paper.json",
        json.dumps(
            {
                "risk": {
                    "max_position_pct": 0.20,
                    "min_position_pct": 0.05,
                }
            }
        ),
    )

    conc_out = tmp_path / "outputs" / "perf" / "concentration_history.csv"
    conc = build_concentration_history(
        holdings_dir=tmp_path / "outputs" / "perf",
        nav_timeseries_path=tmp_path / "outputs" / "perf" / "nav_timeseries.csv",
        output_path=conc_out,
    )
    assert conc_out.exists()
    assert int(conc.iloc[0]["holdings_count"]) == 5
    assert abs(float(conc.iloc[0]["cash_weight"]) - 0.26) < 1e-12
    assert abs(float(conc.iloc[0]["largest_position_weight"]) - 0.18) < 1e-12

    parity = build_construction_parity_artifact(
        asof_date="2026-04-07",
        concentration_history_path=conc_out,
        config_path=tmp_path / "paper" / "config_paper.json",
        output_dir=tmp_path / "outputs" / "perf",
    )
    assert parity["status"] == "DRIFTED"
    assert "cash_weight_above_target" in parity["warnings"]
    assert "gross_exposure_below_target" in parity["warnings"]


def test_trade_day_pnl_artifact_producer(tmp_path: Path):
    run_root = tmp_path / "outputs" / "runs" / "2026-04-07T101024-0400_c809985"
    _write_text(
        run_root / "broker" / "pretrade_positions.json",
        json.dumps(
            {
                "positions": [
                    {
                        "symbol": "ABC",
                        "raw": {
                            "symbol": "ABC",
                            "qty": "2",
                            "avg_entry_price": "10.0",
                            "cost_basis": "20.0",
                        },
                    }
                ]
            }
        ),
    )
    _write_text(
        tmp_path / "outputs" / "broker_snapshot" / "broker_snapshot_2026-04-07.json",
        json.dumps(
            {
                "orders_report_date": [
                    {
                        "id": "sell-1",
                        "symbol": "ABC",
                        "side": "OrderSide.SELL",
                        "qty": "2",
                        "filled_qty": "2",
                        "filled_avg_price": "12.0",
                        "submitted_at": "2026-04-07T14:10:45+00:00",
                        "filled_at": "2026-04-07T14:10:46+00:00",
                        "status": "OrderStatus.FILLED",
                    },
                    {
                        "id": "buy-1",
                        "symbol": "XYZ",
                        "side": "OrderSide.BUY",
                        "qty": "3",
                        "filled_qty": "3",
                        "filled_avg_price": "20.0",
                        "submitted_at": "2026-04-07T14:10:50+00:00",
                        "filled_at": "2026-04-07T14:10:51+00:00",
                        "status": "OrderStatus.FILLED",
                    },
                ],
                "positions_current": [
                    {
                        "symbol": "XYZ",
                        "qty": "3",
                        "current_price": "21.5",
                        "cost_basis": "60.0",
                        "unrealized_pl": "4.5",
                    }
                ],
            }
        ),
    )

    payload = build_trade_day_pnl_artifact(
        trade_date="2026-04-07",
        run_root=run_root,
        broker_snapshot_path=tmp_path / "outputs" / "broker_snapshot" / "broker_snapshot_2026-04-07.json",
    )

    assert payload["status"] == "COMPLETE"
    assert payload["summary"]["realized_exit_pnl"] == 4.0
    assert payload["summary"]["open_buy_mark_pnl"] == 4.5
    assert (run_root / "trade_day_pnl.json").exists()
    assert (run_root / "trade_day_pnl.csv").exists()
