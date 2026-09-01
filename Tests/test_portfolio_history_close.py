from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import scripts.run_portfolio_history_close as close


def _runner(returncodes: list[int], calls: list[list[str]]):
    def _run(command, *, cwd, check):
        assert cwd == Path("/tmp/caerus-close").resolve()
        assert check is False
        calls.append(command)
        return SimpleNamespace(returncode=returncodes.pop(0))

    return _run


def test_close_chain_runs_all_steps_when_green(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(close.subprocess, "run", _runner([0, 0, 0], calls))

    result = close.run_close_chain(
        repo_root=Path("/tmp/caerus-close"),
        trade_date="2026-09-01",
        send_escalation=True,
    )

    assert result["returncode"] == 0
    assert "scripts/build_portfolio_history.py" in calls[0]
    assert "scripts/build_daily_portfolio_audit.py" in calls[1]
    assert "core.portfolio_history_escalation" in calls[2]
    assert "--send" in calls[2]


def test_close_chain_always_escalates_after_history_failure(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(close.subprocess, "run", _runner([7, 0], calls))

    result = close.run_close_chain(
        repo_root=Path("/tmp/caerus-close"),
        trade_date="2026-09-01",
        send_escalation=True,
    )

    assert result["portfolio_history_returncode"] == 7
    assert result["daily_audit_returncode"] is None
    assert result["escalation_returncode"] == 0
    assert result["returncode"] == 7
    assert len(calls) == 2
    assert "core.portfolio_history_escalation" in calls[-1]


def test_close_chain_always_escalates_and_fails_after_audit_failure(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(close.subprocess, "run", _runner([0, 9, 0], calls))

    result = close.run_close_chain(
        repo_root=Path("/tmp/caerus-close"),
        trade_date="2026-09-01",
        send_escalation=True,
    )

    assert result["daily_audit_returncode"] == 9
    assert result["escalation_returncode"] == 0
    assert result["returncode"] == 9
    assert "core.portfolio_history_escalation" in calls[-1]


def test_close_chain_propagates_escalation_failure(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(close.subprocess, "run", _runner([0, 0, 4], calls))

    result = close.run_close_chain(
        repo_root=Path("/tmp/caerus-close"),
        trade_date="2026-09-01",
        send_escalation=True,
    )

    assert result["returncode"] == 4
