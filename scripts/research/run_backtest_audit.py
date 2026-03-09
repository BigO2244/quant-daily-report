#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run backtest-mode audit generation via daily_quant_report.py and verify outputs."
    )
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--policy", default="FULL")
    parser.add_argument("--audit-root", default="outputs/audit")
    parser.add_argument("--min-dates", type=int, default=200)
    parser.add_argument(
        "--synthetic",
        dest="synthetic",
        action="store_true",
        default=True,
        help="Use deterministic synthetic data (default on).",
    )
    parser.add_argument(
        "--live-data",
        dest="synthetic",
        action="store_false",
        help="Use live market data instead of synthetic fixture data.",
    )
    return parser.parse_args()


def _run(cmd: list[str], env: dict[str, str]) -> None:
    proc = subprocess.run(cmd, env=env)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def main() -> None:
    args = parse_args()

    env = os.environ.copy()
    env["BACKTEST_START"] = str(args.start)
    env["BACKTEST_END"] = str(args.end)
    env["AUDIT_EXPORT"] = "1"
    env["AUDIT_RUN_ID"] = str(args.run_id)
    env["AUDIT_OUTDIR"] = str(args.audit_root)
    env["BREAKER_POLICY"] = str(args.policy).strip().upper() or "FULL"
    env.setdefault("BREAKER_STATE_CAN_OVERRIDE", "0")
    env["BACKTEST_SYNTHETIC"] = "1" if bool(args.synthetic) else "0"

    _run([sys.executable, "daily_quant_report.py"], env=env)
    _run(
        [
            sys.executable,
            "scripts/verify_audit_outputs.py",
            "--run-id",
            str(args.run_id),
            "--start",
            str(args.start),
            "--end",
            str(args.end),
            "--audit-root",
            str(args.audit_root),
            "--min-dates",
            str(int(args.min_dates)),
        ],
        env=env,
    )
    print(
        "[RUN_BACKTEST_AUDIT] ok "
        f"run_id={args.run_id} start={args.start} end={args.end} "
        f"policy={env['BREAKER_POLICY']} synthetic={env['BACKTEST_SYNTHETIC']} "
        f"audit_root={Path(args.audit_root)}"
    )


if __name__ == "__main__":
    main()
