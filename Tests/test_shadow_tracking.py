from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from research.alpha_lab_v1.signals import build_alpha_lab_signal_frame
from research.shadow_tracking.run import (
    build_comparison_markdown,
    compute_returns_for_trade_date,
    compute_strategy_delta,
    find_previous_shadow_date,
    find_previous_trading_date,
    main,
    resolve_trade_date,
    trade_date_has_data,
)
from research.shadow_tracking.strategies import build_strategy_lookup


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


def test_strategy_names_and_slugs_map_correctly() -> None:
    lookup = build_strategy_lookup()
    assert lookup["caerus_polaris"].strategy_name == "Caerus Polaris"
    assert lookup["caerus_orion"].spec.use_rank_decay_exit is True
    assert lookup["caerus_orion"].spec.top_n == 5
    assert lookup["caerus_lyra"].spec.rebalance_mode == "weekly"
    assert lookup["caerus_lyra"].spec.use_rank_decay_exit is False


def test_shadow_runner_writes_expected_files_and_no_execution_side_effects(tmp_path: Path) -> None:
    panel = _make_panel()
    panel_path = tmp_path / "price_panel.parquet"
    panel.to_parquet(panel_path, index=False)
    out_dir = tmp_path / "shadow"
    rc = main(
        [
            "--trade-date",
            "2023-03-30",
            "--start-date",
            "2022-01-03",
            "--end-date",
            "2023-03-30",
            "--output-dir",
            str(out_dir),
            "--price-cache-path",
            str(panel_path),
        ]
    )
    assert rc == 0
    rc = main(
        [
            "--trade-date",
            "2023-03-31",
            "--start-date",
            "2022-01-03",
            "--end-date",
            "2023-03-31",
            "--output-dir",
            str(out_dir),
            "--price-cache-path",
            str(panel_path),
        ]
    )
    assert rc == 0
    dated_dir = out_dir / "2023-03-31"
    assert (dated_dir / "caerus_polaris.json").exists()
    assert (dated_dir / "caerus_orion.json").exists()
    assert (dated_dir / "caerus_lyra.json").exists()
    assert (dated_dir / "summary.json").exists()
    assert (dated_dir / "comparison.json").exists()
    assert (dated_dir / "comparison.md").exists()
    assert (dated_dir / "delta.json").exists()
    assert (dated_dir / "shadow_performance.json").exists()
    assert (dated_dir / "shadow_evaluation.json").exists()
    assert (out_dir / "performance" / "shadow_nav_series.csv").exists()
    assert (out_dir / "performance" / "shadow_summary.json").exists()
    assert not (out_dir / "paper_state").exists()


def test_compute_strategy_delta_partial_overlap() -> None:
    previous_payload = {
        "strategy_name": "Caerus Orion",
        "strategy_slug": "caerus_orion",
        "target_weights": {"AAA": 0.2, "BBB": 0.2, "CCC": 0.2},
    }
    current_payload = {
        "strategy_name": "Caerus Orion",
        "strategy_slug": "caerus_orion",
        "target_weights": {"AAA": 0.25, "BBB": 0.15, "DDD": 0.2},
    }
    delta = compute_strategy_delta(previous_payload, current_payload)
    assert delta["adds"] == ["DDD"]
    assert delta["removes"] == ["CCC"]
    assert delta["unchanged"] == ["AAA", "BBB"]
    assert delta["weight_changes"]["AAA"] == 0.05
    assert delta["weight_changes"]["BBB"] == -0.05
    assert delta["summary_metrics"]["turnover_proxy"] == 0.5
    assert delta["summary"] == "Orion rotated 2 names"


def test_find_previous_shadow_date_and_no_prior_case(tmp_path: Path) -> None:
    (tmp_path / "performance").mkdir()
    (tmp_path / "2026-04-20").mkdir()
    (tmp_path / "2026-04-21").mkdir()
    assert find_previous_shadow_date(tmp_path, trade_date="2026-04-21") == "2026-04-20"
    assert find_previous_shadow_date(tmp_path, trade_date="2026-04-20") is None


