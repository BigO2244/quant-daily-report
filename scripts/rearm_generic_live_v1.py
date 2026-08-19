#!/usr/bin/env python3
"""Fail-safe generic Live v1 rearm helper; never submits or disarms."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from core.generic_live_v1_submission import ensure_generic_live_v1_rearmed_after_failure
from core.generic_live_v1_ops import require_protected_mode, secure_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-path", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--preflight-hash", required=True)
    parser.add_argument("--plan-hash", required=True)
    parser.add_argument("--trigger", required=True)
    args = parser.parse_args()
    if args.trigger != "PREFLIGHT_BREAK":
        parser.error("standalone rearm helper permits PREFLIGHT_BREAK only")
    secure_path(args.state_root, allowed_roots=[args.state_root], must_exist=True, kind="directory")
    require_protected_mode(args.state_root, directory=True)
    state_path = secure_path(
        args.state_path, allowed_roots=[args.state_root], must_exist=False, kind="file"
    )
    payload = ensure_generic_live_v1_rearmed_after_failure(
        state_path=state_path,
        preflight_hash=args.preflight_hash,
        plan_hash=args.plan_hash,
        rearmed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
    )
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
