from __future__ import annotations

from pathlib import Path

from aiops.run import run_end_to_end


def test_run_fails_if_parse_fails(tmp_path: Path, monkeypatch) -> None:
    """Run should exit non-zero if spec parse fails."""
    spec_path = tmp_path / "missing.md"
    monkeypatch.chdir(tmp_path)

    exit_code = run_end_to_end(spec_path, mode_override="BUILD")

    assert exit_code != 0


def test_run_produces_run_directory_and_invokes_dispatch(tmp_path: Path, monkeypatch) -> None:
    """Run should create plan artifacts and invoke dispatch with RUN_ID."""
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        "OBJECTIVE: Test run command.\n"
        "MODE: BUILD\n"
        "\n"
        "## FILES\n"
        "\n"
        "create:\n"
        "- test.py\n"
        "\n"
        "## ACCEPTANCE CRITERIA\n"
        "\n"
        "- Deterministic behavior\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"available": True, "short_sha": "abc1234"})

    from datetime import datetime, timezone

    fixed_dt = datetime(2026, 2, 28, 10, 30, 45, tzinfo=timezone.utc)
    monkeypatch.setattr("aiops.plan.now_local", lambda: fixed_dt)

    dispatch_calls: list[str] = []

    def _mock_dispatch(run_id: str) -> int:
        dispatch_calls.append(run_id)
        return 0

    monkeypatch.setattr("aiops.run.run_dispatch", _mock_dispatch)

    exit_code = run_end_to_end(spec_path, mode_override="BUILD")

    assert exit_code == 0
    assert len(dispatch_calls) == 1
    run_id = dispatch_calls[0]
    assert run_id == "20260228_103045_abc1234"
    run_dir = tmp_path / "reports" / "ai_runs" / run_id
    assert run_dir.exists()
    assert (run_dir / "plan.md").exists()
    assert (run_dir / "spec_snapshot.md").exists()


def test_run_returns_dispatch_exit_code_on_success(tmp_path: Path, monkeypatch) -> None:
    """Run should return the exit code from dispatch when dispatch succeeds."""
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        "OBJECTIVE: Test run command.\n"
        "MODE: EXPLORE\n"
        "\n"
        "## FILES\n"
        "\n"
        "modify:\n"
        "- test.py\n"
        "\n"
        "## ACCEPTANCE CRITERIA\n"
        "\n"
        "- Behavior validated\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"available": True, "short_sha": "xyz789"})

    from datetime import datetime, timezone

    fixed_dt = datetime(2026, 2, 28, 14, 20, 10, tzinfo=timezone.utc)
    monkeypatch.setattr("aiops.plan.now_local", lambda: fixed_dt)

    def _mock_dispatch(run_id: str) -> int:
        return 0

    monkeypatch.setattr("aiops.run.run_dispatch", _mock_dispatch)

    exit_code = run_end_to_end(spec_path, mode_override="EXPLORE")

    assert exit_code == 0


def test_run_returns_dispatch_exit_code_on_failure(tmp_path: Path, monkeypatch) -> None:
    """Run should return the exit code from dispatch when dispatch fails."""
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        "OBJECTIVE: Test run command.\n"
        "MODE: HARDEN\n"
        "\n"
        "## FILES\n"
        "\n"
        "modify:\n"
        "- test.py\n"
        "\n"
        "## ACCEPTANCE CRITERIA\n"
        "\n"
        "- All gates pass\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"available": True, "short_sha": "def456"})

    from datetime import datetime, timezone

    fixed_dt = datetime(2026, 2, 28, 16, 45, 30, tzinfo=timezone.utc)
    monkeypatch.setattr("aiops.plan.now_local", lambda: fixed_dt)

    def _mock_dispatch(run_id: str) -> int:
        return 2

    monkeypatch.setattr("aiops.run.run_dispatch", _mock_dispatch)

    exit_code = run_end_to_end(spec_path, mode_override="HARDEN")

    assert exit_code == 2
