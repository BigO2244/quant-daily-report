#!/usr/bin/env python3
"""Fail-safe generic Live v1 rearm helper; never submits or disarms."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from core.generic_live_v1_submission import rearm_generic_live_v1_session


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-path", type=Path, required=True)
    parser.add_argument("--preflight-hash", required=True)
    parser.add_argument("--plan-hash", required=True)
    parser.add_argument("--trigger", required=True)
    args = parser.parse_args()
    payload = rearm_generic_live_v1_session(
        state_path=args.state_path,
        preflight_hash=args.preflight_hash,
        plan_hash=args.plan_hash,
        rearmed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        trigger=args.trigger,
    )
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
