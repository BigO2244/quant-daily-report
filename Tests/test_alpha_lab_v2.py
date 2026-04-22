from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from research.alpha_lab_v1.signals import build_alpha_lab_signal_frame
from research.alpha_lab_v2.engine import StrategySpec, run_backtest
from research.alpha_lab_v2.hypotheses import build_strategy_specs
from research.alpha_lab_v2.robustness import run_randomized_windows
from research.alpha_lab_v2.run import build_comparison_table, build_ranked_variants, identify_best_single_change, write_artifacts


def _make_panel() -> pd.DataFrame:
    dates = pd.date_range("2022-01-03", periods=340, freq="B")
    rows = []
    slopes = {
        "AAA": 0.0026,
        "BBB": 0.0017,
        "CCC": 0.0012,
        "DDD": 0.0007,
        "EEE": 0.0002,
        "FFF": -0.0002,
        "SPY": 0.0011,
    }
    for ticker, slope in slopes.items():
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
                    "volume": 1_000_000 + 20_000 * (i % 7),
                    "sector": "Tech",
                }
            )
    return pd.DataFrame(rows)


def test_combined_logic_uses_existing_ranks_without_lookahead() -> None:
    panel = _make_panel()
    signals = build_alpha_lab_signal_frame(panel)
    row = signals[signals["ticker"] == "AAA"].sort_values("date").iloc[260]
    ticker_rows = signals[signals["ticker"] == "AAA"].sort_values("date").reset_index(drop=True)
    idx = ticker_rows.index[ticker_rows["date"] == row["date"]][0]
    expected_r12_1 = ticker_rows.loc[idx - 21, "close"] / ticker_rows.loc[idx - 252, "close"] - 1.0
    assert abs(float(row["r12_1"]) - float(expected_r12_1)) < 1e-12


def test_combined_variants_run_correctly() -> None:
    panel = _make_panel()
    signals = build_alpha_lab_signal_frame(panel)
    spec = StrategySpec(
        name="combo",
        hypothesis_id="COMBO",
        description="weekly top5 with rank decay",
        top_n=5,
        rebalance_mode="weekly",
        use_rank_decay_exit=True,
    )
    result = run_backtest(signals, spec, start_date="2022-01-03", end_date="2023-04-01")
    assert "summary" in result
    assert not result["nav"].empty
    assert int(result["daily"]["holdings_count"].max()) <= 5


def test_output_artifacts_written_successfully(tmp_path: Path) -> None:
    panel = _make_panel()
    signals = build_alpha_lab_signal_frame(panel)
    specs = build_strategy_specs()[:4]
    results = {spec.name: run_backtest(signals, spec, start_date="2022-01-03", end_date="2023-04-01") for spec in specs}
    comparison = build_comparison_table(results)
    best_single = identify_best_single_change(
        pd.DataFrame(
            [
                {"strategy": "h2_rank_decay_exit_top10_daily", "sharpe": 1.1, "cagr": 0.2},
                {"strategy": "h6_top5_daily", "sharpe": 1.2, "cagr": 0.21},
                {"strategy": "h1_weekly_top10", "sharpe": 1.0, "cagr": 0.18},
            ]
        )
    )
    ranked = build_ranked_variants(results, {"strategies": []}, best_single)
    summary = {
        "schema_version": "alpha_lab_v2",
        "baseline": results["baseline_top10_daily"]["summary"],
        "best_single_change_variant": best_single,
        "ranked_variants": ranked,
        "study_answers": {"verdict_map": {item["strategy"]: item["verdict"] for item in ranked}},
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
    assert payload["schema_version"] == "alpha_lab_v2"


def test_randomized_windows_reproducible_with_same_seed() -> None:
    panel = _make_panel()
    signals = build_alpha_lab_signal_frame(panel)
    specs = build_strategy_specs()[:4]
    results_1, summary_1 = run_randomized_windows(
        signals,
        specs=specs,
        start_date="2022-01-03",
        end_date="2023-04-01",
        window_years=[2, 3],
        num_sims=2,
        seed=42,
        baseline_name="baseline_top10_daily",
        best_single_change_name="h6_top5_daily",
    )
    results_2, summary_2 = run_randomized_windows(
        signals,
        specs=specs,
        start_date="2022-01-03",
        end_date="2023-04-01",
        window_years=[2, 3],
        num_sims=2,
        seed=42,
        baseline_name="baseline_top10_daily",
        best_single_change_name="h6_top5_daily",
    )
    pd.testing.assert_frame_equal(results_1, results_2)
    assert summary_1 == summary_2


def test_cli_smoke(tmp_path: Path) -> None:
    panel = _make_panel()
    panel_path = tmp_path / "price_panel.parquet"
    panel.to_parquet(panel_path, index=False)
    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "research.alpha_lab_v2.run",
            "--start-date",
            "2022-01-03",
            "--end-date",
            "2023-04-01",
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
