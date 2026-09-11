from __future__ import annotations

import json
import hashlib
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
    observe_submitted_run,
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


def test_submitted_unfilled_refreshes_original_run_until_filled() -> None:
    initial = _outcome(
        exit_code=1,
        retryable=False,
        reason="SUBMITTED_UNFILLED",
        submitted_count=5,
    )
    refresh_calls: list[int] = []
    sleeps: list[float] = []

    def refresh_once(attempt: int) -> AttemptOutcome:
        refresh_calls.append(attempt)
        return _outcome(
            exit_code=0 if attempt == 3 else 1,
            retryable=False,
            reason="CLEAN" if attempt == 3 else "SUBMITTED_UNFILLED",
            submitted_count=5,
        )

    outcome = observe_submitted_run(
        initial=initial,
        refresh_once=refresh_once,
        max_attempts=5,
        delay_seconds=2,
        sleep_fn=sleeps.append,
    )

    assert outcome.exit_code == 0
    assert outcome.reason_code == "CLEAN"
    assert outcome.fill_refresh_count == 3
    assert refresh_calls == [1, 2, 3]
    assert sleeps == [2.0, 2.0]


def test_submitted_unfilled_exhausts_read_only_observation_window() -> None:
    initial = _outcome(
        exit_code=1,
        retryable=False,
        reason="SUBMITTED_UNFILLED",
        submitted_count=5,
    )
    refresh_calls: list[int] = []

    def refresh_once(attempt: int) -> AttemptOutcome:
        refresh_calls.append(attempt)
        return initial

    outcome = observe_submitted_run(
        initial=initial,
        refresh_once=refresh_once,
        max_attempts=3,
        delay_seconds=0,
        sleep_fn=lambda _delay: None,
    )

    assert outcome.exit_code == 1
    assert outcome.reason_code == "SUBMITTED_UNFILLED"
    assert outcome.fill_refresh_count == 3
    assert refresh_calls == [1, 2, 3]


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


def test_inspect_attempt_recognizes_exact_submitted_unfilled_count(tmp_path: Path) -> None:
    trade_date = "2026-08-12"
    run_root = tmp_path / "outputs" / "paper_lane" / "runs" / "exact-open"
    run_root.mkdir(parents=True)
    (run_root / "execution_results.json").write_text(
        json.dumps(
            {
                "raw_execution_status": "SUBMITTED_UNFILLED",
                "terminal_status": "FAILED_RECONCILIATION",
                "reason_code": "exact_order_not_terminal:order-1",
                "orders_submitted_count": 1,
            }
        ),
        encoding="utf-8",
    )
    write_paper_lane_pointers(
        trade_date=trade_date,
        run_id="exact-open",
        run_root=str(run_root),
        terminal_status="FAILED_RECONCILIATION",
        reason_code="paper_posttrade_verification_failed:exact_order_not_terminal",
        workspace_root=str(tmp_path),
    )

    outcome = inspect_attempt(tmp_path, trade_date, 1)

    assert outcome.reason_code == "SUBMITTED_UNFILLED"
    assert outcome.submitted_count == 1
    assert outcome.retryable is False


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
    confirm_kwargs, _ = _confirmation_fixture(tmp_path, monkeypatch, outcome=outcome)
    def send_with_receipt(command, **_kwargs):
        calls.append(list(command))
        _delivery_receipt(confirm_kwargs, sent=True)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(paper_execution_retry.subprocess, "run", send_with_receipt)

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


def test_cron_enters_global_retry_lock_before_replacing_canonical_pointer() -> None:
    script = Path("scripts/cron_execute.sh").read_text(encoding="utf-8")
    lock_entry = 'flock -n 9'
    pointer_claim = 'bootstrap_pointer "running" "paper_execution_bootstrap"'
    assert script.index(lock_entry) < script.index(pointer_claim)
    assert 'if [[ "${CAERUS_PAPER_RETRY_CHILD:-0}" != "1" ]]' in script
    assert '--inherited-lock-fd 9' in script
    harness_exec = script.index('exec "${PYTHON_BIN}" -m scripts.paper_execution_retry')
    assert script.index('export ALPACA_BASE_URL="https://paper-api.alpaca.markets"') < harness_exec
    assert script.index('export CAERUS_REQUIRE_EXACT_EXECUTION_PLAN="1"') < harness_exec


