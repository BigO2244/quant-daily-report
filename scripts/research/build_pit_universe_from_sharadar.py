#!/usr/bin/env python3
"""FR-068 Phase 1 — build the canonical PIT universe foundation from Sharadar.

Fetches SHARADAR/TICKERS metadata and writes canonical local artifacts under
`data/pit_universe/`:

  - security_master.csv     (identity, dates, category, relatedtickers, source)
  - symbol_history.csv      (security_id -> related/prior tickers)
  - security_events.csv     (DELISTING events for inactive names)
  - membership_universe.csv (sharadar_security_existence family rows)
  - manifest.json           (source, retrieved_at, row_count, sha256, filters)

This is the Phase 1 *foundation* only — security-existence PIT, not historical
index membership, and no strategy migration. RESEARCH_ONLY / NON_EXECUTIONAL.

Key handling: --api-key / --env-file / NASDAQ_DATA_LINK_API_KEY; the key is never
printed or written to any artifact. `--demo-fixture` builds from a small embedded
set of well-known securities with no network (for smoke/validation without a key);
`--dry-run` computes and reports without writing; `--limit` caps rows.

Usage:
    # canonical ingest (paid key)
    python3 scripts/research/build_pit_universe_from_sharadar.py --api-key "$NASDAQ_DATA_LINK_API_KEY"
    # no-network smoke seed
    python3 scripts/research/build_pit_universe_from_sharadar.py --demo-fixture
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.research.verify_sharadar_coverage import (  # noqa: E402  (key handling reuse)
    _load_env_file,
    _ndl_get,
    _rows_from_datatable,
    resolve_api_key,
)

SCHEMA_VERSION = "caerus_pit_universe_v1"
DEFAULT_OUTPUT_DIR = "data/pit_universe"
SECURITY_EXISTENCE_FAMILY = "sharadar_security_existence"
TICKERS_COLUMNS = (
    "permaticker,ticker,name,exchange,category,isdelisted,firstpricedate,"
    "lastpricedate,relatedtickers,currency,location,lastupdated"
)

SECURITY_MASTER_FIELDS = [
    "security_id", "permaticker", "ticker", "name", "exchange", "category",
    "isdelisted", "firstpricedate", "lastpricedate", "relatedtickers", "currency",
    "location", "source", "source_table", "lastupdated", "confidence",
]
SYMBOL_HISTORY_FIELDS = ["security_id", "permaticker", "ticker", "related_ticker", "source", "confidence"]
SECURITY_EVENTS_FIELDS = ["event_id", "security_id", "event_type", "effective_date", "source", "confidence"]
MEMBERSHIP_FIELDS = [
    "security_id", "ticker", "membership_family", "membership_start_date",
    "membership_end_date", "source", "confidence",
]

# Small embedded fixture of well-known securities for no-network smoke runs.
# Values are public/known; META carries FB in relatedtickers with a stable
# permaticker (Sharadar preserves permaticker across ticker changes).
DEMO_FIXTURE: list[dict[str, Any]] = [
    {"permaticker": "199059", "ticker": "AAPL", "name": "Apple Inc", "exchange": "NASDAQ",
     "category": "Domestic Common Stock", "isdelisted": "N", "firstpricedate": "1998-01-02",
     "lastpricedate": "2026-03-06", "relatedtickers": "", "currency": "USD", "location": "California; U.S.A", "lastupdated": "2026-03-06"},
    {"permaticker": "122827", "ticker": "MSFT", "name": "Microsoft Corp", "exchange": "NASDAQ",
     "category": "Domestic Common Stock", "isdelisted": "N", "firstpricedate": "1998-01-02",
     "lastpricedate": "2026-03-06", "relatedtickers": "", "currency": "USD", "location": "Washington; U.S.A", "lastupdated": "2026-03-06"},
    {"permaticker": "118692", "ticker": "META", "name": "Meta Platforms Inc", "exchange": "NASDAQ",
     "category": "Domestic Common Stock", "isdelisted": "N", "firstpricedate": "2012-05-18",
     "lastpricedate": "2026-03-06", "relatedtickers": "FB", "currency": "USD", "location": "California; U.S.A", "lastupdated": "2026-03-06"},
    {"permaticker": "200148", "ticker": "TWTR", "name": "Twitter Inc", "exchange": "NYSE",
     "category": "Domestic Common Stock", "isdelisted": "Y", "firstpricedate": "2013-11-07",
     "lastpricedate": "2022-10-27", "relatedtickers": "", "currency": "USD", "location": "California; U.S.A", "lastupdated": "2022-10-27"},
    {"permaticker": "100612", "ticker": "ATVI", "name": "Activision Blizzard Inc", "exchange": "NASDAQ",
     "category": "Domestic Common Stock", "isdelisted": "Y", "firstpricedate": "1993-10-25",
     "lastpricedate": "2023-10-13", "relatedtickers": "", "currency": "USD", "location": "California; U.S.A", "lastupdated": "2023-10-13"},
    {"permaticker": "104532", "ticker": "GYMB", "name": "Gymboree Corp", "exchange": "NASDAQ",
     "category": "Domestic Common Stock", "isdelisted": "Y", "firstpricedate": "1993-04-01",
     "lastpricedate": "2010-11-22", "relatedtickers": "", "currency": "USD", "location": "California; U.S.A", "lastupdated": "2010-11-22"},
]


def _is_common_stock(category: str) -> bool:
    return "common stock" in str(category or "").lower()


def map_ticker_rows(
    rows: list[dict[str, Any]], *, source: str, confidence: str, common_stock_only: bool = True
) -> dict[str, list[dict[str, Any]]]:
    """Pure mapping from raw SHARADAR/TICKERS rows to the 4 canonical tables."""
    master, symbols, events, membership = [], [], [], []
    for raw in rows:
        permaticker = str(raw.get("permaticker") or "").strip()
        ticker = str(raw.get("ticker") or "").strip().upper()
        category = str(raw.get("category") or "").strip()
        if not permaticker or not ticker:
            continue
        if common_stock_only and not _is_common_stock(category):
            continue
        security_id = f"SHARADAR:{permaticker}"
        isdelisted = str(raw.get("isdelisted") or "").strip().upper()
        active = isdelisted.startswith("N") or isdelisted == ""
        first = str(raw.get("firstpricedate") or "").strip()[:10]
        last = str(raw.get("lastpricedate") or "").strip()[:10]
        related = str(raw.get("relatedtickers") or "").strip()

        master.append({
            "security_id": security_id, "permaticker": permaticker, "ticker": ticker,
            "name": raw.get("name"), "exchange": raw.get("exchange"), "category": category,
            "isdelisted": "N" if active else "Y", "firstpricedate": first, "lastpricedate": last,
            "relatedtickers": related, "currency": raw.get("currency"), "location": raw.get("location"),
            "source": source, "source_table": "SHARADAR/TICKERS",
            "lastupdated": str(raw.get("lastupdated") or "")[:10], "confidence": confidence,
        })
        for rel in [r.strip().upper() for r in related.split() if r.strip()]:
            symbols.append({"security_id": security_id, "permaticker": permaticker, "ticker": ticker,
                            "related_ticker": rel, "source": source, "confidence": confidence})
        if not active and last:
            events.append({"event_id": f"DELIST:{security_id}", "security_id": security_id,
                           "event_type": "DELISTING", "effective_date": last,
                           "source": source, "confidence": confidence})
        membership.append({
            "security_id": security_id, "ticker": ticker,
            "membership_family": SECURITY_EXISTENCE_FAMILY,
            "membership_start_date": first, "membership_end_date": "" if active else last,
            "source": source, "confidence": confidence,
        })
    # Deterministic ordering.
    master.sort(key=lambda r: (r["security_id"], r["ticker"]))
    symbols.sort(key=lambda r: (r["security_id"], r["related_ticker"]))
    events.sort(key=lambda r: r["security_id"])
    membership.sort(key=lambda r: (r["security_id"], r["ticker"]))
    return {"security_master": master, "symbol_history": symbols,
            "security_events": events, "membership_universe": membership}


def fetch_all_tickers(api_key: str, *, get_fn: Callable[..., dict[str, Any]] | None = None,
                      limit: int | None = None) -> list[dict[str, Any]]:
    """Fetch SHARADAR/TICKERS with cursor pagination (datatables caps ~10k/call)."""
    getter = get_fn or (lambda table, params: _ndl_get(table, params, api_key=api_key))
    rows: list[dict[str, Any]] = []
    cursor = None
    for _ in range(100):  # safety cap
        params: dict[str, Any] = {"table": "SEP", "qopts.columns": TICKERS_COLUMNS}
        if cursor:
            params["qopts.cursor_id"] = cursor
        payload = getter("SHARADAR/TICKERS", params)
        rows.extend(_rows_from_datatable(payload))
        cursor = (payload.get("meta") or {}).get("next_cursor_id")
        if limit and len(rows) >= limit:
            rows = rows[:limit]
            break
        if not cursor:
            break
    return rows


_FIELD_MAP = {
    "security_master": SECURITY_MASTER_FIELDS, "symbol_history": SYMBOL_HISTORY_FIELDS,
    "security_events": SECURITY_EVENTS_FIELDS, "membership_universe": MEMBERSHIP_FIELDS,
}


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row.get(f) for f in fieldnames})
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_artifacts(tables: dict[str, list[dict[str, Any]]], *, output_dir: Path,
                    source: str, retrieved_at: str, filters: dict[str, Any], mode: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    hashes = {name: _write_csv(output_dir / f"{name}.csv", _FIELD_MAP[name], tables[name])
              for name in _FIELD_MAP}
    manifest = {
        "schema_version": SCHEMA_VERSION, "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL", "source": source, "source_table": "SHARADAR/TICKERS",
        "mode": mode, "retrieved_at": retrieved_at,
        "row_counts": {name: len(tables[name]) for name in _FIELD_MAP},
        "sha256": hashes, "filters": filters,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FR-068 Phase 1: build PIT universe from Sharadar TICKERS.")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=None, help="Cap rows (test/smoke).")
    parser.add_argument("--all-categories", action="store_true", help="Disable common-stock-only filter.")
    parser.add_argument("--demo-fixture", action="store_true", help="Build from embedded fixture (no network/key).")
    parser.add_argument("--dry-run", action="store_true", help="Compute + report; write nothing.")
    parser.add_argument("--retrieved-at", default=None, help="Override manifest timestamp (determinism).")
    args = parser.parse_args(argv)

    common_only = not args.all_categories
    retrieved_at = args.retrieved_at or datetime.now(tz=timezone.utc).isoformat()
    filters = {"common_stock_only": common_only, "limit": args.limit}

    if args.demo_fixture:
        raw_rows = DEMO_FIXTURE if not args.limit else DEMO_FIXTURE[: args.limit]
        source, confidence, mode = "sharadar_tickers_fixture_demo", "DEMO", "demo_fixture"
    else:
        if args.env_file:
            _load_env_file(args.env_file)
        api_key = resolve_api_key(args.api_key)
        if not api_key:
            print("[PIT_BUILD][REFUSED] No API key. Use --api-key/--env-file/NASDAQ_DATA_LINK_API_KEY, "
                  "or --demo-fixture for a no-network smoke. (Key never logged.)", file=sys.stderr)
            return 2
        raw_rows = fetch_all_tickers(api_key, limit=args.limit)
        source, confidence, mode = "sharadar_tickers", "HIGH", "live"

    tables = map_ticker_rows(raw_rows, source=source, confidence=confidence, common_stock_only=common_only)

    summary = {"mode": mode, "row_counts": {k: len(v) for k, v in tables.items()},
               "active": sum(1 for r in tables["security_master"] if r["isdelisted"] == "N"),
               "delisted": sum(1 for r in tables["security_master"] if r["isdelisted"] == "Y")}
    if args.dry_run:
        summary["dry_run"] = "no files written"
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    manifest = write_artifacts(tables, output_dir=Path(args.output_dir), source=source,
                               retrieved_at=retrieved_at, filters=filters, mode=mode)
    summary["output_dir"] = args.output_dir
    summary["sha256_security_master"] = manifest["sha256"]["security_master"]
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
