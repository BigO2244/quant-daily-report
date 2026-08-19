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
import scripts.finalize_generic_live_v1_posttrade as posttrade_cli
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


@pytest.mark.parametrize(
    ("child_body", "expected_returncode"),
    [
        (
            "#!/usr/bin/env bash\necho SENTINEL_SECRET\necho SENTINEL_SECRET >&2\nexit 78\n",
            78,
        ),
        (
            "#!/usr/bin/env bash\n"
            "echo SENTINEL_SECRET\n"
            "printf '{\"trigger\":\"ORDER_BREAK\"}\\n' >\"${CAERUS_GENERIC_LIVE_GUARD_TEST_ROOT}/.caerus/generic_live_v1_state/session_gate.json\"\n"
            "exit 1\n",
            1,
        ),
    ],
)
def test_external_guard_fully_rolls_back_bootstrap_and_terminal_order_failures(
    tmp_path: Path, child_body: str, expected_returncode: int,
) -> None:
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
    _write(child, child_body, 0o700)
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
    assert result.returncode == expected_returncode
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


@pytest.mark.parametrize(
    "status", ["ORDER_BREAK_REARMED", "UNRESOLVED_ORDER_REARMED"],
)
def test_order_break_and_unresolved_results_both_require_outer_rollback(
    status: str,
) -> None:
    assert runner._requires_external_rollback(status) is True
    assert runner._requires_external_rollback("FILLED_REARMED") is False


def test_posttrade_cli_missing_link_performs_full_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = tmp_path / "inputs"
    state = tmp_path / "state"
    paper = tmp_path / "paper"
    ops = tmp_path / "ops"
    rollback = state / "rollback"
    for directory in (inputs, state, paper, ops, rollback):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)

    input_payloads = {
        "submission.json": {"preflight_hash": "a" * 64},
        "plan.json": {"content_hash": "b" * 64},
        "order.json": {},
        "broker-orders.json": {"broker_orders": []},
        "broker-fills.json": {"broker_fills": []},
        "ending-state.json": {},
        "journal.json": {"journal_entries": []},
        "valuations.json": {"valuations": []},
        "deployment-policy.json": {},
        "known-sleeves.json": {"known_sleeve_ids": ["caerus_lyra"]},
        "deployment-state.json": {},
        "capital.json": {},
    }
    for name, payload in input_payloads.items():
        _write(inputs / name, json.dumps(payload))
    missing_other_lane_audits = inputs / "missing-other-lane-audits.json"
    gate = state / "session-gate.json"
    _write(gate, json.dumps({"status": "DISARMED_FOR_EXACT_SESSION"}))
    active = ops / "generic_live_v1.env"
    backup = ops / "generic_live_v1.env.rollback"
    _write(active, "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=1\nNEW=1\n")
    _write(backup, "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=0\nPRIOR=1\n")
    paper_paths = [paper / "cron_precompute.sh", paper / "cron_execute.sh"]
    for index, path in enumerate(paper_paths):
        _write(path, f"paper-{index}\n")
    paper_before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paper_paths
    }

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(mode=0o700)
    cron_store = tmp_path / "crontab.txt"
    exact = "36 9 19 8 * /fixed/guard --effective-session 2026-08-19 # CAERUS_GENERIC_LIVE_V1_SESSION=2026-08-19"
    cron_store.write_text(f"PAPER_LINE\n{exact}\n", encoding="utf-8")
    _write(
        fake_bin / "crontab",
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == '-l' ]]; then cat \"${FAKE_CRONTAB_STORE}\"; exit 0; fi\n"
        "if [[ \"${1:-}\" == '-' ]]; then cat >\"${FAKE_CRONTAB_STORE}\"; exit 0; fi\n"
        "exit 2\n",
        0o700,
    )
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CRONTAB_STORE", str(cron_store))
    monkeypatch.setattr(posttrade_cli, "CANONICAL_OPS_ROOT", ops)
    monkeypatch.setattr(posttrade_cli, "CANONICAL_ACTIVE_CONFIG", active)
    monkeypatch.setattr(posttrade_cli, "CANONICAL_BACKUP_CONFIG", backup)

    argv = [
        "--input-root", str(inputs), "--state-root", str(state),
        "--submission-result", str(inputs / "submission.json"),
        "--exact-plan", str(inputs / "plan.json"),
        "--order-lifecycle", str(inputs / "order.json"),
        "--broker-orders", str(inputs / "broker-orders.json"),
        "--broker-fills", str(inputs / "broker-fills.json"),
        "--ending-state", str(inputs / "ending-state.json"),
        "--existing-journal", str(inputs / "journal.json"),
        "--prior-valuations", str(inputs / "valuations.json"),
        "--deployment-policy", str(inputs / "deployment-policy.json"),
        "--known-sleeve-ids", str(inputs / "known-sleeves.json"),
        "--deployment-state", str(inputs / "deployment-state.json"),
        "--capital", str(inputs / "capital.json"),
        "--other-lane-audits", str(missing_other_lane_audits),
        "--session-gate-path", str(gate),
        "--base-result-path", str(state / "base.json"),
        "--closure-result-path", str(state / "closure.json"),
        "--reporting-artifact-directory", str(state / "reporting"),
        "--exact-cron-line", exact,
        "--active-config-path", str(active),
        "--backup-config-path", str(backup),
        "--paper-root", str(paper),
        "--rollback-evidence-directory", str(rollback),
        "--finalized-at", "2026-08-19T21:00:00+00:00",
        "--reconciled-at", "2026-08-19T20:59:00+00:00",
        "--valuation-date", "2026-08-19",
    ]
    for path in paper_paths:
        argv.extend(["--paper-path", str(path)])

    with pytest.raises(GenericLiveV1OpsError, match="required path does not exist"):
        posttrade_cli.main(argv)

    gate_payload = json.loads(gate.read_text())
    assert gate_payload["status"] == "ARMED"
    assert gate_payload["trigger"] == "REPORTING_BREAK"
    assert exact not in cron_store.read_text()
    assert "PAPER_LINE" in cron_store.read_text()
    assert active.read_bytes() == backup.read_bytes()
    assert b"PRIOR=1" in active.read_bytes()
    assert paper_before == {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paper_paths
    }
    evidence = json.loads((rollback / "reporting_break.json").read_text())
    assert evidence["status"] == "ROLLED_BACK_ARMED"
    assert evidence["cron_exact_line_removed"] is True
    assert evidence["config_action"] == "RESTORED_BACKUP"
    assert evidence["paper_bytes_unchanged"] is True
    assert not (state / "generic_live_v1.env").exists()


