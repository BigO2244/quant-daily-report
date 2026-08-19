from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from core.generic_live_v1_ops import (
    CRON_TZ_LINE,
    GENERIC_LIVE_V1_BREAK_TRIGGERS,
    GenericLiveV1OpsError,
    perform_generic_live_v1_rollback,
)
from core.generic_live_v1_submission import rearm_generic_live_v1_session
from authority.lane_exact_plan import canonical_json
from scripts.manage_generic_live_v1_cron import update_crontab
import scripts.run_generic_live_v1_session as runner


ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, value: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)


@pytest.mark.parametrize("trigger", sorted(GENERIC_LIVE_V1_BREAK_TRIGGERS))
def test_every_named_break_rearms_rolls_back_and_proves_paper_unchanged(
    tmp_path: Path, trigger: str,
) -> None:
    protected = tmp_path / "protected"
    protected.mkdir(mode=0o700)
    active = protected / "generic.env"
    backup = protected / "generic.env.rollback"
    state = protected / "gate.json"
    evidence = protected / f"rollback-{trigger}.json"
    paper_a = protected / "paper-a.sh"
    paper_b = protected / "paper-b.txt"
    _write(active, "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=1\nNEW=1\n")
    _write(backup, "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=0\nOLD=1\n")
    _write(paper_a, "paper execution bytes\n")
    _write(paper_b, "paper schedule bytes\n")
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (paper_a, paper_b)}
    exact = "36 9 19 8 * /fixed/guard --effective-session 2026-08-19 # CAERUS_GENERIC_LIVE_V1_SESSION=2026-08-19"
    current = f"MAILTO=ops\n{CRON_TZ_LINE}\nPAPER_CRON_LINE\n{exact}\n"
    installed: list[str] = []

    def rearm(observed: str) -> dict:
        assert observed == trigger
        return rearm_generic_live_v1_session(
            state_path=state,
            preflight_hash="a" * 64,
            plan_hash="b" * 64,
            rearmed_at="2026-08-19T13:31:00+00:00",
            trigger=observed,
        )

    result = perform_generic_live_v1_rollback(
        trigger=trigger,
        rearm_action=rearm,
        current_crontab=current,
        exact_cron_line=exact,
        apply_crontab=installed.append,
        active_config_path=active,
        backup_config_path=backup,
        paper_paths=[paper_a, paper_b],
        evidence_path=evidence,
        allowed_roots=[protected],
        rolled_back_at="2026-08-19T13:31:01+00:00",
    )
    assert json.loads(state.read_text())["status"] == "ARMED"
    assert exact not in installed[0]
    assert "PAPER_CRON_LINE" in installed[0]
    assert "OLD=1" in active.read_text()
    assert backup.exists()
    assert result["status"] == "ROLLED_BACK_ARMED"
    assert result["paper_bytes_unchanged"] is True
    assert result["cron_exact_line_removed"] is True
    assert json.loads(evidence.read_text()) == result
    assert stat.S_IMODE(evidence.stat().st_mode) == 0o600
    assert before == {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (paper_a, paper_b)
    }


def test_rollback_removes_new_generic_config_when_no_prior_config(tmp_path: Path) -> None:
    protected = tmp_path / "protected"
    protected.mkdir(mode=0o700)
    active = protected / "generic.env"
    paper = protected / "paper"
    _write(active, "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=1\n")
    _write(paper, "paper\n")
    result = perform_generic_live_v1_rollback(
        trigger="PREFLIGHT_BREAK",
        rearm_action=lambda trigger: {"status": "ARMED", "content_hash": "a" * 64},
        current_crontab="PAPER\n",
        exact_cron_line="GENERIC",
        apply_crontab=lambda value: None,
        active_config_path=active,
        backup_config_path=protected / "missing.rollback",
        paper_paths=[paper],
        evidence_path=protected / "evidence.json",
        allowed_roots=[protected],
        rolled_back_at="2026-08-19T13:31:01+00:00",
    )
    assert result["config_action"] == "REMOVED_NO_PRIOR_CONFIG"
    assert not active.exists()


def test_cron_timezone_is_installed_once_and_conflicts_fail_closed() -> None:
    exact = "GENERIC # CAERUS_GENERIC_LIVE_V1_SESSION=2026-08-19"
    updated = update_crontab("PAPER\n", exact_line=exact, install=True)
    assert updated.splitlines() == ["PAPER", CRON_TZ_LINE, exact]
    duplicate = update_crontab(updated + CRON_TZ_LINE + "\n", exact_line=exact, install=True)
    assert duplicate.splitlines().count(CRON_TZ_LINE) == 1
    positioned = update_crontab(
        f"PAPER_A\n{CRON_TZ_LINE}\nPAPER_B\n", exact_line=exact, install=True
    )
    assert positioned.splitlines()[:3] == ["PAPER_A", CRON_TZ_LINE, "PAPER_B"]
    with pytest.raises(GenericLiveV1OpsError, match="timezone differs"):
        update_crontab("CRON_TZ=UTC\n", exact_line=exact, install=True)


