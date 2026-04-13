#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure repo root is on sys.path when invoked as `python scripts/execute_options_overlay.py`
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from brokers.alpaca_broker import AlpacaBroker
from core.options_execution import write_options_execution_review


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or submit the options overlay execution review.")
    parser.add_argument("--run-root", default=".")
    parser.add_argument("--output-dir", default="outputs/options_execution")
    parser.add_argument(
        "--paper-review",
        default="outputs/options_overlay_paper/options_overlay_paper_review_latest.json",
    )
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--asof-date", default=None)
    parser.add_argument("--submit", action="store_true", help="Actually submit the live options order.")
    args = parser.parse_args(argv)

    paper_review = _load_json(Path(args.paper_review))
    broker = AlpacaBroker.from_env() if args.submit else None
    review = write_options_execution_review(
        run_root=args.run_root,
        output_dir=args.output_dir,
        trade_date=args.trade_date,
        asof_date=args.asof_date,
        paper_review=paper_review,
        allow_live_submission=bool(args.submit),
        broker=broker,
    )
    print(json.dumps(review, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
