"""
Run pointer management.

Two pointer classes exist:

1. ``outputs/latest_run.json`` records the most recent run activity of any kind.
   It is useful for dashboards and generic tooling, but it is mutable shared
   state and should not be used as the only workflow handoff between critical
   trading phases.
2. ``outputs/workflow/<trade_date>/<stage>.json`` records trade-date-scoped,
   stage-specific pointers (for example ``execution``). These are the
   high-confidence handoff artifacts used by execution and confirmation flows.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


LATEST_RUN_POINTER = 'outputs/latest_run.json'
WORKFLOW_POINTER_ROOT = "outputs/workflow"
VALID_WORKFLOW_STAGES = frozenset({"precompute", "execution", "confirmation"})

# ── Terminal run status constants ─────────────────────────────────────────────
# Use these instead of raw strings to ensure consistent classification across
# daily_quant_report.py, operator_summary.py, and the dashboard builder.
TERMINAL_STATUS_SUCCESS = "success"
TERMINAL_STATUS_NO_ACTION = "no_action"
TERMINAL_STATUS_FAILED_PRE_PAYLOAD = "failed_pre_payload"
TERMINAL_STATUS_FAILED_PRE_EXECUTION = "failed_pre_execution"
TERMINAL_STATUS_FAILED_BROKER_PROBE = "failed_broker_probe"
TERMINAL_STATUS_FAILED_UNKNOWN = "failed_unknown"


def is_run_complete(status: str) -> bool:
    """Return True if *status* represents a terminal non-failure state."""
    return status in {TERMINAL_STATUS_SUCCESS, TERMINAL_STATUS_NO_ACTION}


def is_run_failure(status: str) -> bool:
    """Return True if *status* represents a failure / incomplete state."""
    return not is_run_complete(status) and status not in {"running", "bootstrapped"}


def _resolve_workspace_root(workspace_root: str | None) -> Path:
    return Path(workspace_root) if workspace_root is not None else Path(os.getcwd())


def _normalize_workflow_stage(stage: str) -> str:
    normalized = str(stage or "").strip().lower()
    if normalized not in VALID_WORKFLOW_STAGES:
        raise ValueError(f"invalid workflow stage: {stage!r}")
    return normalized


def workflow_stage_pointer_path(
    trade_date: str,
    stage: str,
    workspace_root: str | None = None,
) -> Path:
    root = _resolve_workspace_root(workspace_root)
    normalized_stage = _normalize_workflow_stage(stage)
    return root / WORKFLOW_POINTER_ROOT / str(trade_date) / f"{normalized_stage}.json"


def write_latest_run_pointer(
    run_id: str,
    trade_date: str,
    mode: str,
    run_root: str,
    status: str = 'success',
    substatus: str | None = None,
    status_message: str | None = None,
    workspace_root: str = None,
) -> str:
    """
    Write the canonical pointer to the latest trading run.
    
    Args:
        run_id: Unique run identifier (e.g., '20260309T093456Z_paper_1')
        trade_date: Trading date in YYYY-MM-DD format
        mode: Execution mode (PAPER, ALPACA, SHADOW, LIVE, etc.)
        run_root: Absolute or relative path to run root (e.g., 'outputs/runs/20260309T093456Z_paper_1/')
        status: Run completion status (success, halted, failed, bootstrapped)
        workspace_root: Workspace directory (defaults to cwd)
    
    Returns:
        Path where pointer was written
    
    Raises:
        IOError: If pointer cannot be written
    """
    workspace = _resolve_workspace_root(workspace_root)
    pointer_path = workspace / LATEST_RUN_POINTER
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    
    pointer_data = {
        'run_id': run_id,
        'trade_date': trade_date,
        'mode': mode,
        'run_root': run_root,
        'status': status,
        'substatus': substatus,
        'status_message': status_message,
        'created_at': datetime.utcnow().isoformat() + 'Z',
    }
    
    with open(pointer_path, 'w') as f:
        json.dump(pointer_data, f, indent=2)
    
    return str(pointer_path)


def read_latest_run_pointer(workspace_root: str = None) -> Optional[dict]:
    """
    Read the canonical pointer to the latest trading run.
    
    Args:
        workspace_root: Workspace directory (defaults to cwd)
    
    Returns:
        Latest run metadata dict with keys (run_id, trade_date, mode, run_root, status, created_at)
        or None if pointer does not exist
    
    Raises:
        json.JSONDecodeError: If pointer file is malformed
    """
    workspace = _resolve_workspace_root(workspace_root)
    pointer_path = workspace / LATEST_RUN_POINTER

    if not pointer_path.exists():
        # Fallback: legacy outputs/latest.json uses 'path' key instead of 'run_root'.
        legacy_path = workspace / "outputs/latest.json"
        if legacy_path.exists():
            try:
                data = json.loads(legacy_path.read_text(encoding="utf-8"))
                run_root = data.get("run_root") or data.get("path") or ""
                if run_root:
                    return {
                        "run_id": data.get("run_id", ""),
                        "trade_date": data.get("report_date", ""),
                        "mode": data.get("mode", ""),
                        "run_root": run_root,
                        "status": data.get("status", "unknown"),
                        "created_at": data.get("created_at", ""),
                        "_source": "legacy_latest_json",
                    }
            except Exception:
                pass
        return None

    with open(pointer_path, 'r') as f:
        return json.load(f)


def write_trade_stage_pointer(
    *,
    stage: str,
    run_id: str,
    trade_date: str,
    mode: str,
    run_root: str,
    status: str = "success",
    substatus: str | None = None,
    status_message: str | None = None,
    workspace_root: str | None = None,
) -> str:
    pointer_path = workflow_stage_pointer_path(
        trade_date=trade_date,
        stage=stage,
        workspace_root=workspace_root,
    )
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_data = {
        "stage": _normalize_workflow_stage(stage),
        "run_id": run_id,
        "trade_date": trade_date,
        "mode": mode,
        "run_root": run_root,
        "status": status,
        "substatus": substatus,
        "status_message": status_message,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    with open(pointer_path, "w") as f:
        json.dump(pointer_data, f, indent=2)
    return str(pointer_path)


def read_trade_stage_pointer(
    trade_date: str,
    stage: str,
    workspace_root: str | None = None,
) -> Optional[dict]:
    pointer_path = workflow_stage_pointer_path(
        trade_date=trade_date,
        stage=stage,
        workspace_root=workspace_root,
    )
    if not pointer_path.exists():
        return None
    with open(pointer_path, "r") as f:
        return json.load(f)


def resolve_trade_stage_pointer(
    trade_date: str,
    stage: str,
    workspace_root: str | None = None,
) -> Optional[dict]:
    """Resolve the authoritative pointer for a trade-date workflow stage.

    Stage-specific pointers are preferred. ``latest_run.json`` remains a
    backward-compatible fallback only when it matches the requested trade date.
    """
    pointer = read_trade_stage_pointer(trade_date, stage, workspace_root)
    if isinstance(pointer, dict):
        return pointer

    latest = read_latest_run_pointer(workspace_root)
    if isinstance(latest, dict) and str(latest.get("trade_date") or "") == str(trade_date):
        fallback = dict(latest)
        fallback.setdefault("_source", "latest_run_json")
        fallback.setdefault("stage", _normalize_workflow_stage(stage))
        return fallback
    return None


def get_canonical_run_root(workspace_root: str = None) -> Optional[str]:
    """
    Get the path to the canonical run root from latest_run.json.
    
    Returns None if pointer does not exist (system not bootstrapped).
    """
    latest = read_latest_run_pointer(workspace_root)
    return latest.get('run_root') if latest else None


def get_canonical_run_id(workspace_root: str = None) -> Optional[str]:
    """Get the run_id from latest_run.json."""
    latest = read_latest_run_pointer(workspace_root)
    return latest.get('run_id') if latest else None


def is_pointer_fresh(
    trade_date: str,
    workspace_root: str = None,
    max_age_seconds: int = 28800,  # 8 hours
) -> bool:
    """
    Check if the latest_run.json pointer is fresh for the given trade date.
    
    Args:
        trade_date: Expected trade date (YYYY-MM-DD)
        workspace_root: Workspace directory
        max_age_seconds: Maximum age in seconds (defaults to 8 hours)
    
    Returns:
        True if pointer exists, matches trade_date, and is recent
    """
    latest = read_latest_run_pointer(workspace_root)
    if not latest:
        return False
    
    # Check date match
    if latest.get('trade_date') != trade_date:
        return False
    
    # Check recency
    created_at_str = latest.get('created_at', '')
    try:
        created_at = datetime.fromisoformat(created_at_str.rstrip('Z'))
        age = (datetime.utcnow() - created_at).total_seconds()
        return age <= max_age_seconds
    except (ValueError, TypeError):
        return False
