from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _make_fake_venv(tmp_path: Path) -> Path:
    venv = tmp_path / "fake_venv"
    bin_dir = venv / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "activate").write_text("export PATH=\"$VIRTUAL_ENV/bin:$PATH\"\n")
    python = bin_dir / "python3"
    python.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
trade_date=""
output_dir=""
for ((i=1; i <= $#; i++)); do
    arg="${!i}"
    next_index=$((i + 1))
    next_value="${!next_index-}"
    case "${arg}" in
        --definitely-invalid-arg)
            exit 2
            ;;
        --trade-date)
            trade_date="${next_value}"
            ;;
        --output-dir)
            output_dir="${next_value}"
            ;;
    esac
done
if [[ -z "${trade_date}" || -z "${output_dir}" ]]; then
    exit 2
fi
dated_dir="${output_dir}/${trade_date}"
mkdir -p "${dated_dir}"
printf '# Shadow Comparison\\n\\n- Trade date: %s\\n' "${trade_date}" > "${dated_dir}/comparison.md"
if [[ -n "${FAKE_SHADOW_REASON:-}" ]]; then
    printf '{"trade_date":"%s","reason_code":"%s"}\\n' "${trade_date}" "${FAKE_SHADOW_REASON}" > "${dated_dir}/comparison.json"
else
    printf '{"trade_date":"%s"}\\n' "${trade_date}" > "${dated_dir}/comparison.json"
fi
printf '{"trade_date":"%s"}\\n' "${trade_date}" > "${dated_dir}/delta.json"
printf '{"trade_date":"%s"}\\n' "${trade_date}" > "${dated_dir}/shadow_evaluation.json"
exit 0
"""
    )
    python.chmod(0o755)
    return venv


def test_wrapper_returns_success_even_when_shadow_runner_fails(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    fake_venv = _make_fake_venv(tmp_path)
    log_dir = repo_root / "logs"
    trade_date = "2023-04-03"
    (log_dir / f"shadow_{trade_date}.log").unlink(missing_ok=True)
    result = subprocess.run(
        [
            "bash",
            "scripts/run_shadow_candidates_daily.sh",
            "--trade-date",
            trade_date,
            "--definitely-invalid-arg",
        ],
        cwd=repo_root,
        env={
            "CAERUS_VENV_DIR": str(fake_venv),
            "HOME": str(tmp_path),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0
    log_path = log_dir / f"shadow_{trade_date}.log"
    assert log_path.exists()
    assert "[SHADOW] failed but non-blocking" in log_path.read_text()
    status = json.loads((repo_root / "outputs" / "workflow" / trade_date / "shadow_generate.json").read_text())
    assert status["status"] == "FAILED"
    assert status["step"] == "generate"


def test_wrapper_smoke_writes_expected_log_lines(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    fake_venv = _make_fake_venv(tmp_path)
    trade_date = "2026-04-21"
    (repo_root / "logs" / f"shadow_{trade_date}.log").unlink(missing_ok=True)
    out_dir = tmp_path / "shadow_out"
    result = subprocess.run(
        [
            "bash",
            "scripts/run_shadow_candidates_daily.sh",
            "--trade-date",
            trade_date,
            "--output-dir",
            str(out_dir),
        ],
        cwd=repo_root,
        env={
            "CAERUS_VENV_DIR": str(fake_venv),
            "HOME": str(tmp_path),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0
    assert (out_dir / trade_date / "comparison.json").exists()
    latest_dir = out_dir / "latest"
    assert (latest_dir / "comparison.md").exists()
    assert (latest_dir / "comparison.json").exists()
    assert (latest_dir / "delta.json").exists()
    assert (latest_dir / "shadow_evaluation.json").exists()
    assert trade_date in (latest_dir / "comparison.md").read_text()
    log_path = repo_root / "logs" / f"shadow_{trade_date}.log"
    text = log_path.read_text()
    assert f"[SHADOW] start trade_date={trade_date}" in text
    assert f"[SHADOW] wrote {out_dir}/{trade_date}/..." in text
    assert f"[SHADOW] latest artifacts published to {latest_dir}/" in text
    assert "[SHADOW] desktop path unavailable; latest artifacts published to" in text
    assert "[SHADOW] updated Desktop Orion.md" not in text
    workflow_dir = repo_root / "outputs" / "workflow" / trade_date
    generate_status = json.loads((workflow_dir / "shadow_generate.json").read_text())
    latest_status = json.loads((workflow_dir / "shadow_latest.json").read_text())
    reconciliation_status = json.loads((workflow_dir / "shadow_reconciliation.json").read_text())
    summary_status = json.loads((workflow_dir / "shadow.json").read_text())
    assert generate_status["status"] == "OK"
    assert latest_status["status"] == "OK"
    assert reconciliation_status["status"] in {"OK", "FAILED"}
    assert summary_status["latest_publish_status"] == "OK"


def test_wrapper_logs_local_hydration_guidance_for_stale_cache(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    fake_venv = _make_fake_venv(tmp_path)
    trade_date = "2026-04-30"
    (repo_root / "logs" / f"shadow_{trade_date}.log").unlink(missing_ok=True)
    out_dir = tmp_path / "shadow_out"
    result = subprocess.run(
        [
            "bash",
            "scripts/run_shadow_candidates_daily.sh",
            "--trade-date",
            trade_date,
            "--output-dir",
            str(out_dir),
        ],
        cwd=repo_root,
        env={
            "CAERUS_VENV_DIR": str(fake_venv),
            "FAKE_SHADOW_REASON": "PRICE_CACHE_STALE",
            "HOME": str(tmp_path),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0
    text = (repo_root / "logs" / f"shadow_{trade_date}.log").read_text()
    assert "[SHADOW] price cache stale; run local hydration workflow to refresh." in text


def test_local_hydration_workflow_dry_run_prints_expected_commands(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            "bash",
            "scripts/hydrate_shadow_locally_and_sync.sh",
            "--trade-date",
            "2026-04-30",
            "--remote-host",
            "vm.example",
            "--remote-repo",
            "/srv/quant",
            "--dry-run",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    output = result.stdout
    assert "python3 -m research.shadow_tracking.run" in output
    assert "--allow-download" in output
    assert "rsync -av outputs/research/flow_detection_v1/price_panel.parquet" in output
    assert "outputs/shadow_candidates/2026-04-30/" in output
    assert "outputs/shadow_candidates/latest/" in output
    assert "python3 -m scripts.live_vs_shadow_reconciliation --trade-date 2026-04-30" in output
    assert "python3 -m scripts.caerus_daily_health_check --trade-date 2026-04-30" in output
