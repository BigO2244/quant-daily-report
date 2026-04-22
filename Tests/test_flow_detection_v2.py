from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from research.flow_detection.v2_analysis import build_event_study_v2
from research.flow_detection.v2_backtest import FlowBacktestV2Config, run_strategy_backtest_v2
from research.flow_detection.v2_regimes import build_regime_frame
from research.flow_detection.v2_run import build_summary_v2, write_artifacts_v2
from research.flow_detection.v2_signals import build_flow_signals_v2


def _make_panel() -> pd.DataFrame:
    dates = pd.date_range("2022-01-03", periods=420, freq="B")
    rows = []
    for ticker, slope, spikes in (
        ("AAA", 0.0030, {250: (4_000_000, 1.02), 251: (3_500_000, 1.015), 252: (3_200_000, 1.01)}),
        ("BBB", 0.0015, {300: (4_500_000, 1.03)}),
        ("SPY", 0.0010, {}),
    ):
        price = 100.0
        for i, dt in enumerate(dates):
            volume = 1_000_000 + (25_000 * (i % 7))
            price *= 1.0 + slope
            if i in spikes:
                vol, bump = spikes[i]
                volume = vol
                price *= bump
            rows.append(
                {
                    "date": dt,
                    "ticker": ticker,
                    "open": price * 0.99,
                    "high": price * 1.01,
                    "low": price * 0.98,
                    "close": price,
                    "volume": volume,
                    "sector": "Tech" if ticker != "SPY" else "Index",
                }
            )
    return pd.DataFrame(rows)


def test_slower_participation_calculations() -> None:
    panel = _make_panel()
    signals = build_flow_signals_v2(panel)
    row = signals[(signals["ticker"] == "AAA") & (signals["date"] == pd.Timestamp("2022-12-20"))].iloc[0]
    assert row["volume_z_3d_avg"] > 0
    assert row["accumulation_3d"] > 0
    assert bool(row["persistent_participation_3d"]) is True


def test_no_lookahead_in_slower_signals() -> None:
    panel = _make_panel()
    signals = build_flow_signals_v2(panel)
    target_date = pd.Timestamp("2022-12-20")
    row = signals[(signals["ticker"] == "AAA") & (signals["date"] == target_date)].iloc[0]
    hist = signals[(signals["ticker"] == "AAA") & (signals["date"] <= target_date)].sort_values("date").tail(3)
    expected = hist["volume_z"].mean()
    assert abs(float(row["volume_z_3d_avg"]) - float(expected)) < 1e-9


def test_exit_signal_forward_alignment() -> None:
    panel = _make_panel()
    signals = build_flow_signals_v2(panel)
    event_rows, summary = build_event_study_v2(signals, horizons=(3,))
    cohort_rows = [row for row in summary if row["cohort"] == "extended_plus_exhaustion" and row["split"] == "all"]
    assert cohort_rows
    assert cohort_rows[0]["horizon_days"] == 3


def test_regime_split_assignment_validity() -> None:
    panel = _make_panel()
    regime = build_regime_frame(panel)
    assert {"date", "trend_state", "vol_bucket"}.issubset(regime.columns)
    assert regime["trend_state"].notna().all()
    assert regime["vol_bucket"].isin(["normal", "high_vol"]).all()


def test_artifact_writing_v2(tmp_path: Path) -> None:
    panel = _make_panel()
    signals = build_flow_signals_v2(panel)
    event_rows, event_summary = build_event_study_v2(signals, horizons=(1, 3))
    config = FlowBacktestV2Config(top_n=2)
    backtests = {
        strategy: run_strategy_backtest_v2(signals, strategy=strategy, config=config)
        for strategy in ("baseline", "participation_entry", "participation_exit", "regime_conditional_participation")
    }
    summary = build_summary_v2(
        panel_meta={"coverage": {"start_date": "2022-01-03", "end_date": "2023-08-15"}},
        signals=signals,
        backtests={k: v["summary"] for k, v in backtests.items()},
        event_study_summary=event_summary,
        randomized_window_summary={"windows": []},
    )
    write_artifacts_v2(
        output_dir=tmp_path,
        summary=summary,
        signals=signals,
        event_rows=event_rows,
        event_summary=event_summary,
        backtests=backtests,
        window_results=pd.DataFrame(),
        window_summary={"windows": []},
    )
    assert (tmp_path / "summary.json").exists()
    payload = json.loads((tmp_path / "summary.json").read_text())
    assert payload["schema_version"] == "flow_detection_v2"


def test_cli_smoke_v2(tmp_path: Path) -> None:
    panel = _make_panel()
    panel_path = tmp_path / "price_panel.parquet"
    panel.to_parquet(panel_path, index=False)
    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "research.flow_detection.v2_run",
            "--start-date",
            "2022-01-03",
            "--end-date",
            "2023-08-15",
            "--num-sims",
            "2",
            "--window-years",
            "2",
            "--price-cache-path",
            str(panel_path),
            "--output-dir",
            str(out_dir),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0
    assert (out_dir / "summary.json").exists()
