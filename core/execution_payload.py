from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from paper.run_manager import safe_write_text


# Normalized execution statuses used across the pipeline
STATUS_NO_ACTION = "NO_ACTION"
STATUS_READY = "READY"
STATUS_HALTED = "HALTED"
STATUS_EXECUTED = "EXECUTED"
STATUS_MARKET_CLOSED = "MARKET_CLOSED"
STATUS_SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
STATUS_IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"

# Final-state reconciliation override (post-trade). When an execution is raised
# as a broker-abort PARTIAL purely because broker fills were not yet observable
# at the buy-decision point, but post-trade reconciliation later confirms the
# broker matches the expected post-execution state, the *raw* status is preserved
# as diagnostic metadata and a *final* status of RECONCILED_SUCCESS is surfaced.
STATUS_RECONCILED_SUCCESS = "RECONCILED_SUCCESS"
RECONCILED_SUCCESS_OPERATOR_STATUS = "reconciled_success"
RECONCILED_TO_TARGET_REASON = "raw_partial_reconciled_to_target_state"

# Only genuine broker-abort partials are eligible for the override. A HALTED that
# originates from pretrade reconciliation failure, market-closed, stale prices,
# signal-date mismatch, etc. must NEVER be upgraded.
_RECONCILABLE_EXECUTION_OUTCOMES = {
    "partial_execution_broker_abort",
    "post_submit_artifact_failure",
}
_OK_RECONCILIATION_STATUSES = {"OK_RECONCILED", "OK"}


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def compute_final_execution_status(
    *,
    raw_execution_status: str | None,
    raw_operator_execution_status: str | None,
    execution_outcome: str | None = None,
    raw_execution_reason: str | None = None,
    posttrade_recon_status: str | None = None,
    posttrade_unresolved_orders_count: object = 0,
    skipped_buy_count: object = 0,
    blocked_buy_count: object = 0,
    pending_buy_count: object = 0,
    rejected_count: object = 0,
    broker_reject_status: str | None = None,
    submitted_count: object = 0,
) -> dict:
    """Derive a final, reconciliation-aware execution status.

    The raw execution status / reason are always preserved verbatim in the
    returned mapping. ``final_execution_status`` is only upgraded to
    :data:`STATUS_RECONCILED_SUCCESS` when ALL of the following hold:

    1. The raw outcome is a broker-abort PARTIAL (not a pretrade/market halt).
    2. Post-trade reconciliation is OK_RECONCILED (broker matches expected
       post-execution state).
    3. No planned orders were skipped/deferred (``skipped``/``blocked``/
       ``pending`` buy counts are all zero) — otherwise a green reconciliation
       only proves the *submitted* subset matched, and upgrading would mask a
       genuinely partial execution.
    4. No orders were rejected by the broker.
    5. At least one order was submitted and every submitted order resolved to a
       terminal/acceptable state (no unresolved orders).

    When any condition fails, ``final_*`` mirrors ``raw_*`` unchanged.
    """
    raw_status = str(raw_execution_status or "").strip().upper()
    raw_operator = str(raw_operator_execution_status or "").strip().lower()
    outcome = str(execution_outcome or "").strip()
    recon_status = str(posttrade_recon_status or "").strip().upper()

    result = {
        "raw_execution_status": raw_status or None,
        "raw_operator_execution_status": raw_operator or None,
        "raw_execution_reason": (str(raw_execution_reason).strip() or None)
        if raw_execution_reason is not None
        else None,
        "final_execution_status": raw_status or None,
        "final_operator_execution_status": raw_operator or None,
        "final_execution_reason": (str(raw_execution_reason).strip() or None)
        if raw_execution_reason is not None
        else None,
        "reconciled_to_target_state": False,
        "reconciliation_override_applied": False,
    }

    # Condition 1 — candidate must be a broker-abort partial.
    if raw_operator != "partial" or outcome not in _RECONCILABLE_EXECUTION_OUTCOMES:
        return result

    recon_ok = recon_status in _OK_RECONCILIATION_STATUSES  # conditions 2
    no_unresolved = _safe_int(posttrade_unresolved_orders_count) == 0  # condition 5
    no_skipped = (
        _safe_int(skipped_buy_count) == 0
        and _safe_int(blocked_buy_count) == 0
        and _safe_int(pending_buy_count) == 0
    )  # condition 3
    no_rejects = (
        _safe_int(rejected_count) == 0 and not str(broker_reject_status or "").strip()
    )  # condition 4
    has_submissions = _safe_int(submitted_count) > 0  # condition 5

    if recon_ok and no_unresolved and no_skipped and no_rejects and has_submissions:
        result.update(
            {
                "final_execution_status": STATUS_RECONCILED_SUCCESS,
                "final_operator_execution_status": RECONCILED_SUCCESS_OPERATOR_STATUS,
                "final_execution_reason": RECONCILED_TO_TARGET_REASON,
                "reconciled_to_target_state": True,
                "reconciliation_override_applied": True,
            }
        )
    return result


