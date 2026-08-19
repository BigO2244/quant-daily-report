#!/usr/bin/env python3
"""Finalize a generic Live v1 causal chain from explicit protected artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from core.generic_live_v1_ops import require_protected_mode, secure_path, secure_read_json
from core.generic_live_v1_posttrade import finalize_generic_live_v1_production_posttrade
from core.generic_live_v1_submission import ensure_generic_live_v1_rearmed_after_failure


def _array(payload: Any, *, key: str) -> list[dict]:
    value = payload.get(key) if isinstance(payload, dict) else payload
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise RuntimeError(f"{key} input must be an object array or {{{key!r}: [...]}}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--submission-result", type=Path, required=True)
    parser.add_argument("--exact-plan", type=Path, required=True)
    parser.add_argument("--order-lifecycle", type=Path, required=True)
    parser.add_argument("--reconciliation", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--valuations", type=Path, required=True)
    parser.add_argument("--performance", type=Path, required=True)
    parser.add_argument("--daily-lane-audit", type=Path, required=True)
    parser.add_argument("--all-lane-audit", type=Path, required=True)
    parser.add_argument("--dashboard-projection", type=Path, required=True)
    parser.add_argument("--session-gate-path", type=Path, required=True)
    parser.add_argument("--base-result-path", type=Path, required=True)
    parser.add_argument("--closure-result-path", type=Path, required=True)
    parser.add_argument("--finalized-at", required=True)
    args = parser.parse_args()

    require_protected_mode(args.input_root, directory=True)
    require_protected_mode(args.state_root, directory=True)
    inputs = [
        args.submission_result, args.exact_plan, args.order_lifecycle,
        args.reconciliation, args.journal, args.valuations, args.performance,
        args.daily_lane_audit, args.all_lane_audit, args.dashboard_projection,
    ]
    for path in inputs:
        secure_path(path, allowed_roots=[args.input_root], must_exist=True, kind="file")
    gate = secure_path(
        args.session_gate_path, allowed_roots=[args.state_root], must_exist=True,
        kind="file",
    )
    for path in (args.base_result_path, args.closure_result_path):
        secure_path(path, allowed_roots=[args.state_root], must_exist=False, kind="file")

    submission: dict = {}
    plan: dict = {}
    try:
        submission = secure_read_json(args.submission_result, allowed_roots=[args.input_root])
        plan = secure_read_json(args.exact_plan, allowed_roots=[args.input_root])
        built = finalize_generic_live_v1_production_posttrade(
            submission_result=submission,
            exact_plan=plan,
            order_lifecycle=secure_read_json(
                args.order_lifecycle, allowed_roots=[args.input_root]
            ),
            reconciliation=secure_read_json(
                args.reconciliation, allowed_roots=[args.input_root]
            ),
            journal_entries=_array(
                secure_read_json(args.journal, allowed_roots=[args.input_root]),
                key="journal_entries",
            ),
            valuations=_array(
                secure_read_json(args.valuations, allowed_roots=[args.input_root]),
                key="valuations",
            ),
            performance=secure_read_json(args.performance, allowed_roots=[args.input_root]),
            daily_lane_audit=secure_read_json(
                args.daily_lane_audit, allowed_roots=[args.input_root]
            ),
            all_lane_audit=secure_read_json(
                args.all_lane_audit, allowed_roots=[args.input_root]
            ),
            dashboard_projection=secure_read_json(
                args.dashboard_projection, allowed_roots=[args.input_root]
            ),
            finalized_at=args.finalized_at,
            rearm_state_path=gate,
            base_result_path=args.base_result_path,
            closure_result_path=args.closure_result_path,
        )
    except Exception:
        ensure_generic_live_v1_rearmed_after_failure(
            state_path=gate,
            preflight_hash=submission.get("preflight_hash"),
            plan_hash=plan.get("content_hash"),
            rearmed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        raise
    print(json.dumps(built, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
