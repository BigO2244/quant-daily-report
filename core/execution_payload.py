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
    executable_count = int((payload or {}).get("executable_trades_count") or 0)
    
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
        "trades": list((payload or {}).get("trades") or []),
        "executable_trades_count": executable_count,
        # Compatibility fields for legacy code
        "execution_status": normalized_status,
        "status_label": (payload or {}).get("status_label"),
        "status_reason": (payload or {}).get("status_reason"),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    safe_write_text(
        out_path,
        json.dumps(canonical_payload, indent=2) + "\n",
        allow_overwrite=allow_overwrite or target_root.name == "execution_payload",
    )
    return out_path
