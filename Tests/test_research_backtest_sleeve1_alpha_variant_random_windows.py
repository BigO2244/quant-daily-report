from pathlib import Path
import subprocess
import sys

import pandas as pd


REQUIRED_COLUMNS = {
    "seed",
    "window_id",
    "start_date",
    "end_date",
    "port_total_return",
    "port_cagr",
    "port_vol",
    "port_sharpe",
    "port_max_drawdown",
    "port_beta_vs_spy",
    "spy_total_return",
    "spy_cagr",
    "spy_vol",
    "spy_sharpe",
    "spy_max_drawdown",
    "excess_cagr",
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

    assert REQUIRED_COLUMNS.issubset(df3.columns)
    assert REQUIRED_COLUMNS.issubset(df5.columns)
