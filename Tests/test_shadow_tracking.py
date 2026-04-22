from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from research.shadow_tracking.run import main
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
            "2023-04-01",
            "--start-date",
            "2022-01-03",
            "--end-date",
            "2023-04-01",
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
    assert (dated_dir / "comparison.json").exists()
    assert (dated_dir / "comparison.md").exists()
    assert (out_dir / "performance" / "shadow_nav_series.csv").exists()
    assert (out_dir / "performance" / "shadow_summary.json").exists()
    assert not (out_dir / "paper_state").exists()


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
            "2023-04-01",
            "--start-date",
            "2022-01-03",
            "--end-date",
            "2023-04-01",
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