def test_cron_never_uses_empty_or_untrusted_submit_run_root() -> None:
    script = Path("scripts/cron_execute.sh").read_text(encoding="utf-8")
    assert 'EXPECTED_SUBMIT_RUN_ROOT="${PAPER_LANE_ROOT}/runs/${SUBMIT_RUN_ID}"' in script
    assert 'SUBMIT_RUN_ROOT="${EXPECTED_SUBMIT_RUN_ROOT}"' in script
    assert 'exact_execution_run_root_identity_mismatch' in script


def test_cron_cannot_exit_success_after_emergency_pointer_fallback() -> None:
    script = Path("scripts/cron_execute.sh").read_text(encoding="utf-8")
    assert "The emergency pointer is intentionally a failure artifact" in script
    assert "canonical terminal execution pointer publication failed" in script
    assert "if ! write_paper_pointer" in script
    assert '"${FINAL_TERMINAL}"' in script


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



def _confirmation_fixture(tmp_path, monkeypatch, outcome=None):
    outcome = outcome or _outcome(exit_code=0, retryable=False, reason="success", submitted_count=3)
    day = "2026-07-15"
    run_root = tmp_path / outcome.run_root
    run_root.mkdir(parents=True, exist_ok=True)
    operator = {"execution_source": "exact_execution_plan_v3", "mode": "PAPER", "run_id": outcome.run_id, "trade_date": day,
                "terminal_status": "SUBMITTED", "terminal_outcome": "RECONCILED_SUCCESS", "reconciliation_status": "CLEAN"}
    results = {**operator, "terminal_outcome": "RECONCILED_SUCCESS", "reconciliation_status": "CLEAN",
               "execution_target_attainment_required": True, "execution_target_attainment_status": "OK_TARGET_ATTAINED"}
    (run_root / "operator_summary.json").write_text(json.dumps(operator))
    (run_root / "execution_results.json").write_text(json.dumps(results))
    (run_root / "audit").mkdir(exist_ok=True)
    (run_root / "audit/execution_integrity.json").write_text(json.dumps({"status": "OK"}))
    pointer = {"run_id": outcome.run_id, "run_root": outcome.run_root, "trade_date": day,
               "mode": "PAPER", "stage": "execution", "status": "success"}
    monkeypatch.setattr(paper_execution_retry, "read_trade_stage_pointer", lambda *a, **k: pointer)
    marker = tmp_path / "outputs/workflow" / day / "paper_execution_retry_late_confirmation.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    return dict(repo_root=tmp_path, trade_date=day, outcome=outcome, confirmation_path=marker), pointer


