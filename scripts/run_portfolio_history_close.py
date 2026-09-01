#!/usr/bin/env python3
"""Run the post-close portfolio chain while always evaluating NAV escalation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def run_close_chain(
    *,
    repo_root: Path,
    trade_date: str,
    send_escalation: bool,
) -> dict[str, int | None | str]:
    root = Path(repo_root).resolve()
    python = sys.executable

    history = subprocess.run(
        [
            python,
            "scripts/build_portfolio_history.py",
            "--trade-date",
            trade_date,
            "--require-causal-valuation",
        ],
        cwd=root,
        check=False,
    )

    audit_returncode: int | None = None
    if history.returncode == 0:
        audit = subprocess.run(
            [
                python,
                "scripts/build_daily_portfolio_audit.py",
                "--trade-date",
                trade_date,
            ],
            cwd=root,
            check=False,
        )
        audit_returncode = audit.returncode

    escalation_command = [
        python,
        "-m",
        "core.portfolio_history_escalation",
        "--trade-date",
        trade_date,
        "--repo-root",
        str(root),
    ]
    if send_escalation:
        escalation_command.append("--send")
    escalation = subprocess.run(escalation_command, cwd=root, check=False)

    if history.returncode != 0:
        returncode = history.returncode
    elif audit_returncode not in {None, 0}:
        returncode = audit_returncode
    else:
        returncode = escalation.returncode

    result: dict[str, int | None | str] = {
        "schema_version": "caerus.portfolio_history_close.v1",
        "trade_date": trade_date,
        "portfolio_history_returncode": history.returncode,
        "daily_audit_returncode": audit_returncode,
        "escalation_returncode": escalation.returncode,
        "returncode": returncode,
    }
    print(json.dumps(result, sort_keys=True))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run portfolio history, daily audit, and unconditional NAV escalation."
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--send-escalation", action="store_true")
    args = parser.parse_args(argv)

    result = run_close_chain(
        repo_root=Path(args.repo_root),
        trade_date=args.trade_date,
        send_escalation=args.send_escalation,
    )
    return int(result["returncode"] or 0)


if __name__ == "__main__":
    raise SystemExit(main())