def test_trade_date_helpers_are_explicit() -> None:
    panel = _make_panel()
    signals = build_alpha_lab_signal_frame(panel)
    message = resolve_trade_date(signals, requested_trade_date="2023-04-02", end_date="2023-04-02")
    assert "requested trade_date unavailable in data" in message
    assert trade_date_has_data(signals, trade_date="2023-03-31") is True
    assert trade_date_has_data(signals, trade_date="2023-04-02") is False
    assert find_previous_trading_date(signals, trade_date="2023-03-31") == "2023-03-30"


def test_comparison_markdown_renders_decision_grade_summary(tmp_path: Path) -> None:
    dated_dir = tmp_path / "2026-04-24"
    dated_dir.mkdir()
    comparison = {
        "trade_date": "2026-04-24",
        "benchmark_symbol": "SPY",
        "delta": {"trade_date": "2026-04-24", "previous_date": "2026-04-23", "status": "OK", "strategies": {}},
        "strategies": {
            "caerus_polaris": {
                "strategy_name": "Caerus Polaris",
                "expected_turnover": 0.10,
                "estimated_holding_period_days": 20.0,
                "weight_concentration": {"max_weight": 0.10, "top3_concentration": 0.30},
                "holdings": [{"ticker": "AAA", "target_weight": 0.10}],
            },
            "caerus_orion": {
                "strategy_name": "Caerus Orion",
                "expected_turnover": 0.20,
                "estimated_holding_period_days": 30.0,
                "weight_concentration": {"max_weight": 0.20, "top3_concentration": 0.60},
                "holdings": [{"ticker": "BBB", "target_weight": 0.20}],
            },
            "caerus_lyra": {
                "strategy_name": "Caerus Lyra",
                "expected_turnover": 0.30,
                "estimated_holding_period_days": 40.0,
                "weight_concentration": {"max_weight": 0.20, "top3_concentration": 0.60},
                "holdings": [{"ticker": "CCC", "target_weight": 0.20}],
            },
        },
        "pairwise_overlap": [
            {
                "left_slug": "caerus_polaris",
                "right_slug": "caerus_orion",
                "overlap_weight_pct": 0.5,
                "left_unique_names": ["AAA"],
                "right_unique_names": ["BBB"],
            },
            {
                "left_slug": "caerus_polaris",
                "right_slug": "caerus_lyra",
                "overlap_weight_pct": 0.4,
                "left_unique_names": ["AAA"],
                "right_unique_names": ["CCC"],
            },
            {
                "left_slug": "caerus_orion",
                "right_slug": "caerus_lyra",
                "overlap_weight_pct": 0.8,
                "left_unique_names": ["BBB"],
                "right_unique_names": ["CCC"],
            },
        ],
        "broker_context": {},
    }
    evaluation = {
        "trade_date": "2026-04-24",
        "benchmark_symbol": "SPY",
        "strategies": {
            "caerus_polaris": {
                "strategy_name": "Caerus Polaris",
                "status": "OK",
                "data_status": "OK",
                "return_convention": "weights_as_of_t",
                "daily_return": 0.01,
                "cumulative_return": 0.03,
                "excess_return_vs_spy": 0.01,
                "rolling_count_of_valid_days": 3,
                "realized_volatility_ann": 0.15,
                "max_drawdown": -0.02,
                "avg_turnover": 0.10,
                "avg_top_3_concentration": 0.30,
                "constituent_change_count": 2,
            },
            "caerus_orion": {
                "strategy_name": "Caerus Orion",
                "status": "OK",
                "data_status": "OK",
                "return_convention": "weights_as_of_t",
                "daily_return": 0.02,
                "cumulative_return": 0.05,
                "excess_return_vs_spy": 0.03,
                "rolling_count_of_valid_days": 3,
                "realized_volatility_ann": 0.18,
                "max_drawdown": -0.01,
                "avg_turnover": 0.20,
                "avg_top_3_concentration": 0.60,
                "constituent_change_count": 4,
            },
            "caerus_lyra": {
                "strategy_name": "Caerus Lyra",
                "status": "OK",
                "data_status": "OK",
                "return_convention": "weights_as_of_t",
                "daily_return": -0.01,
                "cumulative_return": 0.01,
                "excess_return_vs_spy": -0.01,
                "rolling_count_of_valid_days": 3,
                "realized_volatility_ann": 0.20,
                "max_drawdown": -0.04,
                "avg_turnover": 0.30,
                "avg_top_3_concentration": 0.60,
                "constituent_change_count": 6,
            },
            "spy_benchmark": {
                "strategy_name": "SPY",
                "status": "OK",
                "data_status": "OK",
                "return_convention": "weights_as_of_t",
                "daily_return": 0.005,
                "cumulative_return": 0.02,
                "excess_return_vs_spy": 0.0,
                "rolling_count_of_valid_days": 3,
                "realized_volatility_ann": 0.12,
                "max_drawdown": -0.01,
            },
        },
    }
    (dated_dir / "delta.json").write_text(json.dumps(comparison["delta"]))
    (dated_dir / "shadow_evaluation.json").write_text(json.dumps(evaluation))

    markdown = build_comparison_markdown(comparison, dated_dir=dated_dir)

    assert markdown.index("## Executive Summary") < markdown.index("## Delta Artifact")
    assert "- Best performer today: Caerus Orion (2.00%)" in markdown
    assert "- Best cumulative performer: Caerus Orion (5.00%)" in markdown
    assert "- Orion vs Polaris: +2.00% cumulative return difference" in markdown
    assert "| Caerus Orion | OK | OK | 3 | 2.00% | 5.00% | 3.00% | 18.00% | -1.00% | 0.20 | 60.00% | 4 |" in markdown
    assert "- Status: DECISION_USEFUL" in markdown
    assert "- Delta status: OK" in markdown
    assert "- Diagnosis: Healthy; cumulative performance and day-over-day delta are available." in markdown
    assert "## Polaris vs Orion" in markdown


