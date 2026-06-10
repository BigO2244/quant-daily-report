#!/usr/bin/env python3
"""FR-051 Cygnus research runner (RESEARCH_ONLY / NON_EXECUTIONAL).

Wave 1, Stage 1: build the EDGAR 8-K Item 2.02 earnings event tape over the
existing universe and emit the acceptance-timestamp audit for owner review.

Stage 2 (cygnus_v0_event_reaction) is gated on owner sign-off of the Stage 1
acceptance-timestamp audit and is added in a later commit.

This runner performs read-only public SEC EDGAR access only. It sends no orders
and changes no execution, cron, registry, allocation, or paper/live behavior.

Usage:
    # full universe tape (long network job, ~rate-limited over all CIKs)
    python3 -m scripts.research.run_cygnus_research --stage events \\
        --start 2016-01-01 --end 2026-06-10 --trade-date 2026-06-10

    # bounded sample for review
    python3 -m scripts.research.run_cygnus_research --stage events \\
        --tickers AAPL,MSFT,NVDA --start 2024-01-01 --end 2026-06-10
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.cygnus.artifacts import write_acceptance_audit, write_event_tape  # noqa: E402
from research.cygnus.events import (  # noqa: E402
    audit_acceptance_timestamps,
    build_event_tape,
    load_universe_ciks,
)


def _resolve_universe(repo_root: Path, tickers: str | None, limit: int | None) -> list[dict[str, str]]:
    universe = load_universe_ciks(repo_root)
    if tickers:
        wanted = {t.strip().upper() for t in tickers.split(",") if t.strip()}
        universe = [u for u in universe if u["ticker"] in wanted]
    if limit:
        universe = universe[:limit]
    return universe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FR-051 Cygnus research runner (Wave 1).")
    parser.add_argument("--stage", choices=["events"], default="events",
                        help="Wave 1 implements 'events' (Stage 1). Stage 2 is gated on audit sign-off.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--trade-date", default=None, help="Artifact date folder; default today UTC.")
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--tickers", default=None, help="Comma-separated subset for a bounded run.")
    parser.add_argument("--limit", type=int, default=None, help="Cap number of universe tickers.")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    now = datetime.now(tz=timezone.utc)
    trade_date = args.trade_date or now.date().isoformat()
    end_date = args.end or now.date().isoformat()
    ingested_at = now.isoformat()

    universe = _resolve_universe(repo_root, args.tickers, args.limit)
    if not universe:
        print("[CYGNUS][ERROR] no universe CIKs resolved (check cik_mapping_results.csv).", file=sys.stderr)
        return 1

    print(f"[CYGNUS] Stage 1 event tape — {len(universe)} tickers, {args.start}..{end_date}")
    events, fetch_errors = build_event_tape(
        universe, start_date=args.start, end_date=end_date, ingested_at=ingested_at
    )
    audit = audit_acceptance_timestamps(events)

    tape_paths = write_event_tape(events, repo_root=repo_root, trade_date=trade_date, fetch_errors=fetch_errors)
    audit_path = write_acceptance_audit(audit, repo_root=repo_root, trade_date=trade_date)

    print(json.dumps(
        {
            "events": len(events),
            "unique_tickers": audit["unique_tickers"],
            "announcement_time_distribution": audit["announcement_time_distribution"],
            "missing_timestamp_count": audit["missing_timestamp_count"],
            "look_ahead_safe": audit["look_ahead_safe"],
            "fetch_errors": len(fetch_errors),
            "artifacts": {**tape_paths, "audit": audit_path},
        },
        indent=2,
        sort_keys=True,
    ))
    print("\n[CYGNUS] Stage 1 complete. Review the acceptance-timestamp audit before Stage 2.")
    return 0 if audit["look_ahead_safe"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
