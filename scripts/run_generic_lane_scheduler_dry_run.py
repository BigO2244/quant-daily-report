#!/usr/bin/env python3
"""Run the off-by-default, no-submit generic lane scheduler boundary."""

from __future__ import annotations

import argparse
import json

from core.lane_exact_plan_dry_run import read_strict_json
from core.lane_scheduler_dry_run import run_generic_lane_scheduler_dry_run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exact-plan", required=True)
    parser.add_argument("--environment-binding", required=True)
    parser.add_argument("--safety-evidence", required=True)
    parser.add_argument("--live-cutover-preflight")
    parser.add_argument("--enable-advisory-scheduler", action="store_true")
    args = parser.parse_args()
    result = run_generic_lane_scheduler_dry_run(
        exact_plan=read_strict_json(args.exact_plan),
        environment_binding=read_strict_json(args.environment_binding),
        safety_evidence=read_strict_json(args.safety_evidence),
        live_cutover_preflight=(
            read_strict_json(args.live_cutover_preflight)
            if args.live_cutover_preflight else None
        ),
        scheduler_enabled=args.enable_advisory_scheduler,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
