from pathlib import Path
import subprocess
import sys

import pandas as pd


REQUIRED_COLUMNS = {
    "seed",
    "window_id",
    "start_date",
    "end_date",
    "avg_turnover",
    "cost_bps",
    "gross_port_cagr",
    "net_port_cagr",
    "gross_port_max_drawdown",
    "net_port_max_drawdown",
    "net_excess_cagr",
}


def test_sleeve1_alpha_variant_random_windows_outputs_exist(tmp_path: Path):
    out_dir = tmp_path / "research"
    cmd = [
        sys.executable,
        "scripts/research_backtest_sleeve1_alpha_variant_random_windows.py",
        "--seed",
        "1",
        "--n3",
        "2",
        "--n5",
        "2",
        "--outdir",
        str(out_dir),
        "--synthetic",
        "--apply-costs",
        "--cost-bps",
        "25",
    ]
    subprocess.run(cmd, check=True)

    out3 = out_dir / "sleeve1_alpha_random_windows_3y.csv"
    out5 = out_dir / "sleeve1_alpha_random_windows_5y.csv"
    summary = out_dir / "sleeve1_alpha_random_windows_summary.csv"

    assert out3.exists()
    assert out5.exists()
    assert summary.exists()

    df3 = pd.read_csv(out3)
    df5 = pd.read_csv(out5)
    s = pd.read_csv(summary)

    assert REQUIRED_COLUMNS.issubset(df3.columns)
    assert REQUIRED_COLUMNS.issubset(df5.columns)
    assert "net_excess_cagr" in df3.columns
    assert "net_excess_cagr" in df5.columns
    assert {"net_port_cagr", "net_port_max_drawdown", "net_excess_cagr"}.issubset(set(s["metric"].dropna()))
