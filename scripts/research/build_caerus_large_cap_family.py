#!/usr/bin/env python3
"""FR-068 Phase 2.5 — build the caerus_large_cap PIT membership family — RESEARCH_ONLY.

Applies the transparent large-cap filters in research/pit_large_cap_family.py to the
PIT security master and writes a `caerus_large_cap` membership artifact. Requires a
scale source (Sharadar TICKERS `scalemarketcap`, or a DAILY numeric market-cap
snapshot); without one the family is BLOCKED and reason-coded — never silently
approximated, never falls back to data/universe.csv.

Scale sources (one required):
  --scalemarketcap-from-tickers   re-fetch SHARADAR/TICKERS with scalemarketcap (key)
  --scale-file FILE               CSV with columns ticker[,security_id],scalemarketcap
  --marketcap-file FILE           CSV with columns ticker[,security_id],marketcap

Key (only for --scalemarketcap-from-tickers): --api-key / --env-file /
NASDAQ_DATA_LINK_API_KEY; never printed.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.pit_large_cap_family import (  # noqa: E402
    DEFAULT_MIN_MARKETCAP,
    build_large_cap_membership,
    normalize_ticker,
)
from research.pit_polaris_rebaseline import load_security_master  # noqa: E402

DEFAULT_DATES = ["2014-01-02", "2016-01-04", "2018-01-02", "2020-01-02",
                 "2022-01-03", "2024-01-02", "2026-01-02"]
MEMBERSHIP_FIELDS = ["security_id", "ticker", "membership_family", "membership_start_date",
                     "membership_end_date", "scale_source", "source", "confidence"]


def _index_master(master: list[dict[str, Any]]) -> dict[str, str]:
    """normalized-ticker -> security_id."""
    out: dict[str, str] = {}
    for r in master:
        out.setdefault(normalize_ticker(r.get("ticker", "")), str(r.get("security_id")))
    return out


def _load_scale_file(path: Path, master: list[dict[str, Any]], column: str) -> dict[str, Any]:
    by_norm = _index_master(master)
    out: dict[str, Any] = {}
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            sid = str(row.get("security_id") or "").strip() or by_norm.get(normalize_ticker(row.get("ticker", "")))
            val = row.get(column)
            if sid and val not in (None, ""):
                out[sid] = float(val) if column == "marketcap" else str(val)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FR-068 Phase 2.5: build caerus_large_cap PIT membership family.")
    parser.add_argument("--data-dir", default="data/pit_universe")
    parser.add_argument("--output", default="data/pit_universe/membership_universe_large_cap.csv")
    parser.add_argument("--summary-out", default="outputs/research/pit_rebaseline/caerus_large_cap_family.json")
    parser.add_argument("--dates", default=",".join(DEFAULT_DATES))
    parser.add_argument("--min-marketcap", type=float, default=DEFAULT_MIN_MARKETCAP)
    parser.add_argument("--scale-file", default=None, help="CSV: ticker[,security_id],scalemarketcap")
    parser.add_argument("--marketcap-file", default=None, help="CSV: ticker[,security_id],marketcap")
    parser.add_argument("--scalemarketcap-from-tickers", action="store_true",
                        help="Re-fetch SHARADAR/TICKERS scalemarketcap (needs key).")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--env-file", default=None)
    args = parser.parse_args(argv)

    master = load_security_master(args.data_dir)
    dates = [d.strip() for d in args.dates.split(",") if d.strip()]

    scalemarketcap_by_id: dict[str, str] = {}
    marketcap_by_id: dict[str, float] = {}
    if args.scale_file:
        scalemarketcap_by_id = _load_scale_file(Path(args.scale_file), master, "scalemarketcap")
    elif args.marketcap_file:
        marketcap_by_id = _load_scale_file(Path(args.marketcap_file), master, "marketcap")
    elif args.scalemarketcap_from_tickers:
        from scripts.research.verify_sharadar_coverage import _load_env_file, _ndl_get, _rows_from_datatable, resolve_api_key
        if args.env_file:
            _load_env_file(args.env_file)
        key = resolve_api_key(args.api_key)
        if not key:
            print("[LARGE_CAP][REFUSED] --scalemarketcap-from-tickers needs an API key.", file=sys.stderr)
            return 2
        by_norm = _index_master(master)
        cursor = None
        for _ in range(100):
            params = {"table": "SEP", "qopts.columns": "ticker,scalemarketcap"}
            if cursor:
                params["qopts.cursor_id"] = cursor
            payload = _ndl_get("SHARADAR/TICKERS", params, api_key=key)
            for row in _rows_from_datatable(payload):
                sid = by_norm.get(normalize_ticker(row.get("ticker", "")))
                if sid and row.get("scalemarketcap"):
                    scalemarketcap_by_id[sid] = str(row["scalemarketcap"])
            cursor = (payload.get("meta") or {}).get("next_cursor_id")
            if not cursor:
                break

    result = build_large_cap_membership(
        master, dates, scalemarketcap_by_id=scalemarketcap_by_id or None,
        marketcap_by_id=marketcap_by_id or None, min_marketcap=args.min_marketcap,
    )

    if not result["blocked"]:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=MEMBERSHIP_FIELDS)
            writer.writeheader()
            for m in result["membership"]:
                writer.writerow({k: m.get(k) for k in MEMBERSHIP_FIELDS})

    summary = {
        "schema_version": "caerus_large_cap_family_v1", "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "family": result["family"], "blocked": result["blocked"],
        "block_reason": result["block_reason"], "scale_caveat": result["scale_caveat"],
        "membership_rows": len(result["membership"]), "by_date": result["by_date"],
        "output": None if result["blocked"] else args.output,
    }
    sp = Path(args.summary_out)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"blocked": result["blocked"], "block_reason": result["block_reason"],
                      "membership_rows": len(result["membership"]),
                      "by_date": result["by_date"]}, indent=2, sort_keys=True))
    return 0 if not result["blocked"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
