from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from research.alpha_assessment.performance_layer_v1 import build_canonical_performance


def _write_csv(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_canonical_schema_and_benchmark_alignment(tmp_path: Path):
    _write_csv(
        tmp_path / "outputs" / "perf" / "nav_timeseries.csv",
        "date,equity,return_1d,gross_exposure,net_exposure,cash,turnover\n"
        "2026-02-24,10000,0.00,0.50,0.50,5000,0.10\n"
        "2026-02-25,10100,0.01,0.55,0.55,4500,0.12\n",
    )
    _write_csv(
        tmp_path / "outputs" / "perf" / "inception_nav_2026-02-23.csv",
        "date,model_nav,spy_nav\n"
        "2026-02-24,10000,10000\n"
        "2026-02-25,10100,10100\n",
    )
    _write_csv(
        tmp_path / "outputs" / "vix_regime" / "regime_history.csv",
        "as_of,regime,vix,position_scale,max_positions\n"
        "2026-02-24,ELEVATED,22.0,0.75,7\n"
        "2026-02-25,LOW,18.0,1.0,10\n",
    )
    _write_csv(
        tmp_path / "outputs" / "perf" / "holdings_mtm_2026-02-25.csv",
        "date,ticker,shares,avg_cost,mtm_price,market_value,unrealized_pnl,realized_pnl\n"
        "2026-02-25,AAPL,1,100,101,101,1,0\n",
    )
    _write_csv(
        tmp_path / "outputs" / "ledger" / "trades.csv",
        "trade_date,ticker,notional\n"
        "2026-02-25,AAPL,101\n",
    )
    (tmp_path / "signals").mkdir(parents=True, exist_ok=True)
    (tmp_path / "signals" / "2026-02-25.json").write_text(
        json.dumps(
            {
                "snapshot_date": "2026-02-25",
                "breaker": {"mode": "partial", "exposure_label_today": "PARTIAL", "exposure_multiplier_today": 0.5},
            }
        ),
        encoding="utf-8",
    )

    df, meta = build_canonical_performance(tmp_path)

    expected_cols = {
        "date",
        "strategy_nav",
        "strategy_return",
        "spy_close",
        "spy_return",
        "excess_return",
        "vix_close",
        "vix_regime",
        "gross_exposure",
        "net_exposure",
        "cash_weight",
        "turnover",
        "holdings_count",
        "realized_pnl",
        "unrealized_pnl",
        "premarket_score",
        "overlay_signal",
        "active_overlay",
        "notes_source_flags",
    }
    assert expected_cols.issubset(df.columns)
    assert not meta["synthetic_mode"]

    row = df[df["date"] == "2026-02-25"].iloc[0]
    assert pytest.approx(float(row["spy_return"]), abs=1e-9) == 0.01
    assert pytest.approx(float(row["excess_return"]), abs=1e-9) == 0.0


def test_graceful_missing_sources_requires_explicit_synthetic(tmp_path: Path):
    with pytest.raises(RuntimeError):
        build_canonical_performance(tmp_path, allow_synthetic=False)

    df, meta = build_canonical_performance(tmp_path, allow_synthetic=True)
    assert not df.empty
    assert meta["synthetic_mode"] is True


def test_analyzer_ingestion_when_available(tmp_path: Path):
    _write_csv(
        tmp_path / "outputs" / "perf" / "nav_timeseries.csv",
        "date,equity,return_1d,gross_exposure,net_exposure,cash,turnover\n"
        "2026-03-04,10000,0.00,0.50,0.50,5000,0.10\n",
    )
    _write_csv(
        tmp_path / "outputs" / "vix_regime" / "regime_history.csv",
        "as_of,regime,vix,position_scale,max_positions\n"
        "2026-03-04,ELEVATED,22.0,0.75,7\n",
    )
    _write_csv(tmp_path / "outputs" / "ledger" / "trades.csv", "trade_date,ticker,notional\n")
    (tmp_path / "signals").mkdir(parents=True, exist_ok=True)
    (tmp_path / "signals" / "2026-03-04.json").write_text(
        json.dumps(
            {
                "snapshot_date": "2026-03-04",
                "market_analyzer": {"score": 0.73},
                "breaker": {"mode": "off", "exposure_label_today": "OFF", "exposure_multiplier_today": 1.0},
            }
        ),
        encoding="utf-8",
    )

    df, _ = build_canonical_performance(tmp_path)
    row = df[df["date"] == "2026-03-04"].iloc[0]
    assert pytest.approx(float(row["premarket_score"]), abs=1e-12) == 0.73


def test_source_precedence_benchmark_close_and_analyzer_csv(tmp_path: Path):
    _write_csv(
        tmp_path / "outputs" / "perf" / "nav_timeseries.csv",
        "date,equity,return_1d,gross_exposure,net_exposure,cash,turnover\n"
        "2026-03-04,10000,0.00,0.50,0.50,5000,0.10\n",
    )
    _write_csv(
        tmp_path / "outputs" / "perf" / "inception_nav_2026-03-04.csv",
        "date,model_nav,spy_nav\n"
        "2026-03-04,10000,20000\n",
    )
    _write_csv(
        tmp_path / "outputs" / "perf" / "benchmark_close_history.csv",
        "date,spy_close,spy_return\n"
        "2026-03-04,612.34,0.0025\n",
    )
    _write_csv(
        tmp_path / "outputs" / "perf" / "premarket_analyzer_scores.csv",
        "date,premarket_score\n"
        "2026-03-04,0.91\n",
    )
    _write_csv(
        tmp_path / "outputs" / "vix_regime" / "regime_history.csv",
        "as_of,regime,vix,position_scale,max_positions\n"
        "2026-03-04,ELEVATED,22.0,0.75,7\n",
    )
    _write_csv(tmp_path / "outputs" / "ledger" / "trades.csv", "trade_date,ticker,notional\n")
    (tmp_path / "signals").mkdir(parents=True, exist_ok=True)
    (tmp_path / "signals" / "2026-03-04.json").write_text(
        json.dumps(
            {
                "snapshot_date": "2026-03-04",
                "market_analyzer": {"score": 0.12},
                "breaker": {"mode": "off", "exposure_label_today": "OFF", "exposure_multiplier_today": 1.0},
            }
        ),
        encoding="utf-8",
    )

    df, _meta = build_canonical_performance(tmp_path)
    row = df[df["date"] == "2026-03-04"].iloc[0]

    assert pytest.approx(float(row["spy_close"]), abs=1e-12) == 612.34
    assert pytest.approx(float(row["spy_return"]), abs=1e-12) == 0.0025
    assert pytest.approx(float(row["premarket_score"]), abs=1e-12) == 0.91


def test_duplicate_dates_are_deterministically_deduped(tmp_path: Path):
    _write_csv(
        tmp_path / "outputs" / "perf" / "nav_timeseries.csv",
        "date,equity,return_1d,gross_exposure,net_exposure,cash,turnover\n"
        "2026-03-04,10000,0.00,0.50,0.50,5000,0.10\n"
        "2026-03-04,10001,0.01,0.51,0.51,4999,0.11\n",
    )
    _write_csv(
        tmp_path / "outputs" / "perf" / "inception_nav_2026-03-04.csv",
        "date,model_nav,spy_nav\n"
        "2026-03-04,10000,20000\n"
        "2026-03-04,10001,20010\n",
    )
    _write_csv(
        tmp_path / "outputs" / "vix_regime" / "regime_history.csv",
        "as_of,regime,vix,position_scale,max_positions\n"
        "2026-03-04,ELEVATED,22.0,0.75,7\n"
        "2026-03-04,LOW,19.0,1.0,10\n",
    )
    _write_csv(tmp_path / "outputs" / "ledger" / "trades.csv", "trade_date,ticker,notional\n")

    df, _meta = build_canonical_performance(tmp_path)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["date"] == "2026-03-04"
    assert pytest.approx(float(row["strategy_nav"]), abs=1e-12) == 10001.0
    assert row["vix_regime"] == "LOW"


def test_missing_required_strategy_source_fails_without_synthetic(tmp_path: Path):
    _write_csv(
        tmp_path / "outputs" / "vix_regime" / "regime_history.csv",
        "as_of,regime,vix,position_scale,max_positions\n"
        "2026-03-04,ELEVATED,22.0,0.75,7\n",
    )
    with pytest.raises(RuntimeError, match="Missing required strategy source data"):
        build_canonical_performance(tmp_path, allow_synthetic=False)


def test_coverage_report_metadata_is_generated(tmp_path: Path):
    _write_csv(
        tmp_path / "outputs" / "perf" / "nav_timeseries.csv",
        "date,equity,return_1d,gross_exposure,net_exposure,cash,turnover\n"
        "2026-03-04,10000,0.00,0.50,0.50,5000,0.10\n",
    )
    _write_csv(
        tmp_path / "outputs" / "perf" / "inception_nav_2026-03-04.csv",
        "date,model_nav,spy_nav\n"
        "2026-03-04,10000,20000\n",
    )
    _write_csv(
        tmp_path / "outputs" / "vix_regime" / "regime_history.csv",
        "as_of,regime,vix,position_scale,max_positions\n"
        "2026-03-04,ELEVATED,22.0,0.75,7\n",
    )
    _write_csv(tmp_path / "outputs" / "ledger" / "trades.csv", "trade_date,ticker,notional\n")

    _df, meta = build_canonical_performance(tmp_path)

    coverage = meta.get("field_coverage") or []
    assert coverage
    spy_close_row = next(row for row in coverage if row["field"] == "spy_close")
    assert spy_close_row["required"] == "optional"
    assert "fill_rate_pct" in spy_close_row
    assert meta.get("missing_contracts")
