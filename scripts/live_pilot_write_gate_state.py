from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.live_pilot_gate_state import write_live_pilot_gate_state
from paper.run_manager import safe_write_text


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write a redacted LIVE_PILOT gate-state artifact")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--output-root", default="outputs/live_pilot")
    parser.add_argument("--decision", choices=["ALLOWED", "BLOCKED"], required=True)
    parser.add_argument("--block-reason", default="")
    parser.add_argument("--broker-orders-submitted", type=int, default=0)
    parser.add_argument("--running-sha", default="")
    parser.add_argument("--deployed-sha", default="")
    parser.add_argument("--guard-message", default="")
    parser.add_argument("--tree-dirty", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    run_root = Path(args.output_root) / "runs" / args.run_id
    path = write_live_pilot_gate_state(
        run_root=run_root,
        run_id=args.run_id,
        trade_date=args.trade_date,
        repo_root=REPO_ROOT,
        decision=args.decision,
        block_reason=args.block_reason or None,
        broker_orders_submitted=args.broker_orders_submitted,
    )
    results_path = None
    if args.decision == "BLOCKED":
        results_path = run_root / "execution_results.json"
        results = {
            "schema_version": "live_pilot_execution_results.v1",
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "run_id": args.run_id,
            "run_root": str(run_root),
            "trade_date": args.trade_date,
            "mode": "LIVE_PILOT",
            "status": "HALTED",
            "terminal_status": "HALTED",
            "operator_execution_status": "halted",
            "halt_reason": args.block_reason or "live_pilot_gate_blocked",
            "reason": args.block_reason or "live_pilot_gate_blocked",
            "gate_decision": "BLOCKED",
            "submitted_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "filled_count": 0,
            "broker_orders_submitted": int(args.broker_orders_submitted or 0),
            "broker_status_refresh_claims_broker_truth": False,
            "running_sha": args.running_sha or None,
            "deployed_sha": args.deployed_sha or None,
            "deploy_tree_dirty": args.tree_dirty or None,
            "deploy_guard_message": args.guard_message or None,
            "gate_state_path": str(path),
        }
        safe_write_text(
            results_path,
            json.dumps(results, indent=2, sort_keys=True) + "\n",
            allow_overwrite=True,
        )
    print(
        json.dumps(
            {
                "gate_state_path": str(path),
                "execution_results_path": str(results_path) if results_path else None,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
