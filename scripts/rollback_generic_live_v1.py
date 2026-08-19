#!/usr/bin/env python3
"""Apply one idempotent named-break rollback for generic Live v1."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
from pathlib import Path

from core.generic_live_v1_ops import perform_generic_live_v1_rollback
from core.generic_live_v1_submission import ensure_generic_live_v1_rearmed_after_failure


def _current_crontab() -> str:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode not in {0, 1}:
        raise RuntimeError("could not read crontab for rollback")
    return result.stdout if result.returncode == 0 else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trigger", required=True)
    parser.add_argument("--state-path", type=Path, required=True)
    parser.add_argument("--preflight-hash")
    parser.add_argument("--plan-hash")
    parser.add_argument("--exact-cron-line", required=True)
    parser.add_argument("--active-config-path", type=Path, required=True)
    parser.add_argument("--backup-config-path", type=Path, required=True)
    parser.add_argument("--paper-path", action="append", type=Path, required=True)
    parser.add_argument("--evidence-path", type=Path, required=True)
    parser.add_argument("--allowed-root", action="append", type=Path, required=True)
    args = parser.parse_args()
    now = dt.datetime.now(dt.timezone.utc).isoformat()

    def rearm(trigger: str) -> dict:
        return ensure_generic_live_v1_rearmed_after_failure(
            state_path=args.state_path,
            preflight_hash=args.preflight_hash,
            plan_hash=args.plan_hash,
            rearmed_at=now,
        )

    def apply_crontab(updated: str) -> None:
        subprocess.run(["crontab", "-"], input=updated, text=True, check=True)

    result = perform_generic_live_v1_rollback(
        trigger=args.trigger,
        rearm_action=rearm,
        current_crontab=_current_crontab(),
        exact_cron_line=args.exact_cron_line,
        apply_crontab=apply_crontab,
        active_config_path=args.active_config_path,
        backup_config_path=args.backup_config_path,
        paper_paths=args.paper_path,
        evidence_path=args.evidence_path,
        allowed_roots=args.allowed_root,
        rolled_back_at=now,
    )
    print(json.dumps({"status": result["status"], "content_hash": result["content_hash"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
