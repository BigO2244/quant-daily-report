from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from research.alpha_lab_v1.engine import StrategySpec, run_backtest
from research.alpha_lab_v1.run import build_comparison_table, build_hypothesis_payload, write_artifacts
from research.alpha_lab_v1.signals import build_alpha_lab_signal_frame


def _make_panel() -> pd.DataFrame:
    dates = pd.date_range("2022-01-03", periods=320, freq="B")
    rows = []
    for ticker, slope in (("AAA", 0.0025), ("BBB", 0.001), ("CCC", -0.0005), ("SPY", 0.0012)):
        price = 100.0
        for i, dt in enumerate(dates):
            price *= 1.0 + slope
            rows.append(
                {
                    "date": dt,
                    "ticker": ticker,
                    "open": price * 0.99,
                    "high": price * 1.01,
                    "low": price * 0.98,
                    "close": price,
                    "volume": 1_000_000 + 50_000 * (i % 5),
                    "sector": "Tech",
                }
            )
    return pd.DataFrame(rows)


def test_no_lookahead_in_momentum_score() -> None:
    panel = _make_panel()
    signals = build_alpha_lab_signal_frame(panel)
    row = signals[(signals["ticker"] == "AAA")].sort_values("date").iloc[260]
    ticker_rows = signals[signals["ticker"] == "AAA"].sort_values("date").reset_index(drop=True)
    idx = ticker_rows.index[ticker_rows["date"] == row["date"]][0]
    expected_r12_1 = ticker_rows.loc[idx - 21, "close"] / ticker_rows.loc[idx - 252, "close"] - 1.0
    assert abs(float(row["r12_1"]) - float(expected_r12_1)) < 1e-12


def test_backtest_runs_end_to_end() -> None:
    panel = _make_panel()
    signals = build_alpha_lab_signal_frame(panel)
    spec = StrategySpec(
        name="baseline_top10_daily",
        hypothesis_id="BASELINE",
        description="baseline",
        selection_mode="momentum",
        top_n=2,
    )
    result = run_backtest(signals, spec, start_date="2022-01-03", end_date="2023-03-01")
    assert "summary" in result
    assert not result["nav"].empty


def test_outputs_written_correctly(tmp_path: Path) -> None:
    panel = _make_panel()
    signals = build_alpha_lab_signal_frame(panel)
    spec = StrategySpec(
        name="baseline_top10_daily",
        hypothesis_id="BASELINE",
        description="baseline",
        selection_mode="momentum",
        top_n=2,
    )
    results = {spec.name: run_backtest(signals, spec, start_date="2022-01-03", end_date="2023-03-01")}
    comparison = build_comparison_table(results)
    summary = {
        "schema_version": "alpha_lab_v1",
        "baseline": results[spec.name]["summary"],
        "hypotheses": build_hypothesis_payload(results, {"strategies": []}),
    }
    write_artifacts(
        output_dir=tmp_path,
        summary=summary,
        results=results,
        comparison=comparison,
        robustness_results=pd.DataFrame(),
        robustness_summary={"strategies": []},
    )
    assert (tmp_path / "summary.json").exists()
    payload = json.loads((tmp_path / "summary.json").read_text())
    assert payload["schema_version"] == "alpha_lab_v1"


def test_cli_smoke(tmp_path: Path) -> None:
    panel = _make_panel()
    panel_path = tmp_path / "price_panel.parquet"
    panel.to_parquet(panel_path, index=False)
    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "research.alpha_lab_v1.run",
            "--start-date",
            "2022-01-03",
            "--end-date",
            "2023-03-01",
            "--top-n",
            "2",
            "--num-sims",
            "2",
            "--output-dir",
            str(out_dir),
            "--price-cache-path",
            str(panel_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0
    assert (out_dir / "summary.json").exists()
