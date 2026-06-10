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


def _run_events_stage(args: argparse.Namespace, repo_root: Path) -> int:
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
        indent=2, sort_keys=True,
    ))
    print("\n[CYGNUS] Stage 1 complete. Review the acceptance-timestamp audit before Stage 2.")
    return 0 if audit["look_ahead_safe"] else 2


def _run_backtest_stage(args: argparse.Namespace, repo_root: Path) -> int:
    """Stage 2 cygnus_v0_event_reaction backtest. Tune + validate ONLY; the
    2025+ holdout is excluded from all data and is never run here."""
    import pandas as pd

    from research.cygnus import backtest as B
    from research.cygnus.artifacts import cygnus_output_dir

    trade_date = args.trade_date or datetime.now(tz=timezone.utc).date().isoformat()
    if args.window == "holdout":
        print("[CYGNUS][REFUSED] holdout (2025+) is owner-gated; pause for review before any holdout run.",
              file=sys.stderr)
        return 3

    prices = B.load_price_matrix(repo_root)  # sliced < 2025-01-01
    events = load_event_tape_csv(repo_root, args.tape_date or trade_date)
    if not events:
        print("[CYGNUS][ERROR] no event tape found; run --stage events first.", file=sys.stderr)
        return 1

    panel = B.build_event_panel(events, prices, fundamentals_loader=B._fundamentals_loader(repo_root))
    # Expected events for coverage: ~4 quarterly per name over the window years.
    years = (B.VALIDATE_END.year - 2016 + 1)
    expected_total = 199 * 4 * years

    tables = []
    if args.window in ("tune", "both"):
        tables.append(B.a4_table(panel, prices, window_name="tune",
                                 start=pd.Timestamp("2016-01-01"), end=B.TUNE_END,
                                 expected_events=199 * 4 * (B.TUNE_END.year - 2016 + 1)))
    if args.window in ("validate", "both"):
        tables.append(B.a4_table(panel, prices, window_name="validate",
                                 start=B.VALIDATE_START, end=B.VALIDATE_END,
                                 expected_events=199 * 4 * (B.VALIDATE_END.year - B.VALIDATE_START.year + 1)))

    out_dir = cygnus_output_dir(repo_root, trade_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "caerus_cygnus_v0_backtest_v1",
        "strategy_id": "caerus_cygnus",
        "variant": "cygnus_v0_event_reaction",
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "panel_events": len(panel),
        "holdout_excluded": True,
        "a4_tables": tables,
    }
    report_path = out_dir / "cygnus_v0_backtest_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(_format_a4(tables))
    print(f"\n[CYGNUS] Stage 2 backtest written: {report_path}")
    print("[CYGNUS] Holdout (2025+) NOT run — pause for owner review of validation results first.")
    return 0


def load_event_tape_csv(repo_root: Path, tape_date: str) -> list:
    from research.cygnus.backtest import load_event_tape
    return load_event_tape(repo_root, tape_date)


def _format_a4(tables: list) -> str:
    lines = []
    for tbl in tables:
        lines.append("=" * 78)
        lines.append(f"A4 PASS/FAIL — {tbl['window'].upper()} {tbl['window_range']} "
                     f"(events={tbl['events_in_window']})  ->  {tbl['overall']} "
                     f"({tbl['criteria_passed']}/{tbl['criteria_total']})")
        lines.append("=" * 78)
        for name, c in tbl["criteria"].items():
            val = {k: v for k, v in c.items() if k not in ("verdict", "threshold")}
            lines.append(f"  [{c['verdict']:>11}] {name}")
            lines.append(f"               threshold: {c['threshold']}")
            lines.append(f"               actual:    {val}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FR-051 Cygnus research runner (Wave 1).")
    parser.add_argument("--stage", choices=["events", "backtest"], default="events",
                        help="'events' = Stage 1 tape; 'backtest' = Stage 2 cygnus_v0_event_reaction.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--trade-date", default=None, help="Artifact date folder; default today UTC.")
    parser.add_argument("--tape-date", default=None, help="Event-tape date folder to read for backtest.")
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--tickers", default=None, help="Comma-separated subset for a bounded run.")
    parser.add_argument("--limit", type=int, default=None, help="Cap number of universe tickers.")
    parser.add_argument("--window", choices=["tune", "validate", "both", "holdout"], default="both",
                        help="Backtest window. 'holdout' is refused (owner-gated).")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    if args.stage == "events":
        return _run_events_stage(args, repo_root)
    return _run_backtest_stage(args, repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
