from pathlib import Path
import subprocess
import sys

import pandas as pd


REQUIRED_SUMMARY_COLUMNS = {
    "window_name",
    "start_date",
    "end_date",
    "port_total_return",
    "port_cagr",
    "port_vol",
    "port_sharpe",
    "port_max_dd",
    "spy_total_return",
    "spy_cagr",
    "spy_vol",
    "spy_sharpe",
    "spy_max_dd",
    "avg_turnover",
    "avg_holdings",
}


def test_research_backtest_fast_mode(tmp_path: Path):
    out_dir = tmp_path / "research"
    cmd = [
        sys.executable,
        "scripts/research_backtest_sleeve1_2009_2025.py",
        "--fast-mode",
        "--output-dir",
        str(out_dir),
        "--synthetic",
    ]
    subprocess.run(cmd, check=True)

    summary = out_dir / "sleeve1_backtest_2009_2025_summary.csv"
    timeseries = out_dir / "sleeve1_backtest_2009_2025_timeseries.csv"

    assert summary.exists(), "Expected summary output csv"
    assert timeseries.exists(), "Expected timeseries output csv"

    summary_df = pd.read_csv(summary)
    ts_df = pd.read_csv(timeseries)

    assert REQUIRED_SUMMARY_COLUMNS.issubset(summary_df.columns)
    assert len(ts_df) > 200
    assert {"portfolio_nav", "spy_nav"}.issubset(ts_df.columns)
