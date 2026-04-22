from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_wrapper_returns_success_even_when_shadow_runner_fails(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    log_dir = repo_root / "logs"
    trade_date = "2023-04-03"
    result = subprocess.run(
        [
            "bash",
            "scripts/run_shadow_candidates_daily.sh",
            "--trade-date",
            trade_date,
            "--definitely-invalid-arg",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0
    log_path = log_dir / f"shadow_{trade_date}.log"
    assert log_path.exists()
    assert "[SHADOW] failed but non-blocking" in log_path.read_text()


def test_wrapper_smoke_writes_expected_log_lines(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    panel_src = repo_root / "outputs/research/flow_detection_v1/price_panel.parquet"
    trade_date = "2026-04-21"
    out_dir = tmp_path / "shadow_out"
    result = subprocess.run(
        [
            "bash",
            "scripts/run_shadow_candidates_daily.sh",
            "--trade-date",
            trade_date,
            "--price-cache-path",
            str(panel_src),
            "--output-dir",
            str(out_dir),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0
    assert (out_dir / trade_date / "comparison.json").exists()
    log_path = repo_root / "logs" / f"shadow_{trade_date}.log"
    text = log_path.read_text()
    assert f"[SHADOW] start trade_date={trade_date}" in text
    assert f"[SHADOW] wrote {out_dir}/{trade_date}/..." in text