def normalize_status(
    execution_status: str | None = None,
    halt_reason: str | None = None,
    executable_trades_count: int = 0,
) -> str:
    """
    Normalize execution status to canonical set.

    Canonical statuses:
      - NO_ACTION: no trades proposed after constraints
      - READY: trades exist and can be executed
      - HALTED: execution cannot proceed (blocker)
      - EXECUTED: orders submitted to broker (set by executor only)
      - MARKET_CLOSED: execution intentionally skipped on a closed market day
      - SKIPPED_DUPLICATE: already executed this run_id (set by executor only)

    Args:
        execution_status: Raw status from planner
        halt_reason: Halt reason if execution blocked
        executable_trades_count: Number of executable trades

    Returns:
        Normalized status string
    """
    raw_status = str(execution_status or "").strip().upper()

    if raw_status == STATUS_MARKET_CLOSED:
        return STATUS_MARKET_CLOSED

    # If explicitly halted or halt reason present
    if halt_reason or raw_status == "HALTED":
        return STATUS_HALTED

    # If no executable trades
    if executable_trades_count == 0:
        return STATUS_NO_ACTION

    # Otherwise ready for execution
    return STATUS_READY


def write_canonical_execution_payload(
    payload: dict,
    run_date: str,
    run_root: Path | None = None,
    *,
    allow_overwrite: bool = False,
) -> Path:
    target_root = run_root
    if target_root is None:
        target_root = Path("outputs") / "execution_payload"
        target_root.mkdir(parents=True, exist_ok=True)

    out_path = target_root / "execution_payload.json"
    
    # Normalize status using canonical function
    raw_status = str((payload or {}).get("execution_status") or "UNKNOWN")
    halt_reason = (payload or {}).get("halt_reason")
    model_proposed_count = int((payload or {}).get("model_proposed_trades_count") or 0)
    planner_intended_count = int(
        (payload or {}).get("planner_intended_trades_count")
        or (payload or {}).get("proposed_trades_intent_count")
        or (payload or {}).get("proposed_trades_intent")
        or 0
    )
    executable_count = int(
        (payload or {}).get("execution_eligible_trades_count")
        or (payload or {}).get("executable_trades_count")
        or 0
    )
    orders_submitted_count = int((payload or {}).get("orders_submitted_count") or 0)
    orders_filled_count = int((payload or {}).get("orders_filled_count") or 0)
    
    normalized_status = normalize_status(
        execution_status=raw_status,
        halt_reason=halt_reason,
        executable_trades_count=executable_count,
    )
    
    canonical_payload = {
        "run_id": str((payload or {}).get("run_id") or ""),
        "trade_date": str((payload or {}).get("trade_date") or run_date),
        "mode": str((payload or {}).get("mode") or "UNKNOWN"),
        "status": normalized_status,  # Canonical status field
        "halt_reason": halt_reason,
        "execution_outcome": (payload or {}).get("execution_outcome"),
        "execution_reason": (payload or {}).get("execution_reason"),
        "reason_code": (payload or {}).get("reason_code"),
        "cash_rebalance_status": (payload or {}).get("cash_rebalance_status"),
        "broker_reject_status": (payload or {}).get("broker_reject_status"),
        "broker_reject_message": (payload or {}).get("broker_reject_message"),
        "submitted_count": int((payload or {}).get("submitted_count") or 0),
        "accepted_count": int((payload or {}).get("accepted_count") or 0),
        "rejected_count": int((payload or {}).get("rejected_count") or 0),
        "trades": list((payload or {}).get("trades") or []),
        "model_proposed_trades_count": model_proposed_count,
        "planner_intended_trades_count": planner_intended_count,
        "execution_eligible_trades_count": executable_count,
        "orders_submitted_count": orders_submitted_count,
        "orders_filled_count": orders_filled_count,
        "planned_payload_trade_count": int((payload or {}).get("planned_payload_trade_count") or 0),
        "exact_plan_enabled": bool((payload or {}).get("exact_plan_enabled")),
        "reason": (payload or {}).get("reason"),
        "operator_action_required": (payload or {}).get("operator_action_required"),
        "market_closed_expected_skip": bool((payload or {}).get("market_closed_expected_skip")),
        "planned_payload_drop_diagnostics": (payload or {}).get("planned_payload_drop_diagnostics"),
        "proposed_trades_intent_count": planner_intended_count,
        "proposed_trades_intent": planner_intended_count,
        "executable_trades_count": executable_count,
        # Compatibility fields for legacy code
        "execution_status": normalized_status,
        "status_label": (payload or {}).get("status_label"),
        "status_reason": (payload or {}).get("status_reason"),
        "operator_execution_status": (payload or {}).get("operator_execution_status"),
        "execution_source": (payload or {}).get("execution_source"),
        "planning_price_basis": (payload or {}).get("planning_price_basis"),
        "pricing_asof": (payload or {}).get("pricing_asof"),
        "execution_price_requirement": (payload or {}).get("execution_price_requirement"),
        "price_freshness_scope": (payload or {}).get("price_freshness_scope"),
        "timing_status": (payload or {}).get("timing_status"),
        "preferred_target_et": (payload or {}).get("preferred_target_et"),
        "degraded_auto_trade_deadline_et": (payload or {}).get("degraded_auto_trade_deadline_et"),
        "actual_workflow_start_et": (payload or {}).get("actual_workflow_start_et"),
        "actual_execution_start_et": (payload or {}).get("actual_execution_start_et"),
        "first_submit_et": (payload or {}).get("first_submit_et"),
        "retry_attempt_count": int((payload or {}).get("retry_attempt_count") or 0),
        "retry_eligible": (payload or {}).get("retry_eligible"),
        "retry_reason": (payload or {}).get("retry_reason"),
        "continuation_eligible": (payload or {}).get("continuation_eligible"),
        "continuation_reason": (payload or {}).get("continuation_reason"),
        "pending_buy_count": int((payload or {}).get("pending_buy_count") or 0),
        "pending_buy_orders": list((payload or {}).get("pending_buy_orders") or []),
        "buy_phase_planned": int((payload or {}).get("buy_phase_planned") or 0),
        "buy_phase_submitted": int((payload or {}).get("buy_phase_submitted") or 0),
        "buy_phase_block_reason": (payload or {}).get("buy_phase_block_reason"),
        "sell_phase_status": (payload or {}).get("sell_phase_status"),
        "sell_phase_completion_reason": (payload or {}).get("sell_phase_completion_reason"),
        "submitted_buy_count": int((payload or {}).get("submitted_buy_count") or 0),
        "submitted_sell_count": int((payload or {}).get("submitted_sell_count") or 0),
        "capital_allows_pending_buys": bool((payload or {}).get("capital_allows_pending_buys")),
        "continuation_mode": (payload or {}).get("continuation_mode"),
        "continuation_source": (payload or {}).get("continuation_source"),
        "continuation_intended_orders_path": (payload or {}).get("continuation_intended_orders_path"),
        "cash_target_weight": (payload or {}).get("cash_target_weight"),
        "achieved_cash_weight": (payload or {}).get("achieved_cash_weight"),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    safe_write_text(
        out_path,
        json.dumps(canonical_payload, indent=2) + "\n",
        allow_overwrite=allow_overwrite or target_root.name == "execution_payload",
    )
    return out_path