def test_external_guard_rolls_back_pre_python_failure_without_secret_leak(tmp_path: Path) -> None:
    test_home = tmp_path / "home"
    repo = test_home / "quant-daily-report"
    scripts = repo / "scripts"
    logs = repo / "logs"
    ops = test_home / ".caerus"
    state = ops / "generic_live_v1_state"
    for directory in (scripts, logs, ops, state):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    guard = scripts / "generic_live_v1_bootstrap_guard.sh"
    shutil.copy2(ROOT / "scripts/generic_live_v1_bootstrap_guard.sh", guard)
    guard.chmod(0o700)
    child = scripts / "cron_generic_live_v1.sh"
    _write(
        child,
        "#!/usr/bin/env bash\necho SENTINEL_SECRET\necho SENTINEL_SECRET >&2\nexit 78\n",
        0o700,
    )
    paper_paths = [scripts / "cron_precompute.sh", scripts / "cron_execute.sh", scripts / "crontab.txt"]
    for index, paper in enumerate(paper_paths):
        _write(paper, f"paper-{index}\n", 0o700 if paper.suffix == ".sh" else 0o600)
    paper_before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paper_paths}
    _write(ops / "generic_live_v1.env", "SENTINEL_SECRET=NEW\n")
    _write(ops / "generic_live_v1.env.rollback", "SAFE_PRIOR=1\n")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(mode=0o700)
    crontab_store = tmp_path / "crontab.txt"
    exact = (
        f"36 9 19 8 * {guard} --effective-session 2026-08-19 >> {logs / 'cron_generic_live_v1.log'} 2>&1 "
        "# CAERUS_GENERIC_LIVE_V1_SESSION=2026-08-19"
    )
    lookalike = exact.replace("36 9 19", "37 9 19", 1)
    crontab_store.write_text(f"PAPER_LINE\n{CRON_TZ_LINE}\n{lookalike}\n{exact}\n")
    fake_crontab = fake_bin / "crontab"
    _write(
        fake_crontab,
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == '-l' ]]; then cat \"${FAKE_CRONTAB_STORE}\"; exit 0; fi\n"
        "if [[ \"${1:-}\" == '-' ]]; then cat >\"${FAKE_CRONTAB_STORE}\"; exit 0; fi\n"
        "exit 2\n",
        0o700,
    )
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "FAKE_CRONTAB_STORE": str(crontab_store),
            "CAERUS_GENERIC_LIVE_GUARD_TEST_MODE": "1",
            "CAERUS_GENERIC_LIVE_GUARD_TEST_ROOT": str(test_home),
            "CAERUS_GENERIC_LIVE_GUARD_TEST_PYTHON": "/missing/python",
            "CAERUS_SECRET_SENTINEL": "SENTINEL_SECRET",
        }
    )
    result = subprocess.run(
        [str(guard), "--effective-session", "2026-08-19"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 78
    assert "SENTINEL_SECRET" not in result.stdout + result.stderr
    gate = json.loads((state / "session_gate.json").read_text())
    assert gate["status"] == "ARMED"
    gate_body = dict(gate)
    recorded_hash = gate_body.pop("content_hash")
    assert recorded_hash == hashlib.sha256(canonical_json(gate_body).encode()).hexdigest()
    assert "SAFE_PRIOR=1" in (ops / "generic_live_v1.env").read_text()
    assert exact not in crontab_store.read_text()
    assert lookalike in crontab_store.read_text()
    assert "PAPER_LINE" in crontab_store.read_text()
    assert paper_before == {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paper_paths
    }
    evidence = list((state / "rollback_evidence").glob("rollback-*.txt"))
    assert len(evidence) == 1
    assert "paper_bytes_unchanged=true" in evidence[0].read_text()
    assert "SENTINEL_SECRET" not in evidence[0].read_text()


def test_runner_masks_forced_secret_from_stdout_and_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_with_secret() -> int:
        raise RuntimeError("provider failure SENTINEL_SECRET")

    monkeypatch.setattr(runner, "main", fail_with_secret)
    assert runner.safe_entrypoint() == 1
    captured = capsys.readouterr()
    assert "SENTINEL_SECRET" not in captured.out + captured.err
    assert "failed closed" in captured.err
