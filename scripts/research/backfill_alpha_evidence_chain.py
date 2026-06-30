#!/usr/bin/env python3
"""Backfill reporting-only alpha evidence-chain sidecars from retained artifacts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.shadow_tracking.evidence_chain import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    REQUIRED_ALPHA_EVIDENCE_SLUGS,
    write_alpha_evidence_chain_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill alpha_evidence_chain.json/md sidecars from existing shadow artifacts."
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--from-date", default=None)
    parser.add_argument("--to-date", default=None)
    parser.add_argument("--trade-date", action="append", default=None, help="Specific YYYY-MM-DD date to backfill; repeatable.")
    parser.add_argument(
        "--registry-active-only",
        action="store_true",
        help="Use registry-active sleeves for each date instead of the fixed five-sleeve alpha evidence set.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_root = Path(args.output_dir)
    dates = _selected_dates(
        output_root=output_root,
        requested=args.trade_date,
        from_date=args.from_date,
        to_date=args.to_date,
    )
    rows = []
    for trade_date in dates:
        payload = write_alpha_evidence_chain_artifacts(
            output_root=output_root,
            trade_date=trade_date,
            assess_latest_pointer=False,
            strategy_slugs=None if args.registry_active_only else REQUIRED_ALPHA_EVIDENCE_SLUGS,
            backfilled=True,
        )
        rows.append(
            {
                "trade_date": trade_date,
                "status": payload.get("status"),
                "can_start_20_60_day_evidence_collection": payload.get("can_start_20_60_day_evidence_collection"),
                "blocked_reasons": payload.get("blocked_reasons"),
                "strategies": {
                    row.get("strategy_id"): row.get("status")
                    for row in payload.get("strategies") or []
                },
            }
        )
    print(
        json.dumps(
            {
                "output_dir": str(output_root),
                "dates_processed": len(rows),
                "pass_dates": [row["trade_date"] for row in rows if row["can_start_20_60_day_evidence_collection"]],
                "blocked_dates": [row["trade_date"] for row in rows if not row["can_start_20_60_day_evidence_collection"]],
                "rows": rows,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _selected_dates(
    *,
    output_root: Path,
    requested: list[str] | None,
    from_date: str | None,
    to_date: str | None,
) -> list[str]:
    if requested:
        dates = sorted(set(requested))
    else:
        dates = sorted(
            child.name
            for child in output_root.iterdir()
            if child.is_dir() and _looks_like_date(child.name)
        )
    if from_date:
        dates = [date for date in dates if date >= from_date]
    if to_date:
        dates = [date for date in dates if date <= to_date]
    return dates


def _looks_like_date(value: str) -> bool:
    parts = value.split("-")
    return len(parts) == 3 and all(part.isdigit() for part in parts) and len(parts[0]) == 4


if __name__ == "__main__":
    raise SystemExit(main())