def test_posttrade_cli_accepts_only_exact_canonical_config_paths() -> None:
    posttrade_cli._require_canonical_config_paths(
        posttrade_cli.CANONICAL_ACTIVE_CONFIG,
        posttrade_cli.CANONICAL_BACKUP_CONFIG,
    )
    with pytest.raises(RuntimeError, match="config paths are fixed"):
        posttrade_cli._require_canonical_config_paths(
            Path("/home/brettolson/.caerus/generic_live_v1_state/generic_live_v1.env"),
            posttrade_cli.CANONICAL_BACKUP_CONFIG,
        )


def test_posttrade_cli_connected_mode_collects_broker_truth_then_calls_raw_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = tmp_path / "inputs"
    state = tmp_path / "state"
    paper = tmp_path / "paper"
    ops = tmp_path / "ops"
    rollback = state / "rollback"
    for directory in (inputs, state, paper, ops, rollback):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    payloads = {
        "submission.json": {"preflight_hash": "a" * 64},
        "plan.json": {"content_hash": "b" * 64},
        "journal.json": {"journal_entries": []},
        "valuations.json": {"valuations": []},
        "policy.json": {},
        "sleeves.json": {"known_sleeve_ids": ["caerus_lyra"]},
        "deployment.json": {}, "capital.json": {},
        "other.json": {"lane_audits": []},
    }
    for name, payload in payloads.items():
        _write(inputs / name, json.dumps(payload))
    gate = state / "gate.json"
    _write(gate, "{}")
    paper_paths = [paper / "one", paper / "two"]
    for path in paper_paths:
        _write(path, "paper")
    active = ops / "generic_live_v1.env"
    backup = ops / "generic_live_v1.env.rollback"
    monkeypatch.setattr(posttrade_cli, "CANONICAL_OPS_ROOT", ops)
    monkeypatch.setattr(posttrade_cli, "CANONICAL_ACTIVE_CONFIG", active)
    monkeypatch.setattr(posttrade_cli, "CANONICAL_BACKUP_CONFIG", backup)
    broker = object()
    monkeypatch.setattr(posttrade_cli.AlpacaBroker, "from_env", lambda: broker)
    captured = {}

    def collect(**kwargs):
        captured.update(kwargs)
        return {"closure": {"status": "GREEN_REARMED"}}

    monkeypatch.setattr(
        posttrade_cli, "collect_and_finalize_generic_live_v1_posttrade", collect,
    )
    argv = [
        "--input-root", str(inputs), "--state-root", str(state),
        "--submission-result", str(inputs / "submission.json"),
        "--exact-plan", str(inputs / "plan.json"), "--collect-from-broker",
        "--broker-evidence-directory", str(state / "broker-evidence"),
        "--published-pointer-path", str(state / "published.json"),
        "--existing-journal", str(inputs / "journal.json"),
        "--prior-valuations", str(inputs / "valuations.json"),
        "--deployment-policy", str(inputs / "policy.json"),
        "--known-sleeve-ids", str(inputs / "sleeves.json"),
        "--deployment-state", str(inputs / "deployment.json"),
        "--capital", str(inputs / "capital.json"),
        "--other-lane-audits", str(inputs / "other.json"),
        "--session-gate-path", str(gate),
        "--base-result-path", str(state / "base.json"),
        "--closure-result-path", str(state / "closure.json"),
        "--reporting-artifact-directory", str(state / "reporting"),
        "--exact-cron-line", "GENERIC", "--active-config-path", str(active),
        "--backup-config-path", str(backup), "--paper-root", str(paper),
        "--rollback-evidence-directory", str(rollback),
        "--reconciled-at", "2026-08-19T20:00:00+00:00",
        "--valuation-date", "2026-08-19",
        "--finalized-at", "2026-08-19T20:00:01+00:00",
    ]
    for path in paper_paths:
        argv.extend(["--paper-path", str(path)])

    assert posttrade_cli.main(argv) == 0
    assert captured["broker"] is broker
    assert captured["submission_result"] == payloads["submission.json"]
    assert captured["exact_plan"] == payloads["plan.json"]
    assert captured["published_pointer_path"] == state / "published.json"
