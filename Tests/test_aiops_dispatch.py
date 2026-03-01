from __future__ import annotations

from pathlib import Path

from aiops.dispatch import run_dispatch


def test_dispatch_missing_run_dir_exits_nonzero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    exit_code = run_dispatch("20260228_010203_abc1234")

    assert exit_code != 0


def test_dispatch_missing_plan_md_exits_nonzero(tmp_path: Path, monkeypatch) -> None:
    run_id = "20260228_010203_abc1234"
    run_dir = tmp_path / "reports" / "ai_runs" / run_id
    run_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    exit_code = run_dispatch(run_id)

    assert exit_code != 0


def test_dispatch_codex_missing_writes_task_and_exits_2(tmp_path: Path, monkeypatch) -> None:
    run_id = "20260228_010203_abc1234"
    run_dir = tmp_path / "reports" / "ai_runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "plan.md").write_text("MODE: BUILD\nPLAN_HASH: abcdef\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("aiops.dispatch.shutil.which", lambda _: None)

    exit_code = run_dispatch(run_id)

    task_path = run_dir / "codex_task.txt"
    assert exit_code == 2
    assert task_path.exists()
    task_text = task_path.read_text(encoding="utf-8")
    
    # Verify all required fields are present
    assert f"RUN_ID: {run_id}" in task_text
    assert "PLAN_PATH:" in task_text
    assert "SPEC_SNAPSHOT_PATH:" in task_text
    assert "MODE: BUILD" in task_text
    assert "TEST_COMMAND: pytest -q" in task_text
    assert "VERIFY_COMMAND: aiops verify" in task_text
    assert f"BRANCH: aiops/{run_id}" in task_text
    assert "EXECUTION_CHECKLIST:" in task_text
    assert "Implement strictly per plan contract." in task_text
    assert "Run TEST_COMMAND and verify all tests pass." in task_text
    assert "Run VERIFY_COMMAND (must exit 0)." in task_text
    assert "Ensure git status is clean." in task_text
    assert f"Commit with message containing RUN_ID {run_id}." in task_text
    assert "Push branch." in task_text


def test_dispatch_runs_verify_with_mode_from_plan(tmp_path: Path, monkeypatch) -> None:
    run_id = "20260228_010203_abc1234"
    run_dir = tmp_path / "reports" / "ai_runs" / run_id
    run_dir.mkdir(parents=True)
    plan_path = run_dir / "plan.md"
    snapshot_path = run_dir / "spec_snapshot.md"
    plan_path.write_text("MODE: HARDEN\nPLAN_HASH: abcdef\n", encoding="utf-8")
    snapshot_path.write_text("OBJECTIVE: x\nMODE: HARDEN\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("aiops.dispatch.shutil.which", lambda _: "/usr/local/bin/codex")

    calls: list[list[str]] = []

    class _Result:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

    def _fake_run(command: list[str], cwd: Path, capture_output: bool, text: bool, check: bool, timeout: int | None = None):
        calls.append(command)
        if command[:2] == ["codex", "exec"]:
            return _Result(0)
        if command[:2] == ["aiops", "verify"]:
            return _Result(7)
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr("aiops.dispatch.subprocess.run", _fake_run)

    exit_code = run_dispatch(run_id)

    assert exit_code == 7
    assert calls[0][0:2] == ["codex", "exec"]
    assert f"RUN_ID: {run_id}" in calls[0][2]
    assert f"PLAN_PATH: {plan_path}" in calls[0][2]
    assert f"SPEC_SNAPSHOT_PATH: {snapshot_path}" in calls[0][2]
    assert calls[1] == ["aiops", "verify", str(snapshot_path), "--mode", "HARDEN"]


def test_dispatch_does_not_print_secret_env_values(tmp_path: Path, monkeypatch, capsys) -> None:
    run_id = "20260228_010203_abc1234"
    run_dir = tmp_path / "reports" / "ai_runs" / run_id
    run_dir.mkdir(parents=True)
    plan_path = run_dir / "plan.md"
    plan_path.write_text("MODE: BUILD\nPLAN_HASH: abcdef\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("aiops.dispatch.shutil.which", lambda _: "/usr/local/bin/codex")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value")

    class _Result:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

    def _fake_run(command: list[str], cwd: Path, capture_output: bool, text: bool, check: bool, timeout: int | None = None):
        if command[:2] == ["codex", "exec"]:
            return _Result(9)
        if command[:2] == ["aiops", "verify"]:
            return _Result(0)
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr("aiops.dispatch.subprocess.run", _fake_run)

    exit_code = run_dispatch(run_id)

    captured = capsys.readouterr()
    combined = f"{captured.out}\n{captured.err}"
    assert exit_code == 9
    assert "OPENAI_API_KEY" not in combined
    assert "sk-test-secret-value" not in combined
