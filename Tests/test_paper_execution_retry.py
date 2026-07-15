from __future__ import annotations

import json
import sys
import datetime as dt
import fcntl
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.broker_retry_policy import is_retryable_broker_read_error
from scripts import paper_execution_retry
from scripts.paper_execution_retry import (
    PAPER_EXECUTION_RETRY_DELAYS_SECONDS,
    AttemptOutcome,
    inspect_attempt,
    run_retry_harness,
)
from scripts.paper_lane_write_execution_pointer import write_paper_lane_pointers


def _outcome(
    *,
    exit_code: int,
    retryable: bool,
    reason: str = "paper_broker_snapshot_transient_failed",
    submitted_count: int = 0,
    attempt: int = 1,
) -> AttemptOutcome:
    return AttemptOutcome(
        exit_code=exit_code,
        retryable=retryable,
        reason_code=reason,
        submitted_count=submitted_count,
        run_id=f"paper-attempt-{attempt}",
        run_root=f"outputs/paper_lane/runs/paper-attempt-{attempt}",
        error=reason,
    )


@pytest.mark.parametrize("success_attempt", [2, 3, 4, 5])
def test_exact_retry_schedule_recovers_at_each_retry_position(success_attempt: int) -> None:
    sleeps: list[float] = []
    published: list[dict[str, object]] = []

    def run_once(attempt: int) -> AttemptOutcome:
        if attempt == success_attempt:
            return _outcome(exit_code=0, retryable=False, reason="success", attempt=attempt)
        return _outcome(exit_code=1, retryable=True, attempt=attempt)

    outcome, payload = run_retry_harness(
        run_once=run_once,
        sleep_fn=sleeps.append,
        publish_fn=lambda report: published.append(dict(report)),
    )

    assert outcome.exit_code == 0
    assert payload["status"] == "RECOVERED_AFTER_RETRY"
    assert payload["attempt_count"] == success_attempt
    assert sleeps == list(PAPER_EXECUTION_RETRY_DELAYS_SECONDS[: success_attempt - 1])
    assert published[-1]["escalation_required"] is False


def test_fifth_failure_exhausts_once_and_escalates_with_zero_submissions() -> None:
    sleeps: list[float] = []

    outcome, payload = run_retry_harness(
        run_once=lambda attempt: _outcome(exit_code=1, retryable=True, attempt=attempt),
        sleep_fn=sleeps.append,
    )

    assert outcome.exit_code == 1
    assert sleeps == [30.0, 60.0, 300.0, 3600.0]
    assert payload["status"] == "ESCALATION_REQUIRED"
    assert payload["attempt_count"] == 5
    assert payload["submitted_count"] == 0
    assert payload["escalation_required"] is True
    assert payload["total_retry_delay_seconds"] == 3990


def test_nonretryable_failure_escalates_immediately_without_sleeping() -> None:
    sleeps: list[float] = []

    outcome, payload = run_retry_harness(
        run_once=lambda attempt: _outcome(
            exit_code=1,
            retryable=False,
            reason="paper_broker_snapshot_non_retryable",
            attempt=attempt,
        ),
        sleep_fn=sleeps.append,
    )

    assert outcome.reason_code == "paper_broker_snapshot_non_retryable"
    assert sleeps == []
    assert payload["attempt_count"] == 1
    assert payload["status"] == "ESCALATION_REQUIRED"


def test_post_submission_failure_is_never_retried() -> None:
    sleeps: list[float] = []

    outcome, payload = run_retry_harness(
        run_once=lambda attempt: _outcome(
            exit_code=1,
            retryable=False,
            reason="failed_reconciliation",
            submitted_count=1,
            attempt=attempt,
        ),
        sleep_fn=sleeps.append,
    )

    assert outcome.submitted_count == 1
    assert sleeps == []
    assert payload["attempt_count"] == 1
    assert payload["escalation_required"] is True


def test_retry_classifier_uses_status_and_exception_chain_but_rejects_auth() -> None:
    class HttpError(RuntimeError):
        status_code = 503

    wrapped = RuntimeError("Alpaca account read failed")
    wrapped.__cause__ = HttpError("upstream unavailable")

    assert is_retryable_broker_read_error(wrapped) is True
    assert is_retryable_broker_read_error('{"code":50410000,"message":"request timed out"}') is True
    assert is_retryable_broker_read_error("HTTP 501 not implemented") is True
    assert is_retryable_broker_read_error("HTTP 401 request timed out") is False
    assert is_retryable_broker_read_error("401 unauthorized: invalid API key") is False
    assert is_retryable_broker_read_error("403 forbidden") is False


