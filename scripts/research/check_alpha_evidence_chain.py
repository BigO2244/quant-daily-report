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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_root = Path(args.output_dir)
    if args.write:
        payload = write_alpha_evidence_chain_artifacts(output_root=output_root, trade_date=args.trade_date)
    else:
        payload = build_alpha_evidence_chain_payload(output_root=output_root, trade_date=args.trade_date)
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
