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
    assert "PLAN_PATH:" in task_text
    assert "aiops/20260228_010203_abc1234" in task_text


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

    def _fake_run(command: list[str], cwd: Path, check: bool):
        calls.append(command)
        if command[0] == "codex":
            return _Result(0)
        if command[:2] == ["aiops", "verify"]:
            return _Result(7)
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr("aiops.dispatch.subprocess.run", _fake_run)

    exit_code = run_dispatch(run_id)

    assert exit_code == 7
    assert calls[0] == ["codex", str(plan_path)]
    assert calls[1] == ["aiops", "verify", str(snapshot_path), "--mode", "HARDEN"]