def test_trade_date_helpers_handle_empty_signals_frame() -> None:
    signals = pd.DataFrame()
    message = resolve_trade_date(signals, requested_trade_date="2023-04-02", end_date="2023-04-02")
    assert "requested trade_date unavailable in data" in message
    assert trade_date_has_data(signals, trade_date="2023-04-02") is False
    assert find_previous_trading_date(signals, trade_date="2023-04-02") is None


def test_compute_returns_for_trade_date_handles_empty_panel() -> None:
    assert compute_returns_for_trade_date(panel=pd.DataFrame(), trade_date="2023-04-02") == {}


def test_cli_smoke(tmp_path: Path) -> None:
    panel = _make_panel()
    panel_path = tmp_path / "price_panel.parquet"
    panel.to_parquet(panel_path, index=False)
    out_dir = tmp_path / "shadow"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "research.shadow_tracking.run",
            "--trade-date",
            "2023-03-31",
            "--start-date",
            "2022-01-03",
            "--end-date",
            "2023-03-31",
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
    payload = json.loads((out_dir / "2023-03-31" / "caerus_orion.json").read_text())
    assert payload["strategy_name"] == "Caerus Orion"
    assert payload["benchmark_symbol"] == "SPY"


def test_runner_writes_no_data_folder_for_unavailable_date(tmp_path: Path) -> None:
    panel = _make_panel()
    panel_path = tmp_path / "price_panel.parquet"
    panel.to_parquet(panel_path, index=False)
    out_dir = tmp_path / "shadow"
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        rc = main(
            [
                "--trade-date",
                "2023-04-02",
                "--start-date",
                "2022-01-03",
                "--end-date",
                "2023-04-02",
                "--output-dir",
                str(out_dir),
                "--price-cache-path",
                str(panel_path),
            ]
        )
    assert rc == 0
    text = stream.getvalue()
    dated_dir = out_dir / "2023-04-02"
    assert "[SHADOW] created folder for trade_date=2023-04-02" in text
    assert "[SHADOW] no data for trade_date=2023-04-02" in text
    assert "[SHADOW] delta status: NO_PRIOR" in text
    assert f"[SHADOW] wrote {dated_dir}/..." in text
    assert dated_dir.exists()
    assert (dated_dir / "summary.json").exists()
    assert (dated_dir / "comparison.md").exists()
    assert (dated_dir / "delta.json").exists()
    assert (dated_dir / "shadow_performance.json").exists()
    assert (dated_dir / "shadow_evaluation.json").exists()
    delta = json.loads((dated_dir / "delta.json").read_text())
    assert delta["status"] == "NO_DATA"
    perf = json.loads((dated_dir / "shadow_performance.json").read_text())
    assert perf["status"] == "NO_PRIOR"
    assert perf["data_status"] == "NO_DATA"
    assert perf["return_convention"] == "weights_as_of_t"
    assert perf["strategies"]["caerus_orion"]["nav"] == perf["strategies"]["caerus_orion"]["previous_nav"]
    evaluation = json.loads((dated_dir / "shadow_evaluation.json").read_text())
    assert evaluation["strategies"]["caerus_orion"]["status"] == "NO_PRIOR"
    assert evaluation["strategies"]["caerus_orion"]["data_status"] == "NO_DATA"
    assert evaluation["strategies"]["caerus_orion"]["return_convention"] == "weights_as_of_t"


