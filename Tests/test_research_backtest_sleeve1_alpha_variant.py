from pathlib import Path
import subprocess
import sys

import pandas as pd


def test_sleeve1_alpha_variant_outputs_exist(tmp_path: Path):
    out_dir = tmp_path / "research"
    cmd = [
        sys.executable,
        "scripts/research_backtest_sleeve1_alpha_variant.py",
        "--start",
        "2020-01-01",
        "--end",
        "2020-06-30",
        "--output-dir",
        str(out_dir),
        "--synthetic",
        "--apply-costs",
        "--cost-bps",
        "25",
    ]
    subprocess.run(cmd, check=True)

    summary_path = out_dir / "sleeve1_alpha_variant_summary.csv"
    ts_path = out_dir / "sleeve1_alpha_variant_timeseries.csv"
    assert summary_path.exists()
    assert ts_path.exists()
    assert (out_dir / "sleeve1_alpha_variant_equity_curve.png").exists()

    summary = pd.read_csv(summary_path)
    required_summary_cols = {
        "gross_total_return",
        "gross_cagr",
        "gross_vol",
        "gross_sharpe",
        "gross_max_drawdown",
        "gross_beta_vs_spy",
        "net_total_return",
        "net_cagr",
        "net_vol",
        "net_sharpe",
        "net_max_drawdown",
        "net_beta_vs_spy",
        "avg_turnover",
        "cost_bps",
        "total_cost_drag",
    }
    assert required_summary_cols.issubset(summary.columns)

    ts = pd.read_csv(ts_path)
    assert {"date", "gross_nav", "net_nav", "spy_nav"}.issubset(ts.columns)
    assert (ts["net_nav"] != ts["gross_nav"]).any()
