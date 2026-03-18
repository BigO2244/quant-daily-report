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
    post_execution_recon_status: str | None = None,
    post_execution_recon_path: str | None = None,
    duplicate_guard_status: str | None = None,
    duplicate_guard_reason: str | None = None,
    affected_symbols: list[str] | None = None,
    repair_suggestions: list[str] | None = None,
    duplicate_fill_suspicions_count: int | None = None,
    broker_reject_status: str | None = None,
    broker_reject_message: str | None = None,
    execution_outcome: str | None = None,
    execution_reason: str | None = None,
    cash_rebalance_status: str | None = None,
    broker_preflight_status: str | None = None,
    broker_preflight_account_status: str | None = None,
    broker_preflight_cash: Any = None,
    broker_preflight_equity: Any = None,
    broker_preflight_buying_power: Any = None,
    broker_preflight_restriction_flags: Dict[str, Any] | None = None,
    broker_preflight_warning_flags: list[str] | None = None,
    broker_pdt_risk_status: str | None = None,
    broker_pdt_daytrade_count: Any = None,
    broker_pdt_daytrading_buying_power: Any = None,
    broker_pdt_flags: list[str] | None = None,
    broker_pdt_warning_message: str | None = None,
    broker_cash_at_planning: Any = None,
    broker_equity_at_planning: Any = None,
    broker_buying_power_at_planning: Any = None,
    reserve_cash_policy: Dict[str, Any] | None = None,
    expected_sell_proceeds: Any = None,
    expected_sell_proceeds_conservative: Any = None,
    requested_buy_notional: Any = None,
    allowed_buy_notional: Any = None,
    capital_constraint_triggered: bool | None = None,
    clipped_or_deferred_buys_count: int | None = None,
    timing_status: str | None = None,
    preferred_target_et: str | None = None,
    degraded_auto_trade_deadline_et: str | None = None,
    actual_workflow_start_et: str | None = None,
    actual_execution_start_et: str | None = None,
    first_submit_et: str | None = None,
    operator_execution_status: str | None = None,
    retry_attempt_count: int | None = None,
    retry_eligible: bool | None = None,
    retry_reason: str | None = None,
    continuation_eligible: bool | None = None,
    continuation_reason: str | None = None,
    pending_buy_count: int | None = None,
    workflow_kind: str | None = None,
    event_freshness_status: str | None = None,
    bundle_status: str | None = None,
    bundle_source: str | None = None,
    precompute_bundle_required: bool | None = None,
    precompute_bundle_found: bool | None = None,
    bundle_report_date: str | None = None,
    execution_window_status: str | None = None,
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
    duplicate_guard_value = (
        str(duplicate_guard_status).strip()
        if duplicate_guard_status is not None and str(duplicate_guard_status).strip()
        else str(existing.get("duplicate_guard_status") or ("SKIPPED_DUPLICATE" if skipped_duplicate else "CLEAR"))
    )
    affected_symbols_value = (
        sorted({str(sym).strip().upper() for sym in (affected_symbols or []) if str(sym).strip()})
        if affected_symbols is not None
        else list(existing.get("affected_symbols") or [])
    )
    repair_suggestions_value = (
        [str(item).strip() for item in (repair_suggestions or []) if str(item).strip()]
        if repair_suggestions is not None
        else list(existing.get("repair_suggestions") or [])
    )
    duplicate_fill_suspicions_value = (
        int(duplicate_fill_suspicions_count)
        if duplicate_fill_suspicions_count is not None
        else int(existing.get("duplicate_fill_suspicions_count", 0))
    )
    duplicate_guard_reason_value = (
        duplicate_guard_reason
        if (duplicate_guard_status is not None or duplicate_guard_reason is not None)
        else existing.get("duplicate_guard_reason")
    )
    broker_reject_status_value = (
        str(broker_reject_status).strip()
        if broker_reject_status is not None and str(broker_reject_status).strip()
        else str(existing.get("broker_reject_status") or "")
    )
    broker_reject_message_value = (
        broker_reject_message
        if (broker_reject_status is not None or broker_reject_message is not None)
        else existing.get("broker_reject_message")
    )
    execution_outcome_value = (
        execution_outcome
        if execution_outcome is not None
        else existing.get("execution_outcome")
    )
    execution_reason_value = (
        execution_reason
        if execution_reason is not None
        else existing.get("execution_reason")
    )
    cash_rebalance_status_value = (
        cash_rebalance_status
        if cash_rebalance_status is not None
        else existing.get("cash_rebalance_status")
    )
    broker_preflight_status_value = (
        str(broker_preflight_status).strip()
        if broker_preflight_status is not None and str(broker_preflight_status).strip()
        else str(existing.get("broker_preflight_status") or "")
    )
    broker_preflight_warning_flags_value = (
        [str(item).strip() for item in (broker_preflight_warning_flags or []) if str(item).strip()]
        if broker_preflight_warning_flags is not None
        else list(existing.get("broker_preflight_warning_flags") or [])
    )
    broker_preflight_restriction_flags_value = (
        dict(broker_preflight_restriction_flags or {})
        if broker_preflight_restriction_flags is not None
        else dict(existing.get("broker_preflight_restriction_flags") or {})
    )
    broker_preflight_account_status_value = (
        broker_preflight_account_status
        if broker_preflight_account_status is not None
        else existing.get("broker_preflight_account_status")
    )
    broker_preflight_cash_value = (
        broker_preflight_cash
        if broker_preflight_cash is not None
        else existing.get("broker_preflight_cash")
    )
    broker_preflight_equity_value = (
        broker_preflight_equity
        if broker_preflight_equity is not None
        else existing.get("broker_preflight_equity")
    )
    broker_preflight_buying_power_value = (
        broker_preflight_buying_power
        if broker_preflight_buying_power is not None
        else existing.get("broker_preflight_buying_power")
    )
    broker_pdt_risk_status_value = (
        str(broker_pdt_risk_status).strip()
        if broker_pdt_risk_status is not None and str(broker_pdt_risk_status).strip()
        else str(existing.get("broker_pdt_risk_status") or "")
    )
    broker_pdt_daytrade_count_value = (
        broker_pdt_daytrade_count
        if broker_pdt_daytrade_count is not None
        else existing.get("broker_pdt_daytrade_count")
    )
    broker_pdt_daytrading_buying_power_value = (
        broker_pdt_daytrading_buying_power
        if broker_pdt_daytrading_buying_power is not None
        else existing.get("broker_pdt_daytrading_buying_power")
    )
    broker_pdt_flags_value = (
        [str(item).strip() for item in (broker_pdt_flags or []) if str(item).strip()]
        if broker_pdt_flags is not None
        else list(existing.get("broker_pdt_flags") or [])
    )
    broker_pdt_warning_message_value = (
        broker_pdt_warning_message
        if (broker_pdt_risk_status is not None or broker_pdt_warning_message is not None)
        else existing.get("broker_pdt_warning_message")
    )
    broker_cash_at_planning_value = (
        broker_cash_at_planning
        if broker_cash_at_planning is not None
        else existing.get("broker_cash_at_planning")
    )
    broker_equity_at_planning_value = (
        broker_equity_at_planning
        if broker_equity_at_planning is not None
        else existing.get("broker_equity_at_planning")
    )
    broker_buying_power_at_planning_value = (
        broker_buying_power_at_planning
        if broker_buying_power_at_planning is not None
        else existing.get("broker_buying_power_at_planning")
    )
    reserve_cash_policy_value = (
        dict(reserve_cash_policy or {})
        if reserve_cash_policy is not None
        else dict(existing.get("reserve_cash_policy") or {})
    )
    expected_sell_proceeds_value = (
        expected_sell_proceeds
        if expected_sell_proceeds is not None
        else existing.get("expected_sell_proceeds")
    )
    expected_sell_proceeds_conservative_value = (
        expected_sell_proceeds_conservative
        if expected_sell_proceeds_conservative is not None
        else existing.get("expected_sell_proceeds_conservative")
    )
    requested_buy_notional_value = (
        requested_buy_notional
        if requested_buy_notional is not None
        else existing.get("requested_buy_notional")
    )
    allowed_buy_notional_value = (
        allowed_buy_notional
        if allowed_buy_notional is not None
        else existing.get("allowed_buy_notional")
    )
    capital_constraint_triggered_value = (
        capital_constraint_triggered
        if capital_constraint_triggered is not None
        else existing.get("capital_constraint_triggered")
    )
    clipped_or_deferred_buys_count_value = (
        int(clipped_or_deferred_buys_count)
        if clipped_or_deferred_buys_count is not None
        else existing.get("clipped_or_deferred_buys_count")
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
        "post_execution_recon_status": post_execution_recon_status or existing.get("post_execution_recon_status"),
        "post_execution_recon_path": post_execution_recon_path or existing.get("post_execution_recon_path"),
        "duplicate_guard_status": duplicate_guard_value,
        "duplicate_guard_reason": duplicate_guard_reason_value,
        "affected_symbols": affected_symbols_value,
        "repair_suggestions": repair_suggestions_value,
        "duplicate_fill_suspicions_count": duplicate_fill_suspicions_value,
        "broker_reject_status": broker_reject_status_value or None,
        "broker_reject_message": broker_reject_message_value,
        "execution_outcome": execution_outcome_value,
        "execution_reason": execution_reason_value,
        "cash_rebalance_status": cash_rebalance_status_value,
        "broker_preflight_status": broker_preflight_status_value or None,
        "broker_preflight_account_status": broker_preflight_account_status_value,
        "broker_preflight_cash": broker_preflight_cash_value,
        "broker_preflight_equity": broker_preflight_equity_value,
        "broker_preflight_buying_power": broker_preflight_buying_power_value,
        "broker_preflight_restriction_flags": broker_preflight_restriction_flags_value,
        "broker_preflight_warning_flags": broker_preflight_warning_flags_value,
        "broker_pdt_risk_status": broker_pdt_risk_status_value or None,
        "broker_pdt_daytrade_count": broker_pdt_daytrade_count_value,
        "broker_pdt_daytrading_buying_power": broker_pdt_daytrading_buying_power_value,
        "broker_pdt_flags": broker_pdt_flags_value,
        "broker_pdt_warning_message": broker_pdt_warning_message_value,
        "broker_cash_at_planning": broker_cash_at_planning_value,
        "broker_equity_at_planning": broker_equity_at_planning_value,
        "broker_buying_power_at_planning": broker_buying_power_at_planning_value,
        "reserve_cash_policy": reserve_cash_policy_value,
        "expected_sell_proceeds": expected_sell_proceeds_value,
        "expected_sell_proceeds_conservative": expected_sell_proceeds_conservative_value,
        "requested_buy_notional": requested_buy_notional_value,
        "allowed_buy_notional": allowed_buy_notional_value,
        "capital_constraint_triggered": capital_constraint_triggered_value,
        "clipped_or_deferred_buys_count": clipped_or_deferred_buys_count_value,
        "timing_status": timing_status if timing_status is not None else existing.get("timing_status"),
        "preferred_target_et": preferred_target_et if preferred_target_et is not None else existing.get("preferred_target_et"),
        "degraded_auto_trade_deadline_et": (
            degraded_auto_trade_deadline_et
            if degraded_auto_trade_deadline_et is not None
            else existing.get("degraded_auto_trade_deadline_et")
        ),
        "actual_workflow_start_et": (
            actual_workflow_start_et
            if actual_workflow_start_et is not None
            else existing.get("actual_workflow_start_et")
        ),
        "actual_execution_start_et": (
            actual_execution_start_et
            if actual_execution_start_et is not None
            else existing.get("actual_execution_start_et")
        ),
        "first_submit_et": first_submit_et if first_submit_et is not None else existing.get("first_submit_et"),
        "operator_execution_status": (
            operator_execution_status
            if operator_execution_status is not None
            else existing.get("operator_execution_status")
        ),
        "retry_attempt_count": (
            int(retry_attempt_count)
            if retry_attempt_count is not None
            else int(existing.get("retry_attempt_count", 0))
        ),
        "retry_eligible": retry_eligible if retry_eligible is not None else existing.get("retry_eligible"),
        "retry_reason": retry_reason if retry_reason is not None else existing.get("retry_reason"),
        "continuation_eligible": (
            continuation_eligible
            if continuation_eligible is not None
            else existing.get("continuation_eligible")
        ),
        "continuation_reason": (
            continuation_reason
            if continuation_reason is not None
            else existing.get("continuation_reason")
        ),
        "pending_buy_count": (
            int(pending_buy_count)
            if pending_buy_count is not None
            else int(existing.get("pending_buy_count", 0))
        ),
        "workflow_kind": (
            workflow_kind
            if workflow_kind is not None
            else existing.get("workflow_kind")
        ),
        "event_freshness_status": (
            event_freshness_status
            if event_freshness_status is not None
            else existing.get("event_freshness_status")
        ),
        "bundle_status": (
            bundle_status
            if bundle_status is not None
            else existing.get("bundle_status")
        ),
        "bundle_source": (
            bundle_source
            if bundle_source is not None
            else existing.get("bundle_source")
        ),
        "precompute_bundle_required": (
            precompute_bundle_required
            if precompute_bundle_required is not None
            else existing.get("precompute_bundle_required")
        ),
        "precompute_bundle_found": (
            precompute_bundle_found
            if precompute_bundle_found is not None
            else existing.get("precompute_bundle_found")
        ),
        "bundle_report_date": (
            bundle_report_date
            if bundle_report_date is not None
            else existing.get("bundle_report_date")
        ),
        "execution_window_status": (
            execution_window_status
            if execution_window_status is not None
            else existing.get("execution_window_status")
        ),
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
    status = summary.get("operator_execution_status") or summary.get("pretrade_status") or "UNKNOWN"
    if summary.get("skipped_duplicate"):
        status = "SKIPPED_DUPLICATE"
    elif (
        (summary.get("broker_reject_status") or summary.get("execution_outcome"))
        and summary.get("submitted_count", 0) > 0
    ):
        status = "PARTIAL_EXECUTION_ABORTED"
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
        f"timing={summary.get('timing_status','unknown')} "
        f"workflow_kind={summary.get('workflow_kind','unknown')} "
        f"bundle_status={summary.get('bundle_status','unknown')} "
        f"bundle_source={summary.get('bundle_source','unknown')} "
        f"window={summary.get('execution_window_status','unknown')} "
        f"confirmation_email={summary.get('confirmation_email_sent',False)}"
    )


