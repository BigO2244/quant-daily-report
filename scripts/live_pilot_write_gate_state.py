from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.live_pilot_gate_state import write_live_pilot_gate_state


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write a redacted LIVE_PILOT gate-state artifact")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--output-root", default="outputs/live_pilot")
    parser.add_argument("--decision", choices=["ALLOWED", "BLOCKED"], required=True)
    parser.add_argument("--block-reason", default="")
    parser.add_argument("--broker-orders-submitted", type=int, default=0)
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
    print(json.dumps({"gate_state_path": str(path)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
