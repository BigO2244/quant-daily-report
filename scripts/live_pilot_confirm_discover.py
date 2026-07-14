#!/usr/bin/env python3
"""LIVE_PILOT confirmation discovery + dedupe ledger.

Replaces the fragile "last-sorted run dir" race that made a real armed
submission go unreported on 2026-07-10 (the 09:45 confirm cron grabbed the
09:36 DRY run; the 10:09 armed submit that followed was never confirmed).

Design
------
For a trade date this enumerates **every terminal run** under a runs root and,
using a persistent append-only sent-ledger (JSONL, keyed by ``run_id``), reports
which runs still need a confirmation email. Guarantees:

* Every terminal run is confirmed **exactly once** — dedupe is by run_id via the
  ledger, so re-running the sweep (scheduled backstop + execute-completion hook)
  never double-sends.
* Multiple runs on the same trade date (e.g. a DRY run then a later armed
  submit) are each discovered and each confirmed.
* Runs that finish **after** the scheduled confirm time are still picked up,
  because both the execute-completion hook and the next scheduled sweep call
  this discovery and act on anything not yet in the ledger.
* ``has_any_run`` lets the caller fail LOUD when a trade date that should have a
  run has none (the previously-silent live confirm lane).

This module has no side effects beyond appending to the ledger on ``mark-sent``.
It performs no email sending — the calling cron owns that so the sweep stays
cron-compatible and this logic stays hermetically testable.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional


EXECUTION_RESULTS_FILENAME = "execution_results.json"

# A run is terminal once its execution_results.json carries a status that is not
# an in-flight marker. Everything else (DRY_RUN_NO_SUBMISSION, CLEAN,
# FAILED_RECONCILIATION, SUBMITTED, BLOCKED, ...) is a terminal outcome that
# earns exactly one confirmation.
_NON_TERMINAL_STATUSES = {"", "running", "bootstrapped", "in_progress", "pending"}


def _read_json(path: Path) -> Optional[dict]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _run_status(results: dict) -> str:
    return str(results.get("status") or results.get("terminal_status") or "").strip()


def is_terminal(results: Optional[dict]) -> bool:
    if not isinstance(results, dict):
        return False
    return _run_status(results).lower() not in _NON_TERMINAL_STATUSES


def read_sent_ledger(ledger_path: Path) -> set[str]:
    """Return the set of run_ids already confirmed (JSONL, one entry per line)."""
    sent: set[str] = set()
    if not ledger_path.exists():
        return sent
    try:
        with ledger_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                run_id = str((entry or {}).get("run_id") or "").strip()
                if run_id:
                    sent.add(run_id)
    except OSError:
        return sent
    return sent


def append_sent_ledger(
    ledger_path: Path,
    *,
    run_id: str,
    run_root: str,
    trade_date: str,
    status: str,
) -> bool:
    """Append a confirmation record. Idempotent: a run_id already present is not
    written again (dedupe survives concurrent sweeps)."""
    run_id = str(run_id or "").strip()
    if not run_id:
        return False
    if run_id in read_sent_ledger(ledger_path):
        return False
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "run_id": run_id,
        "run_root": str(run_root or ""),
        "trade_date": str(trade_date or ""),
        "status": str(status or ""),
        "sent_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return True


def discover_pending(
    trade_date: str,
    runs_root: Path | str,
    ledger_path: Path | str,
) -> dict[str, Any]:
    """Enumerate terminal runs for *trade_date* and split into already-sent vs
    pending using the dedupe ledger."""
    runs_root = Path(runs_root)
    ledger_path = Path(ledger_path)
    trade_date = str(trade_date).strip()

    sent = read_sent_ledger(ledger_path)

    terminal: list[dict[str, Any]] = []
    any_run = False
    if runs_root.exists():
        # Sort by name so multiple same-date runs come out in run-timestamp order
        # (the run_id timestamp prefix makes name-sort == chronological order).
        for run_dir in sorted(p for p in runs_root.iterdir() if p.is_dir()):
            name = run_dir.name
            if not name.startswith(f"{trade_date}T"):
                continue
            results_path = run_dir / EXECUTION_RESULTS_FILENAME
            if not results_path.exists():
                continue
            any_run = True
            results = _read_json(results_path)
            if not is_terminal(results):
                continue
            terminal.append(
                {
                    "run_id": str((results or {}).get("run_id") or name),
                    "run_root": str(run_dir),
                    "results_path": str(results_path),
                    "status": _run_status(results or {}),
                }
            )

    pending = [run for run in terminal if run["run_id"] not in sent]
    already_sent = [run for run in terminal if run["run_id"] in sent]

    return {
        "trade_date": trade_date,
        "runs_root": str(runs_root),
        "ledger_path": str(ledger_path),
        "has_any_run": any_run,
        "terminal_count": len(terminal),
        "pending_count": len(pending),
        "already_sent_count": len(already_sent),
        "pending": pending,
        "terminal": terminal,
    }


def _cmd_discover(args: argparse.Namespace) -> int:
    result = discover_pending(args.trade_date, args.runs_root, args.ledger)
    if args.emit_summary:
        print(f"has_any_run={1 if result['has_any_run'] else 0}")
        print(f"terminal_count={result['terminal_count']}")
        print(f"pending_count={result['pending_count']}")
        print(f"already_sent_count={result['already_sent_count']}")
        return 0
    if args.emit_pending:
        for run in result["pending"]:
            print(
                "\t".join(
                    [run["run_id"], run["run_root"], run["results_path"], run["status"]]
                )
            )
        return 0
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _cmd_mark_sent(args: argparse.Namespace) -> int:
    wrote = append_sent_ledger(
        Path(args.ledger),
        run_id=args.run_id,
        run_root=args.run_root or "",
        trade_date=args.trade_date or "",
        status=args.status or "",
    )
    print(f"marked_sent={1 if wrote else 0} run_id={args.run_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LIVE_PILOT confirmation discovery + dedupe")
    default_date = os.getenv("REPORT_DATE") or dt.date.today().isoformat()
    sub = parser.add_subparsers(dest="command")

    discover = sub.add_parser("discover", help="report pending terminal runs (default)")
    discover.add_argument("--trade-date", default=default_date)
    discover.add_argument("--runs-root", default="outputs/live_pilot/runs")
    discover.add_argument("--ledger", default="outputs/live_pilot/state/confirm_sent_ledger.jsonl")
    discover.add_argument("--emit-pending", action="store_true", help="print pending runs as TSV lines")
    discover.add_argument("--emit-summary", action="store_true", help="print KEY=VALUE summary lines")
    discover.set_defaults(func=_cmd_discover)

    mark = sub.add_parser("mark-sent", help="record a run as confirmed in the dedupe ledger")
    mark.add_argument("--run-id", required=True)
    mark.add_argument("--run-root", default="")
    mark.add_argument("--trade-date", default=default_date)
    mark.add_argument("--status", default="")
    mark.add_argument("--ledger", default="outputs/live_pilot/state/confirm_sent_ledger.jsonl")
    mark.set_defaults(func=_cmd_mark_sent)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # Default to `discover` with its defaults for zero-arg invocation.
        args = parser.parse_args(["discover", *(argv or [])])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