def test_delta_generation_when_prior_day_exists(tmp_path: Path) -> None:
    panel = _make_panel()
    panel_path = tmp_path / "price_panel.parquet"
    panel.to_parquet(panel_path, index=False)
    out_dir = tmp_path / "shadow"
    assert main(
        [
            "--trade-date",
            "2023-03-30",
            "--start-date",
            "2022-01-03",
            "--end-date",
            "2023-03-30",
            "--output-dir",
            str(out_dir),
            "--price-cache-path",
            str(panel_path),
        ]
    ) == 0
    assert main(
        [
            "--trade-date",
            "2023-03-31",
            "--start-date",
            "2022-01-03",
            "--end-date",
            "2023-03-31",
            "--output-dir",
            str(out_dir),
            "--price-cache-path",
            str(panel_path),
        ]
    ) == 0
    dated_dir = out_dir / "2023-03-31"
    delta = json.loads((dated_dir / "delta.json").read_text())
    assert delta["status"] == "OK"
    assert delta["previous_date"] == "2023-03-30"
    comparison_md = (dated_dir / "comparison.md").read_text()
    assert "## Day-over-Day Changes" in comparison_md
    perf_1 = json.loads((out_dir / "2023-03-30" / "shadow_performance.json").read_text())
    perf_2 = json.loads((dated_dir / "shadow_performance.json").read_text())
    evaluation_1 = json.loads((out_dir / "2023-03-30" / "shadow_evaluation.json").read_text())
    evaluation_2 = json.loads((dated_dir / "shadow_evaluation.json").read_text())
    assert perf_1["status"] == "NO_PRIOR"
    assert perf_1["data_status"] == "OK"
    assert perf_2["status"] == "OK"
    assert perf_2["data_status"] == "OK"
    assert perf_2["return_convention"] == "weights_as_of_t"
    assert perf_1["strategies"]["caerus_orion"]["nav"] == 1.0 + perf_1["strategies"]["caerus_orion"]["daily_return"]
    assert perf_2["strategies"]["caerus_orion"]["previous_nav"] == perf_1["strategies"]["caerus_orion"]["nav"]
    expected_nav = perf_1["strategies"]["caerus_orion"]["nav"] * (1.0 + perf_2["strategies"]["caerus_orion"]["daily_return"])
    assert perf_2["strategies"]["caerus_orion"]["nav"] == round(expected_nav, 10)
    assert evaluation_1["strategies"]["caerus_orion"]["rolling_count_of_valid_days"] == 1
    assert evaluation_2["strategies"]["caerus_orion"]["rolling_count_of_valid_days"] == 2
    assert evaluation_2["strategies"]["caerus_orion"]["avg_turnover"] is not None
    assert evaluation_2["strategies"]["caerus_orion"]["avg_top_3_concentration"] is not None
    spy_cum = evaluation_2["strategies"]["spy_benchmark"]["cumulative_return"]
    orion_cum = evaluation_2["strategies"]["caerus_orion"]["cumulative_return"]
    assert evaluation_2["strategies"]["caerus_orion"]["excess_return_vs_spy"] == round(orion_cum - spy_cum, 10)


