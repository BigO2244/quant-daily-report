"""
Operator Summary Artifact

Provides one-glance truth artifact for pipeline auditing.

Written/updated at key stages:
  1. After planner writes execution_payload.json
  2. After executor writes execution_results.json
  3. After confirmation email sent (if applicable)

Status normalization:
  - NO_ACTION: no trades proposed after constraints
  - READY: trades exist and can be executed
  - HALTED: execution cannot proceed (blocker)
  - EXECUTED: orders submitted to broker
  - SKIPPED_DUPLICATE: already executed this run_id
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict

from paper.run_manager import safe_write_text


def write_operator_summary(
    run_root: Path,
    *,
    run_id: str,
    trade_date: str,
    mode: str,
    pretrade_status: str | None = None,
    pretrade_halt_reason: str | None = None,
    proposed_trades_count: int = 0,
    executable_trades_count: int = 0,
    submitted_count: int = 0,
    accepted_count: int = 0,
    rejected_count: int = 0,
    model_proposed_trades_count: int | None = None,
    planner_intended_trades_count: int | None = None,
    execution_eligible_trades_count: int | None = None,
    orders_submitted_count: int | None = None,
    orders_filled_count: int | None = None,
    skipped_duplicate: bool = False,
    confirmation_email_sent: bool = False,
    planner_completed: bool = False,
    executor_completed: bool = False,
    report_completed: bool = False,
    allow_overwrite: bool = True,
    # Extended observability fields (all optional for backward compatibility)
    terminal_status: str | None = None,
    execution_payload_written: bool = False,
    execution_stage_reached: bool = False,
    broker_probe_ok: bool | None = None,
    suggested_order_count: int | None = None,
    no_trade_reason: str | None = None,
    exception_type: str | None = None,
    exception_message: str | None = None,
    broker_pretrade_snapshot_ok: bool | None = None,
    broker_posttrade_snapshot_ok: bool | None = None,
    broker_authoritative_state: bool | None = None,
) -> Path:
    """
    Write or update operator_summary.json in run root.

    This is the authoritative one-glance truth artifact for the run.

    Args:
        run_root: Run directory (outputs/runs/<run_id>/)
        run_id: Unique run identifier
        trade_date: Trade date YYYY-MM-DD
        mode: Execution mode (PAPER, ALPACA, SHADOW, etc.)
        pretrade_status: Normalized status after planner (NO_ACTION, READY, HALTED)
        pretrade_halt_reason: Reason if halted during planning
        proposed_trades_count: Legacy alias for planner_intended_trades_count
        executable_trades_count: Legacy alias for execution_eligible_trades_count
        submitted_count: Orders submitted to broker
        accepted_count: Orders accepted by broker
        rejected_count: Orders rejected by broker
        skipped_duplicate: True if execution skipped due to duplicate run_id
        confirmation_email_sent: True if confirmation email was sent
        planner_completed: True if planner stage completed
        executor_completed: True if executor stage completed
        report_completed: True if reporting stage completed
        allow_overwrite: Allow overwriting existing summary

    Returns:
        Path to written operator_summary.json
    """
    out_path = run_root / "operator_summary.json"
    
    # If file exists and we're updating, merge with existing data
    existing = {}
    if out_path.exists() and allow_overwrite:
        try:
            with out_path.open("r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = {}

    planner_intended_value = (
        int(planner_intended_trades_count)
        if planner_intended_trades_count is not None
        else int(proposed_trades_count or existing.get("planner_intended_trades_count") or existing.get("proposed_trades_count", 0))
    )
    execution_eligible_value = (
        int(execution_eligible_trades_count)
        if execution_eligible_trades_count is not None
        else int(executable_trades_count or existing.get("execution_eligible_trades_count") or existing.get("executable_trades_count", 0))
    )
    orders_submitted_value = (
        int(orders_submitted_count)
        if orders_submitted_count is not None
        else int(submitted_count or existing.get("orders_submitted_count") or existing.get("submitted_count", 0))
    )
    orders_filled_value = (
        int(orders_filled_count)
        if orders_filled_count is not None
        else int(existing.get("orders_filled_count", 0))
    )
    model_proposed_value = (
        int(model_proposed_trades_count)
        if model_proposed_trades_count is not None
        else int(existing.get("model_proposed_trades_count", 0))
    )

    payload = {
        "run_id": run_id,
        "trade_date": trade_date,
        "mode": mode,
        "run_root": str(run_root),
        "execution_payload_path": str(run_root / "execution_payload.json"),
        "execution_results_path": str(run_root / "execution_results.json"),
        "pretrade_status": pretrade_status or existing.get("pretrade_status"),
        "pretrade_halt_reason": pretrade_halt_reason or existing.get("pretrade_halt_reason"),
        "model_proposed_trades_count": model_proposed_value,
        "planner_intended_trades_count": planner_intended_value,
        "execution_eligible_trades_count": execution_eligible_value,
        "orders_submitted_count": orders_submitted_value,
        "orders_filled_count": orders_filled_value,
        "proposed_trades_count": planner_intended_value,
        "executable_trades_count": execution_eligible_value,
        "submitted_count": submitted_count or existing.get("submitted_count", 0),
        "accepted_count": accepted_count or existing.get("accepted_count", 0),
        "rejected_count": rejected_count or existing.get("rejected_count", 0),
        "skipped_duplicate": skipped_duplicate or existing.get("skipped_duplicate", False),
        "confirmation_email_sent": confirmation_email_sent or existing.get("confirmation_email_sent", False),
        "planner_completed": planner_completed or existing.get("planner_completed", False),
        "executor_completed": executor_completed or existing.get("executor_completed", False),
        "report_completed": report_completed or existing.get("report_completed", False),
        # Extended observability fields — present in all new writes, null if unknown.
        "terminal_status": terminal_status or existing.get("terminal_status"),
        "execution_payload_written": execution_payload_written or existing.get("execution_payload_written", False),
        "execution_stage_reached": execution_stage_reached or existing.get("execution_stage_reached", False),
        "broker_probe_ok": broker_probe_ok if broker_probe_ok is not None else existing.get("broker_probe_ok"),
        "suggested_order_count": suggested_order_count if suggested_order_count is not None else existing.get("suggested_order_count"),
        "submitted_order_count": orders_submitted_value,
        "no_trade_reason": no_trade_reason or existing.get("no_trade_reason"),
        "halt_reason": pretrade_halt_reason or existing.get("pretrade_halt_reason") or existing.get("halt_reason"),
        "exception_type": exception_type or existing.get("exception_type"),
        "exception_message": exception_message or existing.get("exception_message"),
        "broker_pretrade_snapshot_ok": broker_pretrade_snapshot_ok if broker_pretrade_snapshot_ok is not None else existing.get("broker_pretrade_snapshot_ok"),
        "broker_posttrade_snapshot_ok": broker_posttrade_snapshot_ok if broker_posttrade_snapshot_ok is not None else existing.get("broker_posttrade_snapshot_ok"),
        "broker_authoritative_state": broker_authoritative_state if broker_authoritative_state is not None else existing.get("broker_authoritative_state"),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    safe_write_text(
        out_path,
        json.dumps(payload, indent=2) + "\n",
        allow_overwrite=allow_overwrite,
    )
    return out_path


def write_preflight_failure(
    run_root: Path,
    *,
    run_id: str,
    stage: str,
    terminal_status: str,
    exception_type: str | None = None,
    exception_message: str | None = None,
    traceback_excerpt: str | None = None,
) -> "Path | None":
    """
    Write run_root/logs/planner_failure.json on early failure.

    Best-effort and non-blocking — returns None on any write error.
    Consumed by the dashboard to distinguish a failed run from a no-action day.
    """
    try:
        dest = run_root / "logs" / "planner_failure.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": run_id,
            "stage": stage,
            "terminal_status": terminal_status,
            "exception_type": exception_type,
            "exception_message": exception_message,
            "traceback_excerpt": traceback_excerpt,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return dest
    except Exception:
        return None


def load_operator_summary(run_root: Path) -> Dict[str, Any] | None:
    """Load operator_summary.json from run root, or None if not found."""
    path = run_root / "operator_summary.json"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def format_operator_summary_log(summary: Dict[str, Any]) -> str:
    """Format operator summary as machine-readable log line."""
    status = summary.get("pretrade_status") or "UNKNOWN"
    if summary.get("skipped_duplicate"):
        status = "SKIPPED_DUPLICATE"
    elif summary.get("submitted_count", 0) > 0:
        status = "EXECUTED"
    
    return (
        f"[OPERATOR_SUMMARY] "
        f"run_id={summary.get('run_id','')} "
        f"status={status} "
        f"executable={summary.get('executable_trades_count',0)} "
        f"submitted={summary.get('submitted_count',0)} "
        f"accepted={summary.get('accepted_count',0)} "
        f"rejected={summary.get('rejected_count',0)} "
        f"confirmation_email={summary.get('confirmation_email_sent',False)}"
    )
