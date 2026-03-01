from __future__ import annotations

from pathlib import Path

from aiops.run_all import run_all


def _write_valid_spec(spec_path: Path) -> None:
    spec_path.write_text(
        "\n".join(
            [
                "OBJECTIVE: test objective",
                "MODE: BUILD",
                "PROJECT_TYPE: test",
                "RISK_TIER: Low",
                "",
                "## FILES",
                "- create: none",
                "",
                "## ACCEPTANCE CRITERIA",
                "- deterministic",
            ]
        ),
        encoding="utf-8",
    )


def _fake_plan_factory(tmp_path: Path, run_id: str):
    def _fake_plan(spec_path: Path, mode_override: str | None = None):
        run_dir = tmp_path / "reports" / "ai_runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "plan.md").write_text(
            "\n".join(
                [
                    f"SPEC_PATH: {spec_path}",
                    f"MODE: {mode_override or 'BUILD'}",
                    "PLAN_HASH: abcdef",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (run_dir / "spec_snapshot.md").write_text("MODE: BUILD\n", encoding="utf-8")
        return (0, run_id)

    return _fake_plan


def test_run_all_missing_codex_exits_2_and_writes_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    run_id = "20260301_010101_abc1234"
    spec_path = tmp_path / "specs" / "sample.md"
    spec_path.parent.mkdir(parents=True)
    _write_valid_spec(spec_path)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("aiops.run_all.run_plan", _fake_plan_factory(tmp_path, run_id))
    monkeypatch.setattr("aiops.dispatch.shutil.which", lambda _: None)

    called = {"run": False, "verify": False}

    def _fake_run_for_run_id(_: str) -> int:
        called["run"] = True
        return 0

    def _fake_verify(_: Path, mode_override: str | None = None) -> int:
        called["verify"] = True
        return 0

    monkeypatch.setattr("aiops.run_all.run_for_run_id", _fake_run_for_run_id)
    monkeypatch.setattr("aiops.run_all.run_verify", _fake_verify)

    exit_code = run_all(spec_path, mode_override="BUILD")

    run_dir = tmp_path / "reports" / "ai_runs" / run_id
    summary_path = run_dir / "run_all_summary.md"
    task_path = run_dir / "codex_task.txt"

    assert exit_code == 2
    assert summary_path.exists()
    assert task_path.exists()
    assert called["run"] is False
    assert called["verify"] is False
    assert "RUN_ALL_STATUS: NEEDS_OPERATOR" in capsys.readouterr().out


def test_run_all_codex_success_exits_0(tmp_path: Path, monkeypatch, capsys) -> None:
    run_id = "20260301_020202_def5678"
    spec_path = tmp_path / "specs" / "sample.md"
    spec_path.parent.mkdir(parents=True)
    _write_valid_spec(spec_path)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("aiops.run_all.run_plan", _fake_plan_factory(tmp_path, run_id))
    monkeypatch.setattr("aiops.dispatch.shutil.which", lambda _: "/usr/local/bin/codex")

    calls: list[list[str]] = []

    class _Result:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

    def _fake_subprocess_run(command: list[str], cwd: Path, capture_output: bool, text: bool, check: bool, timeout: int):
        calls.append(command)
        if command[:2] == ["codex", "exec"]:
            return _Result(0)
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr("aiops.dispatch.subprocess.run", _fake_subprocess_run)
    monkeypatch.setattr("aiops.run_all.run_for_run_id", lambda _: 0)
    monkeypatch.setattr("aiops.run_all.run_verify", lambda *_args, **_kwargs: 0)

    exit_code = run_all(spec_path, mode_override="BUILD")

    run_dir = tmp_path / "reports" / "ai_runs" / run_id
    summary_text = (run_dir / "run_all_summary.md").read_text(encoding="utf-8")

    assert exit_code == 0
    assert calls and calls[0][:2] == ["codex", "exec"]
    assert "RUN_ALL_STATUS: OK" in summary_text
    assert "RUN_ALL_STATUS: OK" in capsys.readouterr().out


def test_run_all_dispatch_failure_exits_5(tmp_path: Path, monkeypatch, capsys) -> None:
    run_id = "20260301_030303_ghi9012"
    spec_path = tmp_path / "specs" / "sample.md"
    spec_path.parent.mkdir(parents=True)
    _write_valid_spec(spec_path)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("aiops.run_all.run_plan", _fake_plan_factory(tmp_path, run_id))
    monkeypatch.setattr("aiops.dispatch.shutil.which", lambda _: "/usr/local/bin/codex")

    class _Result:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

    def _fake_subprocess_run(command: list[str], cwd: Path, capture_output: bool, text: bool, check: bool, timeout: int):
        if command[:2] == ["codex", "exec"]:
            return _Result(9)
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr("aiops.dispatch.subprocess.run", _fake_subprocess_run)
    monkeypatch.setattr("aiops.run_all.run_for_run_id", lambda _: 0)
    monkeypatch.setattr("aiops.run_all.run_verify", lambda *_args, **_kwargs: 0)

    exit_code = run_all(spec_path, mode_override="BUILD")

    run_dir = tmp_path / "reports" / "ai_runs" / run_id
    summary_text = (run_dir / "run_all_summary.md").read_text(encoding="utf-8")

    assert exit_code == 5
    assert "RUN_ALL_STATUS: FAILED" in summary_text
    assert "| dispatch | 9 |" in summary_text
    assert "RUN_ALL_STATUS: FAILED" in capsys.readouterr().out


def test_run_all_verify_failure_exits_3(tmp_path: Path, monkeypatch, capsys) -> None:
    run_id = "20260301_040404_jkl3456"
    spec_path = tmp_path / "specs" / "sample.md"
    spec_path.parent.mkdir(parents=True)
    _write_valid_spec(spec_path)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("aiops.run_all.run_plan", _fake_plan_factory(tmp_path, run_id))
    monkeypatch.setattr("aiops.dispatch.shutil.which", lambda _: "/usr/local/bin/codex")

    class _Result:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

    def _fake_subprocess_run(command: list[str], cwd: Path, capture_output: bool, text: bool, check: bool, timeout: int):
        if command[:2] == ["codex", "exec"]:
            return _Result(0)
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr("aiops.dispatch.subprocess.run", _fake_subprocess_run)
    monkeypatch.setattr("aiops.run_all.run_for_run_id", lambda _: 0)
    monkeypatch.setattr("aiops.run_all.run_verify", lambda *_args, **_kwargs: 1)

    exit_code = run_all(spec_path, mode_override="BUILD")

    run_dir = tmp_path / "reports" / "ai_runs" / run_id
    summary_text = (run_dir / "run_all_summary.md").read_text(encoding="utf-8")

    assert exit_code == 3
    assert "RUN_ALL_STATUS: FAILED" in summary_text
    assert "| verify | 1 |" in summary_text
    assert "RUN_ALL_STATUS: FAILED" in capsys.readouterr().out
