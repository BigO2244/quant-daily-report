#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from brokers.alpaca_broker import AlpacaBroker
from core.security_master import (
    fetch_nasdaq_symbol_directories,
    update_security_master,
)


def _load_fixture(path: str | None) -> list[dict]:
    if not path:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        rows = payload.get("assets") or payload.get("records") or payload.get("symbols") or []
    else:
        rows = payload
    if not isinstance(rows, list):
        raise RuntimeError(f"fixture_rows_not_list:{path}")
    return [dict(row) for row in rows if isinstance(row, dict)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the local Caerus security master snapshot.")
    parser.add_argument("--asof-date", required=True)
    parser.add_argument("--root", default="data/security_master")
    parser.add_argument("--alpaca-fixture", help="Test/local fixture for Alpaca assets JSON.")
    parser.add_argument("--nasdaq-fixture", help="Test/local fixture for Nasdaq symbol directory JSON.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.alpaca_fixture:
        alpaca_assets = _load_fixture(args.alpaca_fixture)
    else:
        alpaca_assets = AlpacaBroker.from_env().list_assets(status="active", asset_class="us_equity")

    if args.nasdaq_fixture:
        nasdaq_records = _load_fixture(args.nasdaq_fixture)
    else:
        nasdaq_records = fetch_nasdaq_symbol_directories()

    if args.dry_run:
        print(
            json.dumps(
                {
                    "asof_date": args.asof_date,
                    "alpaca_assets": len(alpaca_assets),
                    "nasdaq_records": len(nasdaq_records),
                    "dry_run": True,
                },
                sort_keys=True,
            )
        )
        return 0

    result = update_security_master(
        asof_date=args.asof_date,
        alpaca_assets=alpaca_assets,
        nasdaq_records=nasdaq_records,
        root=Path(args.root),
    )
    pointer = result["pointer"]
    print(json.dumps(pointer, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
