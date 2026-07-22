"""Tests for the LIVE_PILOT confirm lane fixes (BLOCKER 5).

Two layers:
* script-text assertions that the fragile race is gone and the fail-loud sweep +
  execute-completion hook are wired (mirrors Tests/test_cron_confirm.py style);
* a functional test that sources scripts/live_pilot_confirm_lib.sh and drives the
  sweep with a stubbed email sender, proving no-run alerts, per-run send, and
  dedupe end to end.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Script-text assertions
# --------------------------------------------------------------------------- #

def test_confirm_cron_no_longer_uses_last_sorted_dir_race() -> None:
    text = (REPO_ROOT / "scripts" / "cron_live_pilot_confirm.sh").read_text(encoding="utf-8")
    # The racy discovery is removed.
    assert "sort | tail -1" not in text
    # It now uses the shared sweep with dedupe + fail-loud alerting.
    assert "source \"${REPO_ROOT}/scripts/live_pilot_confirm_lib.sh\"" in text
    assert "live_pilot_confirm_sweep" in text


def test_confirm_lib_fails_loud_on_no_run() -> None:
    text = (REPO_ROOT / "scripts" / "live_pilot_confirm_lib.sh").read_text(encoding="utf-8")
    # No run found -> email alert, not a silent exit 0.
    assert "live_pilot_confirm_alert" in text
    assert 'No execution run to confirm' in text
    assert "smtplib" in text  # alert really sends mail


def test_execute_cron_has_completion_hook() -> None:
    text = (REPO_ROOT / "scripts" / "cron_live_pilot_execute.sh").read_text(encoding="utf-8")
    assert "confirm_completed_runs()" in text
    # Hook is invoked on both the dry-only (submission paused) and armed paths.
    assert text.count("confirm_completed_runs") >= 3  # def + 2 call sites


# --------------------------------------------------------------------------- #
# Functional sweep test (stubbed sender)
# --------------------------------------------------------------------------- #

def _write_run(runs_root: Path, run_id: str, status: str) -> None:
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "execution_results.json").write_text(
        f'{{"run_id": "{run_id}", "status": "{status}"}}', encoding="utf-8"
    )


def _make_python_shim(shim_path: Path, send_log: Path) -> None:
    """A fake `python` that records confirmation-email sends and delegates every
    other invocation (the discovery module) to the real interpreter."""
    shim_path.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            for arg in "$@"; do
                if [[ "$arg" == "scripts.send_trading_confirmation_email" ]]; then
                    echo "$TRADING_CONFIRMATION_RUN_ROOT" >> "{send_log}"
                    exit 0
                fi
            done
            exec "{sys.executable}" "$@"
            """
        ),
        encoding="utf-8",
    )
    shim_path.chmod(shim_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_sweep(tmp_path: Path, runs_root: Path, ledger: Path, send_log: Path) -> subprocess.CompletedProcess:
    shim = tmp_path / "python_shim.sh"
    _make_python_shim(shim, send_log)
    script = textwrap.dedent(
        f"""\
        set -uo pipefail
        export PYTHON_BIN="{shim}"
        export REPORT_DATE="2026-07-10"
        export PYTHONPATH="{REPO_ROOT}"
        # SMTP intentionally unset: alert cannot send, proving fail-loud returns non-zero.
        source "{REPO_ROOT}/scripts/live_pilot_confirm_lib.sh"
        live_pilot_confirm_sweep "{runs_root}" "{ledger}"
        echo "SWEEP_RC=$?"
        """
    )
    env = dict(os.environ)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_sweep_no_runs_returns_nonzero_and_attempts_alert(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    ledger = tmp_path / "ledger.jsonl"
    send_log = tmp_path / "sends.log"
    proc = _run_sweep(tmp_path, runs, ledger, send_log)
    assert "SWEEP_RC=1" in proc.stdout, proc.stdout + proc.stderr
    assert "no LIVE_PILOT run found" in proc.stdout
    # No confirmation emails attempted when there is nothing to confirm.
    assert not send_log.exists() or send_log.read_text().strip() == ""


def test_sweep_confirms_both_runs_once_and_dedupes(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    ledger = tmp_path / "ledger.jsonl"
    send_log = tmp_path / "sends.log"
    _write_run(runs, "2026-07-10T093604-0400_live_pilot_cron_dry", "DRY_RUN_NO_SUBMISSION")
    _write_run(runs, "2026-07-10T100930-0400_live_pilot_cron_submit", "FAILED_RECONCILIATION")

    proc = _run_sweep(tmp_path, runs, ledger, send_log)
    assert "SWEEP_RC=0" in proc.stdout, proc.stdout + proc.stderr
    sent = [ln for ln in send_log.read_text().splitlines() if ln.strip()]
    assert len(sent) == 2  # both runs confirmed
    # Ledger records both.
    ledger_ids = {ln for ln in ledger.read_text().splitlines() if ln.strip()}
    assert len(ledger_ids) == 2

    # Second sweep: everything already confirmed -> no new sends.
    proc2 = _run_sweep(tmp_path, runs, ledger, send_log)
    assert "SWEEP_RC=0" in proc2.stdout
    assert "already confirmed" in proc2.stdout
    sent_after = [ln for ln in send_log.read_text().splitlines() if ln.strip()]
    assert len(sent_after) == 2  # unchanged — dedupe held


def test_sweep_confirms_only_new_run_after_late_submit(tmp_path: Path) -> None:
    """Reproduces the 07-10 timeline: dry confirmed first, submit arrives later
    and is picked up by the next sweep exactly once."""
    runs = tmp_path / "runs"
    ledger = tmp_path / "ledger.jsonl"
    send_log = tmp_path / "sends.log"

    _write_run(runs, "2026-07-10T093604-0400_live_pilot_cron_dry", "DRY_RUN_NO_SUBMISSION")
    _run_sweep(tmp_path, runs, ledger, send_log)
    assert len([ln for ln in send_log.read_text().splitlines() if ln.strip()]) == 1

    # Later armed submit finishes AFTER the first sweep.
    _write_run(runs, "2026-07-10T100930-0400_live_pilot_cron_submit", "FAILED_RECONCILIATION")
    proc = _run_sweep(tmp_path, runs, ledger, send_log)
    assert "SWEEP_RC=0" in proc.stdout, proc.stdout + proc.stderr
    sent = [ln for ln in send_log.read_text().splitlines() if ln.strip()]
    assert len(sent) == 2  # the previously-unreported submit is now confirmed
    assert sent[1].endswith("_live_pilot_cron_submit")


def test_sweep_fails_loud_when_dry_run_masks_unconfirmable_block(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    ledger = tmp_path / "ledger.jsonl"
    send_log = tmp_path / "sends.log"
    dry = "2026-07-10T093604-0400_live_pilot_cron_dry"
    gate = "2026-07-10T093601-0400_live_pilot_cron_gate"
    _write_run(runs, dry, "DRY_RUN_NO_SUBMISSION")
    pointer = tmp_path / "workflow" / "2026-07-10" / "live_pilot_execution.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(
        '{"run_id":"' + gate + '","status":"blocked","status_message":"live_pilot_deploy_sha_drift"}',
        encoding="utf-8",
    )

    proc = _run_sweep(tmp_path, runs, ledger, send_log)
    assert "SWEEP_RC=1" in proc.stdout, proc.stdout + proc.stderr
    assert "terminal LIVE_PILOT workflow outcome is not confirmable" in proc.stdout
