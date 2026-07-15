"""Lane-wide retry orchestration for transient PAPER pre-submission failures.

The harness runs ``scripts/cron_execute.sh`` once immediately, then retries the
entire paper lane after 30 seconds, 1 minute, 5 minutes, and 1 hour only when
the failed attempt proves that no orders were submitted and the failure is a
transient broker-read error. Live trading never uses this harness.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

from core.broker_retry_policy import is_retryable_broker_read_error
from core.run_pointer import read_trade_stage_pointer
from paper.run_manager import safe_write_text
from scripts.paper_lane_write_execution_pointer import write_paper_lane_pointers


PAPER_EXECUTION_RETRY_DELAYS_SECONDS = (30, 60, 300, 3600)
TRANSIENT_PAPER_FAILURE_REASONS = {
    "paper_lane_capital_cap_transient_read_failed",
    "paper_broker_snapshot_transient_failed",
}


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _sanitized_error(value: object) -> str:
    text = str(value or "")
    for key in (
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "SMTP_PASSWORD",
        "EMAIL_APP_PASSWORD",
    ):
        secret = str(os.environ.get(key) or "")
        if secret:
            text = text.replace(secret, "<redacted>")
    return text[:2000]


@dataclass(frozen=True)
class AttemptOutcome:
    exit_code: int
    retryable: bool
    reason_code: str
    submitted_count: int
    run_id: str
    run_root: str
    error: str


def inspect_attempt(
    repo_root: Path,
    trade_date: str,
    exit_code: int,
    *,
    not_before_ns: int | None = None,
) -> AttemptOutcome:
    pointer_path = repo_root / "outputs" / "workflow" / trade_date / "execution.json"
    pointer = read_trade_stage_pointer(trade_date, "execution", workspace_root=repo_root) or {}
    pointer_fresh = True
    if not_before_ns is not None:
        try:
            pointer_fresh = pointer_path.stat().st_mtime_ns >= not_before_ns
        except OSError:
            pointer_fresh = False
    pointer_is_paper_execution = (
        str(pointer.get("mode") or "").strip().upper() == "PAPER"
        and str(pointer.get("stage") or "").strip().lower() == "execution"
        and bool(str(pointer.get("run_id") or "").strip())
    )
    run_root_text = str(pointer.get("run_root") or "").strip()
    run_root = Path(run_root_text) if run_root_text else None
    if run_root is not None and not run_root.is_absolute():
        run_root = repo_root / run_root
    results: dict[str, object] = {}
    if run_root is not None:
        results_path = run_root / "execution_results.json"
        try:
            loaded = json.loads(results_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                results = loaded
        except (OSError, json.JSONDecodeError):
            results = {}

    reason = str(
        pointer.get("substatus")
        or pointer.get("status_message")
        or results.get("halt_reason")
        or results.get("reason")
        or "paper_lane_unknown_failure"
    ).strip()
    submitted_count = int(results.get("submitted_count") or 0)
    retryable = bool(
        exit_code != 0
        and pointer_fresh
        and pointer_is_paper_execution
        and submitted_count == 0
        and reason in TRANSIENT_PAPER_FAILURE_REASONS
    )
    return AttemptOutcome(
        exit_code=int(exit_code),
        retryable=retryable,
        reason_code=reason,
        submitted_count=submitted_count,
        run_id=str(pointer.get("run_id") or results.get("run_id") or ""),
        run_root=run_root_text,
        error=_sanitized_error(pointer.get("status_message") or results.get("halt_reason") or reason),
    )


def run_retry_harness(
    *,
    run_once: Callable[[int], AttemptOutcome],
    retry_delays_seconds: tuple[int, ...] = PAPER_EXECUTION_RETRY_DELAYS_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
    publish_fn: Callable[[Mapping[str, object]], None] | None = None,
    mark_retrying_fn: Callable[[AttemptOutcome, int], None] | None = None,
) -> tuple[AttemptOutcome, dict[str, object]]:
    delays = tuple(max(int(delay), 0) for delay in retry_delays_seconds)
    max_attempts = len(delays) + 1
    attempts: list[dict[str, object]] = []

    def publish(status: str, outcome: AttemptOutcome, *, next_delay: int | None) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": "paper_execution_retry.v1",
            "updated_at": _now_utc(),
            "status": status,
            "attempt_count": len(attempts),
            "max_attempts": max_attempts,
            "retry_delays_seconds": list(delays),
            "total_retry_delay_seconds": sum(delays),
            "next_retry_delay_seconds": next_delay,
            "attempts": list(attempts),
            "last_reason_code": outcome.reason_code,
            "last_error": outcome.error,
            "submitted_count": outcome.submitted_count,
            "escalation_required": status == "ESCALATION_REQUIRED",
            "live_lane_affected": False,
        }
        if publish_fn is not None:
            publish_fn(payload)
        return payload

    final_outcome: AttemptOutcome | None = None
    final_payload: dict[str, object] | None = None
    for attempt_number in range(1, max_attempts + 1):
        started_at = _now_utc()
        outcome = run_once(attempt_number)
        attempts.append(
            {
                "attempt": attempt_number,
                "started_at": started_at,
                "completed_at": _now_utc(),
                "exit_code": outcome.exit_code,
                "retryable": outcome.retryable,
                "reason_code": outcome.reason_code,
                "submitted_count": outcome.submitted_count,
                "run_id": outcome.run_id,
                "run_root": outcome.run_root,
                "error": outcome.error,
            }
        )
        if outcome.exit_code == 0:
            status = "SUCCEEDED" if attempt_number == 1 else "RECOVERED_AFTER_RETRY"
            return outcome, publish(status, outcome, next_delay=None)

        retry_index = attempt_number - 1
        if outcome.retryable and retry_index < len(delays):
            delay = delays[retry_index]
            publish("RETRY_SCHEDULED", outcome, next_delay=delay)
            if mark_retrying_fn is not None:
                mark_retrying_fn(outcome, delay)
            sleep_fn(float(delay))
            continue

        final_outcome = outcome
        final_payload = publish("ESCALATION_REQUIRED", outcome, next_delay=None)
        break

    assert final_outcome is not None and final_payload is not None
    return final_outcome, final_payload


def _write_payload(path: Path, payload: Mapping[str, object]) -> None:
    safe_write_text(
        path,
        json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
        allow_overwrite=True,
    )


def _send_escalation(trade_date: str, payload: Mapping[str, object]) -> None:
    # Lazy import keeps retry policy/harness tests independent of the reporting
    # stack's NumPy/pandas runtime while production still uses canonical email.
    from core.quant_report import send_email

    exhausted = str(payload.get("status") or "") == "RETRY_EXHAUSTED"
    send_email(
        f"❌ [Alpha Stack] PAPER execution {'retry exhausted' if exhausted else 'halted'} — {trade_date}",
        body_text=(
            f"Paper execution remained fail-closed after {payload.get('attempt_count')} attempt(s).\n\n"
            f"Reason: {payload.get('last_reason_code')}\n"
            f"Error: {payload.get('last_error')}\n"
            f"Orders submitted: {payload.get('submitted_count')}\n"
            + (
                "Retry schedule exhausted: initial, +30 seconds, +1 minute, +5 minutes, +1 hour.\n\n"
                if exhausted
                else "The failure was not safe to retry automatically.\n\n"
            )
            + "Operator action: inspect outputs/workflow/"
            f"{trade_date}/paper_execution_retry.json and contact Alpaca if the failure is external."
        ),
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the PAPER execution lane with bounded retries")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser.parse_args(argv)


def _confirmation_is_due(trade_date: str, now: dt.datetime | None = None) -> bool:
    current = now or dt.datetime.now(ZoneInfo("America/New_York"))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo("America/New_York"))
    current_et = current.astimezone(ZoneInfo("America/New_York"))
    return current_et.date().isoformat() == trade_date and (current_et.hour, current_et.minute) >= (10, 0)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    workflow_dir = repo_root / "outputs" / "workflow" / args.trade_date
    workflow_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = workflow_dir / "paper_execution_retry.json"
    confirmation_path = workflow_dir / "paper_execution_retry_late_confirmation.json"
    lock_path = repo_root / "outputs" / "workflow" / "paper_execution_retry.lock"

    lock_file = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("FATAL: paper execution retry harness is already running", file=sys.stderr)
        return 75

    def publish(payload: Mapping[str, object]) -> None:
        _write_payload(artifact_path, payload)

    def run_once(_attempt_number: int) -> AttemptOutcome:
        env = dict(os.environ)
        env["CAERUS_PAPER_RETRY_CHILD"] = "1"
        started_ns = time.time_ns()
        paper_script = repo_root / "scripts" / "cron_execute.sh"
        completed = subprocess.run([str(paper_script)], cwd=repo_root, env=env, check=False)
        return inspect_attempt(
            repo_root,
            args.trade_date,
            completed.returncode,
            not_before_ns=started_ns,
        )

    def mark_retrying(outcome: AttemptOutcome, delay: int) -> None:
        write_paper_lane_pointers(
            trade_date=args.trade_date,
            run_id=outcome.run_id or f"paper-retry-{args.trade_date}",
            run_root=outcome.run_root,
            terminal_status="running",
            reason_code=f"paper_retry_scheduled_in_{delay}_seconds",
            workspace_root=str(repo_root),
        )

    outcome, payload = run_retry_harness(
        run_once=run_once,
        publish_fn=publish,
        mark_retrying_fn=mark_retrying,
    )
    if outcome.exit_code == 0:
        # A delayed run can recover on any attempt after the normal 10:00 job.
        # Record intent before sending so a crash or manual rerun cannot duplicate it.
        if _confirmation_is_due(args.trade_date) and not confirmation_path.exists():
            _write_payload(
                confirmation_path,
                {
                    "schema_version": "paper_execution_retry_late_confirmation.v1",
                    "trade_date": args.trade_date,
                    "attempted_at": _now_utc(),
                    "status": "ATTEMPTING",
                },
            )
            confirm = subprocess.run(
                [sys.executable, "-m", "scripts.send_trading_confirmation_email"],
                cwd=repo_root,
                env=dict(os.environ),
                check=False,
            )
            _write_payload(
                confirmation_path,
                {
                    "schema_version": "paper_execution_retry_late_confirmation.v1",
                    "trade_date": args.trade_date,
                    "completed_at": _now_utc(),
                    "status": "SENT" if confirm.returncode == 0 else "FAILED",
                    "exit_code": confirm.returncode,
                },
            )
            updated = dict(payload)
            updated["late_confirmation_exit_code"] = confirm.returncode
            updated["late_confirmation_attempted"] = True
            publish(updated)
        return 0

    updated = dict(payload)
    if outcome.retryable and int(payload.get("attempt_count") or 0) == len(PAPER_EXECUTION_RETRY_DELAYS_SECONDS) + 1:
        updated["status"] = "RETRY_EXHAUSTED"
        write_paper_lane_pointers(
            trade_date=args.trade_date,
            run_id=outcome.run_id or f"paper-retry-{args.trade_date}",
            run_root=outcome.run_root,
            terminal_status="BLOCKED",
            reason_code="paper_execution_retry_exhausted",
            status_message=f"paper execution retries exhausted: {outcome.reason_code}",
            workspace_root=str(repo_root),
        )
    else:
        updated["status"] = "IMMEDIATE_ESCALATION_REQUIRED"
    updated["escalation_delivery_attempted"] = True
    try:
        _send_escalation(args.trade_date, updated)
        updated["escalation_delivery_status"] = "SENT"
    except Exception as exc:
        updated["escalation_delivery_status"] = "FAILED"
        updated["escalation_delivery_error"] = str(exc)
    publish(updated)
    return outcome.exit_code or 1


if __name__ == "__main__":
    raise SystemExit(main())
