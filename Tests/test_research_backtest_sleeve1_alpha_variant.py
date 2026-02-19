from pathlib import Path
import subprocess
import sys


def test_sleeve1_alpha_variant_outputs_exist(tmp_path: Path):
    out_dir = tmp_path / "research"
    cmd = [
        sys.executable,
        "scripts/research_backtest_sleeve1_alpha_variant.py",
        "--start",
        "2020-01-01",
        "--end",
        "2024-12-31",
        "--output-dir",
        str(out_dir),
        "--synthetic",
    ]
    subprocess.run(cmd, check=True)

    assert (out_dir / "sleeve1_alpha_variant_summary.csv").exists()
    assert (out_dir / "sleeve1_alpha_variant_timeseries.csv").exists()
    assert (out_dir / "sleeve1_alpha_variant_equity_curve.png").exists()
