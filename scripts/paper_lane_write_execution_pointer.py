"""Write the unified paper lane's workflow pointers.

The unified PAPER lane (scripts/cron_execute.sh, 9:35 ET) runs the SAME engine as
the live pilot (plan builder + live_pilot_execute.py) with MODE=paper against the
Alpaca paper endpoint. The 10:00 ET confirm flow (scripts/cron_confirm.sh,
daily_trade_execution_email.py, scripts.send_trading_confirmation_email) resolves
Phase 2 through ``outputs/workflow/<date>/execution.json`` via
``core.run_pointer.read_trade_stage_pointer``. This helper keeps that contract:

1. writes the compatible ``execution`` stage pointer (same schema the dormant
   ``run_precomputed_alpaca_execution`` path wrote), and
2. writes a lane-scoped ``paper_lane_execution.json`` pointer beside it
   (mirroring the live lane's ``live_pilot_execution.json``).

Terminal-status mapping (executor terminal_status -> pointer status):

- ``running``               -> ``running`` (pre-execution placeholder)
- ``SUBMITTED``             -> ``success``
- ``DRY_RUN``               -> ``no_action`` (dry pass only; nothing submitted)
- ``BLOCKED`` with reason ``live_pilot_transition_no_actionable_order``
                            -> ``no_action`` (book already at target; benign no-op)
- ``BLOCKED`` (other)       -> ``failed_blocked``
- ``SUBMITTED_UNFILLED``    -> ``failed_incomplete`` (orders exist; never retry)
- ``FAILED_RECONCILIATION`` -> ``failed_reconciliation``
- anything else / missing   -> ``failed_unknown``

``lane_exit_ok`` in the printed JSON tells the cron wrapper whether the day should
count as a healthy lane run (success/no_action/running) or a failure.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.run_pointer import (
    TERMINAL_STATUS_NO_ACTION,
    TERMINAL_STATUS_SUCCESS,
    write_trade_stage_pointer,
)

PAPER_LANE_POINTER_FILENAME = "paper_lane_execution.json"
NO_ACTIONABLE_ORDER_REASON = "live_pilot_transition_no_actionable_order"


def map_terminal_status(terminal_status: str, reason_code: str = "") -> tuple[str, str | None]:
    """Return ``(pointer_status, substatus)`` for an executor terminal status."""
    terminal = str(terminal_status or "").strip()
    reason = str(reason_code or "").strip()
    upper = terminal.upper()
    if terminal.lower() == "running":
        return "running", None
    if upper == "SUBMITTED":
        return TERMINAL_STATUS_SUCCESS, None
    if upper == "DRY_RUN":
        return TERMINAL_STATUS_NO_ACTION, "dry_run_only"
    if upper == "BLOCKED" and NO_ACTIONABLE_ORDER_REASON in reason:
        return TERMINAL_STATUS_NO_ACTION, "no_actionable_order"
    if upper == "BLOCKED":
        return "failed_blocked", reason or None
    if upper == "SUBMITTED_UNFILLED":
        return "failed_incomplete", "SUBMITTED_UNFILLED"
    if upper == "FAILED_RECONCILIATION":
        return "failed_reconciliation", reason or None
    return "failed_unknown", (terminal or "missing_terminal_status")


def lane_exit_ok(pointer_status: str) -> bool:
    return not str(pointer_status or "").startswith("failed")


def write_paper_lane_pointers(
    *,
    trade_date: str,
    run_id: str,
    run_root: str,
    terminal_status: str,
    reason_code: str = "",
    status_message: str = "",
    workspace_root: str | None = None,
) -> dict[str, object]:
    status, substatus = map_terminal_status(terminal_status, reason_code)
    message = status_message or reason_code or None

    execution_pointer_path = write_trade_stage_pointer(
        stage="execution",
        run_id=run_id,
        trade_date=trade_date,
        mode="PAPER",
        run_root=run_root,
        status=status,
        substatus=substatus,
        status_message=message,
        workspace_root=workspace_root,
    )

    lane_pointer_path = (
        Path(workspace_root) if workspace_root else Path.cwd()
    ) / "outputs" / "workflow" / str(trade_date) / PAPER_LANE_POINTER_FILENAME
    lane_pointer_path.parent.mkdir(parents=True, exist_ok=True)
    lane_payload = {
        "stage": "paper_lane_execution",
        "trade_date": trade_date,
        "mode": "PAPER",
        "run_id": run_id,
        "run_root": run_root,
        "status": status,
        "substatus": substatus,
        "terminal_status": str(terminal_status or ""),
        "status_message": message,
        "engine": "live_pilot_execute (unified lane, paper endpoint)",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    lane_pointer_path.write_text(
        json.dumps(lane_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return {
        "execution_pointer": str(execution_pointer_path),
        "paper_lane_pointer": str(lane_pointer_path),
        "status": status,
        "substatus": substatus,
        "lane_exit_ok": lane_exit_ok(status),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write unified paper-lane workflow pointers")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--terminal-status", required=True)
    parser.add_argument("--reason-code", default="")
    parser.add_argument("--status-message", default="")
    parser.add_argument("--workspace-root", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = write_paper_lane_pointers(
        trade_date=args.trade_date,
        run_id=args.run_id,
        run_root=args.run_root,
        terminal_status=args.terminal_status,
        reason_code=args.reason_code,
        status_message=args.status_message,
        workspace_root=args.workspace_root,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