def test_inspect_attempt_requires_known_transient_reason_and_zero_submissions(tmp_path: Path) -> None:
    trade_date = "2026-07-15"
    run_root = tmp_path / "outputs" / "paper_lane" / "runs" / "paper-x"
    run_root.mkdir(parents=True)
    (run_root / "execution_results.json").write_text(
        json.dumps({"submitted_count": 0, "halt_reason": "paper_broker_snapshot_transient_failed"}),
        encoding="utf-8",
    )
    write_paper_lane_pointers(
        trade_date=trade_date,
        run_id="paper-x",
        run_root=str(run_root),
        terminal_status="BLOCKED",
        reason_code="paper_broker_snapshot_transient_failed",
        workspace_root=str(tmp_path),
    )

    outcome = inspect_attempt(tmp_path, trade_date, 1)

    assert outcome.retryable is True
    assert outcome.submitted_count == 0
    assert outcome.run_root == str(run_root)

    stale = inspect_attempt(
        tmp_path,
        trade_date,
        1,
        not_before_ns=(tmp_path / "outputs" / "workflow" / trade_date / "execution.json").stat().st_mtime_ns + 1,
    )
    assert stale.retryable is False


def test_main_runs_normal_confirmation_after_fifth_attempt_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = _outcome(exit_code=0, retryable=False, reason="success", attempt=5)
    payload = {"status": "RECOVERED_AFTER_RETRY", "attempt_count": 5}
    calls: list[list[str]] = []

    monkeypatch.setattr(
        paper_execution_retry,
        "run_retry_harness",
        lambda **_kwargs: (outcome, payload),
    )
    monkeypatch.setattr(paper_execution_retry, "_confirmation_is_due", lambda _trade_date: True)
    monkeypatch.setattr(
        paper_execution_retry.subprocess,
        "run",
        lambda command, **_kwargs: calls.append(list(command)) or SimpleNamespace(returncode=0),
    )

    exit_code = paper_execution_retry.main(
        ["--trade-date", "2026-07-15", "--repo-root", str(tmp_path)]
    )

    artifact = json.loads(
        (tmp_path / "outputs" / "workflow" / "2026-07-15" / "paper_execution_retry.json").read_text(
            encoding="utf-8"
        )
    )
    assert exit_code == 0
    assert calls == [[sys.executable, "-m", "scripts.send_trading_confirmation_email"]]
    assert artifact["late_confirmation_attempted"] is True
    assert artifact["late_confirmation_exit_code"] == 0


def test_main_sends_and_records_escalation_after_exhaustion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = _outcome(exit_code=1, retryable=True, attempt=5)
    payload = {
        "status": "ESCALATION_REQUIRED",
        "attempt_count": 5,
        "last_reason_code": outcome.reason_code,
        "last_error": outcome.error,
        "submitted_count": 0,
    }
    escalations: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        paper_execution_retry,
        "run_retry_harness",
        lambda **_kwargs: (outcome, payload),
    )
    monkeypatch.setattr(
        paper_execution_retry,
        "_send_escalation",
        lambda trade_date, report: escalations.append((trade_date, dict(report))),
    )

    exit_code = paper_execution_retry.main(
        ["--trade-date", "2026-07-15", "--repo-root", str(tmp_path)]
    )

    artifact = json.loads(
        (tmp_path / "outputs" / "workflow" / "2026-07-15" / "paper_execution_retry.json").read_text(
            encoding="utf-8"
        )
    )
    assert exit_code == 1
    assert escalations[0][0] == "2026-07-15"
    assert artifact["escalation_delivery_attempted"] is True
    assert artifact["escalation_delivery_status"] == "SENT"
    assert artifact["status"] == "RETRY_EXHAUSTED"
    assert escalations[0][1]["status"] == "RETRY_EXHAUSTED"
    pointer = json.loads(
        (tmp_path / "outputs" / "workflow" / "2026-07-15" / "execution.json").read_text(
            encoding="utf-8"
        )
    )
    assert pointer["status"] == "failed_blocked"
    assert pointer["substatus"] == "paper_execution_retry_exhausted"


def test_confirmation_due_uses_trade_date_and_ten_am_et() -> None:
    before = dt.datetime(2026, 7, 15, 9, 59, tzinfo=dt.timezone(dt.timedelta(hours=-4)))
    after = dt.datetime(2026, 7, 15, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=-4)))

    assert paper_execution_retry._confirmation_is_due("2026-07-15", before) is False
    assert paper_execution_retry._confirmation_is_due("2026-07-15", after) is True
    assert paper_execution_retry._confirmation_is_due("2026-07-14", after) is False


def test_global_lock_blocks_overlapping_dates(tmp_path: Path) -> None:
    lock_path = tmp_path / "outputs" / "workflow" / "paper_execution_retry.lock"
    lock_path.parent.mkdir(parents=True)
    with lock_path.open("a+", encoding="utf-8") as held_lock:
        fcntl.flock(held_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        exit_code = paper_execution_retry.main(
            ["--trade-date", "2026-07-16", "--repo-root", str(tmp_path)]
        )

    assert exit_code == 75


def test_cli_does_not_accept_an_arbitrary_child_script() -> None:
    with pytest.raises(SystemExit):
        paper_execution_retry._parse_args(
            [
                "--trade-date",
                "2026-07-15",
                "--repo-root",
                "/tmp/repo",
                "--script",
                "/tmp/not-paper.sh",
            ]
        )