def test_shadow_performance_broken_chain_on_missing_prior_artifact(tmp_path: Path) -> None:
    panel = _make_panel()
    panel_path = tmp_path / "price_panel.parquet"
    panel.to_parquet(panel_path, index=False)
    out_dir = tmp_path / "shadow"
    assert main(
        [
            "--trade-date",
            "2023-03-30",
            "--start-date",
            "2022-01-03",
            "--end-date",
            "2023-03-30",
            "--output-dir",
            str(out_dir),
            "--price-cache-path",
            str(panel_path),
        ]
    ) == 0
    (out_dir / "2023-03-30" / "shadow_performance.json").unlink()
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        assert main(
            [
                "--trade-date",
                "2023-03-31",
                "--start-date",
                "2022-01-03",
                "--end-date",
                "2023-03-31",
                "--output-dir",
                str(out_dir),
                "--price-cache-path",
                str(panel_path),
            ]
        ) == 0
    perf = json.loads((out_dir / "2023-03-31" / "shadow_performance.json").read_text())
    assert perf["status"] == "BROKEN_CHAIN"
    assert perf["data_status"] == "OK"
    assert perf["strategies"]["caerus_orion"]["nav"] is None
    assert perf["strategies"]["caerus_orion"]["previous_nav"] is None
    evaluation = json.loads((out_dir / "2023-03-31" / "shadow_evaluation.json").read_text())
    assert evaluation["strategies"]["caerus_orion"]["status"] == "BROKEN_CHAIN"
    assert evaluation["strategies"]["caerus_orion"]["cumulative_return"] is None
    assert evaluation["strategies"]["caerus_orion"]["realized_volatility_ann"] is None
    assert evaluation["strategies"]["caerus_orion"]["max_drawdown"] is None
    assert "[SHADOW] broken performance chain at prior date=2023-03-30" in stream.getvalue()


def test_shadow_performance_broken_chain_on_corrupt_prior_artifact(tmp_path: Path) -> None:
    panel = _make_panel()
    panel_path = tmp_path / "price_panel.parquet"
    panel.to_parquet(panel_path, index=False)
    out_dir = tmp_path / "shadow"
    assert main(
        [
            "--trade-date",
            "2023-03-30",
            "--start-date",
            "2022-01-03",
            "--end-date",
            "2023-03-30",
            "--output-dir",
            str(out_dir),
            "--price-cache-path",
            str(panel_path),
        ]
    ) == 0
    (out_dir / "2023-03-30" / "shadow_performance.json").write_text("{bad json")
    assert main(
        [
            "--trade-date",
            "2023-03-31",
            "--start-date",
            "2022-01-03",
            "--end-date",
            "2023-03-31",
            "--output-dir",
            str(out_dir),
            "--price-cache-path",
            str(panel_path),
        ]
    ) == 0
    perf = json.loads((out_dir / "2023-03-31" / "shadow_performance.json").read_text())
    assert perf["status"] == "BROKEN_CHAIN"
    assert perf["strategies"]["spy_benchmark"]["nav"] is None
    evaluation = json.loads((out_dir / "2023-03-31" / "shadow_evaluation.json").read_text())
    assert evaluation["strategies"]["spy_benchmark"]["status"] == "BROKEN_CHAIN"
    assert evaluation["strategies"]["spy_benchmark"]["excess_return_vs_spy"] is None