def format_execution_health_banner(summary: Dict[str, Any]) -> str:
    affected_symbols = list(summary.get("affected_symbols") or [])
    repair_suggestions = list(summary.get("repair_suggestions") or [])
    broker_reject_status = str(summary.get("broker_reject_status") or "").strip() or "none"
    broker_reject_message = str(summary.get("broker_reject_message") or "").strip() or "none"
    execution_outcome = str(summary.get("execution_outcome") or "").strip() or "none"
    execution_reason = str(summary.get("execution_reason") or "").strip() or "none"
    cash_rebalance_status = str(summary.get("cash_rebalance_status") or "").strip() or "none"
    broker_preflight_status = str(summary.get("broker_preflight_status") or "").strip() or "none"
    broker_preflight_warning_flags = list(summary.get("broker_preflight_warning_flags") or [])
    broker_pdt_status = str(summary.get("broker_pdt_risk_status") or "").strip() or "none"
    broker_pdt_flags = list(summary.get("broker_pdt_flags") or [])
    broker_pdt_warning_message = str(summary.get("broker_pdt_warning_message") or "").strip() or "none"
    capital_constraint = (
        "TRIGGERED" if summary.get("capital_constraint_triggered") else "CLEAR"
    )
    allowed_buy_notional = summary.get("allowed_buy_notional")
    requested_buy_notional = summary.get("requested_buy_notional")
    clipped_or_deferred_buys_count = int(summary.get("clipped_or_deferred_buys_count") or 0)
    return (
        "[EXECUTION_HEALTH] "
        f"run_id={summary.get('run_id','')} "
        f"duplicate_guard={summary.get('duplicate_guard_status') or 'CLEAR'} "
        f"broker_preflight={broker_preflight_status} "
        f"broker_preflight_warnings={','.join(broker_preflight_warning_flags) if broker_preflight_warning_flags else 'none'} "
        f"broker_pdt={broker_pdt_status} "
        f"broker_pdt_flags={','.join(broker_pdt_flags) if broker_pdt_flags else 'none'} "
        f"broker_pdt_message={broker_pdt_warning_message} "
        f"execution_outcome={execution_outcome} "
        f"execution_reason={execution_reason} "
        f"cash_rebalance={cash_rebalance_status} "
        f"broker_reject={broker_reject_status} "
        f"broker_reject_message={broker_reject_message} "
        f"capital_constraint={capital_constraint} "
        f"requested_buys={requested_buy_notional if requested_buy_notional is not None else 'none'} "
        f"allowed_buys={allowed_buy_notional if allowed_buy_notional is not None else 'none'} "
        f"clipped_buys={clipped_or_deferred_buys_count} "
        f"post_execution_recon={summary.get('post_execution_recon_status') or 'UNKNOWN'} "
        f"affected_symbols={','.join(affected_symbols) if affected_symbols else 'none'} "
        f"duplicate_fill_suspicions={int(summary.get('duplicate_fill_suspicions_count') or 0)} "
        f"repairs={'; '.join(repair_suggestions) if repair_suggestions else 'none'}"
    )


def format_broker_preflight_banner(preflight: Dict[str, Any]) -> str:
    warning_flags = list(preflight.get("broker_preflight_warning_flags") or [])
    restriction_flags = dict(preflight.get("broker_preflight_restriction_flags") or {})
    restriction_tokens = ",".join(f"{k}={v}" for k, v in sorted(restriction_flags.items()))
    pdt_flags = list(preflight.get("broker_pdt_flags") or [])
    return (
        "[BROKER_PREFLIGHT] "
        f"status={preflight.get('broker_preflight_status') or 'UNKNOWN'} "
        f"account_status={preflight.get('broker_preflight_account_status') or 'unknown'} "
        f"cash={preflight.get('broker_preflight_cash')} "
        f"equity={preflight.get('broker_preflight_equity')} "
        f"buying_power={preflight.get('broker_preflight_buying_power')} "
        f"warnings={','.join(warning_flags) if warning_flags else 'none'} "
        f"restrictions={restriction_tokens or 'none'} "
        f"pdt_status={preflight.get('broker_pdt_risk_status') or 'UNKNOWN'} "
        f"pdt_flags={','.join(pdt_flags) if pdt_flags else 'none'}"
    )
