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
STATUS_SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
STATUS_IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"


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
      - SKIPPED_DUPLICATE: already executed this run_id (set by executor only)

    Args:
        execution_status: Raw status from planner
        halt_reason: Halt reason if execution blocked
        executable_trades_count: Number of executable trades

    Returns:
        Normalized status string
    """
    # If explicitly halted or halt reason present
    if halt_reason or (execution_status or "").upper() == "HALTED":
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
        "proposed_trades_intent_count": planner_intended_count,
        "proposed_trades_intent": planner_intended_count,
        "executable_trades_count": executable_count,
        # Compatibility fields for legacy code
        "execution_status": normalized_status,
        "status_label": (payload or {}).get("status_label"),
        "status_reason": (payload or {}).get("status_reason"),
        "operator_execution_status": (payload or {}).get("operator_execution_status"),
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
        "continuation_source": (payload or {}).get("continuation_source"),
        "continuation_intended_orders_path": (payload or {}).get("continuation_intended_orders_path"),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    safe_write_text(
        out_path,
        json.dumps(canonical_payload, indent=2) + "\n",
        allow_overwrite=allow_overwrite or target_root.name == "execution_payload",
    )
    return out_path