def _delivery_receipt(kwargs, *, sent, override=None):
    run_root = kwargs["repo_root"] / kwargs["outcome"].run_root
    operator = json.loads((run_root / "operator_summary.json").read_text())
    digest = hashlib.sha256(json.dumps(operator, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    receipt = {"schema_version": "caerus.trading_confirmation_delivery.v1", "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
               "run_id": kwargs["outcome"].run_id, "trade_date": kwargs["trade_date"], "operator_summary_sha256": digest,
               "confirmation_email_sent": sent, **(override or {})}
    (run_root / "trading_confirmation_delivery.json").write_text(json.dumps(receipt))


def test_late_confirmation_explicitly_enables_and_scopes_sender_and_dedupes(tmp_path, monkeypatch):
    kwargs, _ = _confirmation_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("EMAIL_TRADING_CONFIRMATION", "0")
    monkeypatch.setenv("EMAIL_DRY_RUN", "1")
    calls = []
    def send(command, *, env, **unused):
        calls.append(command)
        assert env["EMAIL_TRADING_CONFIRMATION"] == "1"
        assert env["EMAIL_DRY_RUN"] == "1"  # Global dry run is never overridden.
        assert env["REPORT_DATE"] == kwargs["trade_date"]
        assert env["TRADING_CONFIRMATION_RUN_ROOT"] == str((tmp_path / kwargs["outcome"].run_root).resolve())
        assert env["TRADING_CONFIRMATION_RESULTS_PATH"].endswith("/execution_results.json")
        _delivery_receipt(kwargs, sent=True)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(paper_execution_retry.subprocess, "run", send)
    first = paper_execution_retry._send_late_confirmation(**kwargs)
    second = paper_execution_retry._send_late_confirmation(**kwargs)
    assert first["status"] == second["status"] == "SENT"
    assert second["attempted"] is False and second["deduplicated"] is True
    assert len(calls) == 1


def test_successful_suppression_is_not_sent_and_can_retry_only_with_same_receipt(tmp_path, monkeypatch):
    kwargs, _ = _confirmation_fixture(tmp_path, monkeypatch)
    sent_flags = iter([False, True])
    def sender(*a, **k):
        _delivery_receipt(kwargs, sent=next(sent_flags))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(paper_execution_retry.subprocess, "run", sender)
    first = paper_execution_retry._send_late_confirmation(**kwargs)
    assert first["status"] == "SUPPRESSED"
    assert paper_execution_retry._send_late_confirmation(**kwargs)["status"] == "SENT"


@pytest.mark.parametrize("case", ["missing", "wrong_run", "wrong_hash", "wrong_schema", "stale", "failure", "failure_false_receipt", "exception"])
def test_missing_wrong_or_ambiguous_delivery_never_claims_sent_or_retries(tmp_path, monkeypatch, case):
    kwargs, _ = _confirmation_fixture(tmp_path, monkeypatch)
    calls = []
    def sender(*a, **k):
        calls.append(1)
        if case == "exception":
            raise OSError("fixture process error")
        overrides = {"wrong_run": {"run_id": "another-run"}, "wrong_hash": {"operator_summary_sha256": "f"*64},
                     "wrong_schema": {"schema_version": "wrong"}, "stale": {"recorded_at": "2020-01-01T00:00:00+00:00"}}
        if case in overrides:
            _delivery_receipt(kwargs, sent=True, override=overrides[case])
        if case == "failure_false_receipt": _delivery_receipt(kwargs, sent=False)
        return SimpleNamespace(returncode=1 if case.startswith("failure") else 0)
    monkeypatch.setattr(paper_execution_retry.subprocess, "run", sender)
    assert paper_execution_retry._send_late_confirmation(**kwargs)["status"] == "SEND_UNKNOWN"
    assert paper_execution_retry._send_late_confirmation(**kwargs)["status"] == "PRIOR_ATTEMPT_BLOCKS_SEND"
    assert len(calls) == 1


@pytest.mark.parametrize("case", ["pointer_failed", "pointer_run", "no_action", "target_failed", "wrong_operator"])
def test_late_confirmation_independently_requires_successful_current_run(tmp_path, monkeypatch, case):
    kwargs, pointer = _confirmation_fixture(tmp_path, monkeypatch)
    run_root = tmp_path / kwargs["outcome"].run_root
    if case == "pointer_failed": pointer["status"] = "failed"
    if case == "pointer_run": pointer["run_id"] = "wrong"
    if case in {"no_action", "target_failed"}:
        results = json.loads((run_root / "execution_results.json").read_text())
        if case == "no_action": results["terminal_outcome"] = "AUTHORIZED_NO_TRADE"
        else: results["execution_target_attainment_status"] = "FAIL"
        (run_root / "execution_results.json").write_text(json.dumps(results))
    if case == "wrong_operator": (run_root / "operator_summary.json").write_text("{}")
    monkeypatch.setattr(paper_execution_retry.subprocess, "run", lambda *a, **k: pytest.fail("unconfirmed run sent"))
    assert paper_execution_retry._send_late_confirmation(**kwargs)["status"] == "NOT_CONFIRMABLE"
    assert not kwargs["confirmation_path"].exists()


def test_legacy_ambiguous_attempt_marker_blocks_sender(tmp_path, monkeypatch):
    kwargs, _ = _confirmation_fixture(tmp_path, monkeypatch)
    kwargs["confirmation_path"].write_text(json.dumps({"status": "ATTEMPTING"}))
    monkeypatch.setattr(paper_execution_retry.subprocess, "run", lambda *a, **k: pytest.fail("duplicate send risk"))
    assert paper_execution_retry._send_late_confirmation(**kwargs)["status"] == "PRIOR_ATTEMPT_BLOCKS_SEND"


def test_modified_no_send_receipt_does_not_authorize_retry(tmp_path, monkeypatch):
    kwargs, _ = _confirmation_fixture(tmp_path, monkeypatch)
    calls = []
    def sender(*a, **k):
        calls.append(1)
        _delivery_receipt(kwargs, sent=False)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(paper_execution_retry.subprocess, "run", sender)
    assert paper_execution_retry._send_late_confirmation(**kwargs)["status"] == "SUPPRESSED"
    _delivery_receipt(kwargs, sent=False)  # A different timestamp/hash is not that attempt's proof.
    assert paper_execution_retry._send_late_confirmation(**kwargs)["status"] == "PRIOR_ATTEMPT_BLOCKS_SEND"
    assert calls == [1]


def test_exit_failure_after_valid_sent_receipt_does_not_duplicate(tmp_path, monkeypatch):
    kwargs, _ = _confirmation_fixture(tmp_path, monkeypatch)
    calls = []
    def sender(*a, **k):
        calls.append(1)
        _delivery_receipt(kwargs, sent=True)
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(paper_execution_retry.subprocess, "run", sender)
    result = paper_execution_retry._send_late_confirmation(**kwargs)
    assert result["status"] == "SENT" and result["exit_code"] == 1
    assert paper_execution_retry._send_late_confirmation(**kwargs)["attempted"] is False
    assert calls == [1]


@pytest.mark.parametrize("field,value", [("terminal_status", "FAILED_RECONCILIATION"),
    ("terminal_outcome", "SYSTEM_FAILURE"), ("reconciliation_status", "FAILED"), ("terminal_status", None)])
def test_late_confirmation_requires_canonical_operator_terminal_truth(tmp_path, monkeypatch, field, value):
    kwargs, _ = _confirmation_fixture(tmp_path, monkeypatch)
    path = tmp_path / kwargs["outcome"].run_root / "operator_summary.json"
    operator = json.loads(path.read_text())
    if value is None: operator.pop(field)
    else: operator[field] = value
    path.write_text(json.dumps(operator))
    monkeypatch.setattr(paper_execution_retry.subprocess, "run", lambda *a, **k: pytest.fail("contradictory operator sent"))
    assert paper_execution_retry._send_late_confirmation(**kwargs)["status"] == "NOT_CONFIRMABLE"


@pytest.mark.parametrize("missing", [False, True])
def test_late_confirmation_requires_integrity_ok(tmp_path, monkeypatch, missing):
    kwargs, _ = _confirmation_fixture(tmp_path, monkeypatch)
    path = tmp_path / kwargs["outcome"].run_root / "audit/execution_integrity.json"
    if missing: path.unlink()
    else: path.write_text(json.dumps({"status": "FAIL"}))
    monkeypatch.setattr(paper_execution_retry.subprocess, "run", lambda *a, **k: pytest.fail("unverified integrity sent"))
    assert paper_execution_retry._send_late_confirmation(**kwargs)["status"] == "NOT_CONFIRMABLE"


@pytest.mark.parametrize("when", ["before_send", "after_send"])
def test_receipt_io_failure_is_unknown_without_escaping_or_retrying(tmp_path, monkeypatch, when):
    kwargs, _ = _confirmation_fixture(tmp_path, monkeypatch)
    calls = []
    actual_read = Path.read_bytes
    fail_read = [when == "before_send"]
    def read_bytes(path):
        if path.name == "trading_confirmation_delivery.json" and fail_read[0]:
            raise PermissionError("fixture receipt I/O failure")
        return actual_read(path)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    def sender(*a, **k):
        calls.append(1)
        _delivery_receipt(kwargs, sent=True)
        fail_read[0] = True
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(paper_execution_retry.subprocess, "run", sender)
    assert paper_execution_retry._send_late_confirmation(**kwargs)["status"] == "SEND_UNKNOWN"
    assert paper_execution_retry._send_late_confirmation(**kwargs)["status"] == "SEND_UNKNOWN"
    assert len(calls) == (0 if when == "before_send" else 1)
