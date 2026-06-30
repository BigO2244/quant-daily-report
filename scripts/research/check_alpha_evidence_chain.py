#!/usr/bin/env python3
"""Build a reporting-only Alpha evidence-chain checklist from shadow artifacts."""
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
    build_alpha_evidence_chain_payload,
    write_alpha_evidence_chain_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check whether Polaris/Orion/Lyra alpha evidence-chain artifacts are collectible."
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--trade-date", default=None, help="YYYY-MM-DD. Defaults to latest dated shadow artifact.")
    parser.add_argument("--write", action="store_true", help="Write alpha_evidence_chain.json/md into the dated shadow folder.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 unless all daily evidence fields are collectible.")
    parser.add_argument("--no-latest-pointer", action="store_true", help="Do not assess latest/ pointer freshness; intended for historical backfill checks.")
    parser.add_argument("--all-five", action="store_true", help="Require Polaris, Orion, Lyra, Polaris_Alpha, and Orion_Alpha regardless of observation_start_date.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_root = Path(args.output_dir)
    assess_latest_pointer = not args.no_latest_pointer
    strategy_slugs = REQUIRED_ALPHA_EVIDENCE_SLUGS if args.all_five else None
    if args.write:
        payload = write_alpha_evidence_chain_artifacts(
            output_root=output_root,
            trade_date=args.trade_date,
            assess_latest_pointer=assess_latest_pointer,
            strategy_slugs=strategy_slugs,
        )
    else:
        payload = build_alpha_evidence_chain_payload(
            output_root=output_root,
            trade_date=args.trade_date,
            assess_latest_pointer=assess_latest_pointer,
            strategy_slugs=strategy_slugs,
        )
    print(
        json.dumps(
            {
                "status": payload["status"],
                "trade_date": payload.get("trade_date"),
                "reporting_status": payload.get("reporting_status"),
                "can_start_20_60_day_evidence_collection": payload.get("can_start_20_60_day_evidence_collection"),
                "blocked_reasons": payload.get("blocked_reasons"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if args.strict and not payload.get("can_start_20_60_day_evidence_collection") else 0


if __name__ == "__main__":
    raise SystemExit(main())
