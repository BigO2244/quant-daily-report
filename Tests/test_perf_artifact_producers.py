from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from paper.perf_artifact_producers import (
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
