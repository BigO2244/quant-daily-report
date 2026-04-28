from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from research.flow_detection.analysis import attach_forward_returns
from research.flow_detection.backtest import FlowBacktestConfig, run_strategy_backtest
from research.flow_detection.data import ensure_price_panel
from research.flow_detection.random_windows import sample_randomized_windows
from research.flow_detection.run import _write_artifacts, build_summary
from research.flow_detection.signals import build_flow_signals


def _make_panel() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=320, freq="B")
    rows = []
    for ticker, slope, flow_day in (("AAA", 0.004, 260), ("BBB", 0.002, 270), ("SPY", 0.0015, 9999)):
        price = 100.0
        for i, dt in enumerate(dates):
            price *= 1.0 + slope
            volume = 1_000_000 + (50_000 * (i % 5))
            if i == flow_day:
                volume = 5_000_000
                price *= 1.03
            rows.append(
                {
                    "date": dt,
                    "ticker": ticker,
                    "open": price * 0.99,
                    "high": price * 1.01,
                    "low": price * 0.98,
                    "close": price,
                    "volume": volume,
                    "sector": "Tech",
                }
            )
    return pd.DataFrame(rows)


def test_volume_z_and_flow_flagging() -> None:
    panel = _make_panel()
    signals = build_flow_signals(panel)
    row = signals[(signals["ticker"] == "AAA") & (signals["date"] == pd.Timestamp("2024-12-30"))].iloc[0]
    assert row["volume_z"] > 1.5
    assert bool(row["flow_active"]) is True


def test_no_lookahead_in_volume_window() -> None:
    panel = _make_panel()
    signals = build_flow_signals(panel)
    row = signals[(signals["ticker"] == "AAA") & (signals["date"] == pd.Timestamp("2024-12-30"))].iloc[0]
    hist = panel[(panel["ticker"] == "AAA") & (panel["date"] < pd.Timestamp("2024-12-30"))].sort_values("date").tail(20)
    expected_mean = hist["volume"].mean()
    assert abs(float(row["vol_mean_20"]) - float(expected_mean)) < 1e-9


def test_forward_return_alignment() -> None:
    panel = _make_panel()
    signals = attach_forward_returns(build_flow_signals(panel), horizons=(1, 3))
    row = signals[(signals["ticker"] == "AAA")].sort_values("date").iloc[250]
    ticker_rows = signals[signals["ticker"] == "AAA"].sort_values("date").reset_index(drop=True)
    idx = ticker_rows.index[ticker_rows["date"] == row["date"]][0]
    expected = ticker_rows.loc[idx + 1, "close"] / ticker_rows.loc[idx, "close"] - 1.0
    assert abs(float(row["fwd_1d"]) - float(expected)) < 1e-12


def test_random_window_sampler_validity() -> None:
    dates = pd.date_range("2010-01-01", periods=4000, freq="B")
    windows = sample_randomized_windows(dates, horizon_years=2, num_samples=10, seed=11)
    assert len(windows) == 10
    for sample in windows:
        assert sample.start_date < sample.end_date


def test_artifact_writing(tmp_path: Path) -> None:
    panel = _make_panel()
    signals = attach_forward_returns(build_flow_signals(panel))
    config = FlowBacktestConfig(top_n=2)
    baseline = run_strategy_backtest(signals, strategy="baseline", config=config)
    flow = run_strategy_backtest(signals, strategy="flow_filtered", config=config)
    summary = build_summary(
        panel_meta={"coverage": {"start_date": "2024-01-01", "end_date": "2025-03-31"}},
        signals=signals,
        baseline=baseline["summary"],
        flow=flow["summary"],
        event_study_summary=[],
        randomized_window_summary={"windows": []},
        use_efficiency_filter=False,
    )
    _write_artifacts(
        output_dir=tmp_path,
        summary=summary,
        signals=signals,
        event_study_rows=signals,
        event_study_summary=[],
        baseline=baseline,
        flow=flow,
        window_results=pd.DataFrame(),
        window_summary={"windows": []},
    )
    for name in (
        "summary.json",
        "signals.parquet",
        "event_study.csv",
        "backtest_baseline.json",
        "backtest_flow_filtered.json",
        "randomized_window_results.csv",
        "randomized_window_summary.json",
        "report.md",
    ):
        assert (tmp_path / name).exists(), name
    payload = json.loads((tmp_path / "summary.json").read_text())
    assert payload["schema_version"] == "flow_detection_v1"


def test_backtest_summary_includes_benchmark_comparison() -> None:
    panel = _make_panel()
    signals = attach_forward_returns(build_flow_signals(panel))
    config = FlowBacktestConfig(top_n=2)
    baseline = run_strategy_backtest(signals, strategy="baseline", config=config)
    assert "benchmark_cumulative_return" in baseline["summary"]
    assert "excess_return_vs_spy" in baseline["summary"]


def test_ensure_price_panel_filters_cache_to_requested_window(tmp_path: Path) -> None:
    panel = _make_panel()
    cache_path = tmp_path / "price_panel.parquet"
    panel.to_parquet(cache_path, index=False)

    filtered, meta = ensure_price_panel(
        symbols=["AAA", "SPY"],
        start_date="2024-06-03",
        end_date="2024-06-10",
        cache_path=cache_path,
        prefer_local=False,
        allow_download=False,
    )

    assert not filtered.empty
    assert set(filtered["ticker"].unique()) == {"AAA", "SPY"}
    assert filtered["date"].min() >= pd.Timestamp("2024-06-03")
    assert filtered["date"].max() <= pd.Timestamp("2024-06-10")
    assert meta["coverage"]["start_date"] == "2024-06-03"
    assert meta["coverage"]["end_date"] == "2024-06-10"


def test_cli_smoke(tmp_path: Path, monkeypatch) -> None:
    panel = _make_panel()
    panel_path = tmp_path / "price_panel.parquet"
    panel.to_parquet(panel_path, index=False)
    out_dir = tmp_path / "out"
    monkeypatch.chdir(Path.cwd())
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "research.flow_detection.run",
            "--start-date",
            "2024-01-01",
            "--end-date",
            "2025-03-31",
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
