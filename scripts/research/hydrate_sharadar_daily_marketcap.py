#!/usr/bin/env python3
"""Hydrate SHARADAR/DAILY market cap for the PIT security master.

Research-only.  Writes a resumable per-ticker cache under
`data/research_cache/sharadar_daily_marketcap/`.  The API key is read from
`--api-key`, `--env-file`, `NASDAQ_DATA_LINK_API_KEY`, or `QUANDL_API_KEY`; it is
never printed or written.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.pit_large_cap_family import normalize_ticker  # noqa: E402
from scripts.research.verify_sharadar_coverage import (  # noqa: E402
    _load_env_file,
    _ndl_get,
    _rows_from_datatable,
    resolve_api_key,
)

SCHEMA_VERSION = "caerus_sharadar_daily_marketcap_hydration_v1"
DEFAULT_CACHE_DIR = "data/research_cache/sharadar_daily_marketcap"
DEFAULT_SECURITY_MASTER = "data/pit_universe/security_master.csv"
DAILY_COLUMNS = "ticker,date,marketcap"
SLEEP_S = 0.34


def _safe_ticker_file(ticker: str) -> str:
    return f"{ticker.replace('/', '_')}.csv"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_security_master_tickers(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            ticker = normalize_ticker(row.get("ticker", ""))
            security_id = str(row.get("security_id") or "").strip()
            if ticker and security_id:
                rows.append({"ticker": ticker, "security_id": security_id})
    rows.sort(key=lambda row: (row["ticker"], row["security_id"]))
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        key = (row["ticker"], row["security_id"])
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def select_tickers_from_master(path: Path, tickers: list[str]) -> list[dict[str, str]]:
    wanted = {normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)}
    rows = load_security_master_tickers(path)
    by_ticker: dict[str, dict[str, str]] = {}
    for row in rows:
        by_ticker.setdefault(row["ticker"], row)
    return [by_ticker.get(ticker, {"ticker": ticker, "security_id": ""}) for ticker in sorted(wanted)]


def daily_rows_to_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    for row in rows:
        date = str(row.get("date") or "")[:10]
        if len(date) != 10:
            continue
        marketcap = row.get("marketcap")
        if marketcap in (None, ""):
            continue
        by_date[date] = {"date": date, "marketcap": marketcap}
    return [by_date[d] for d in sorted(by_date)]


def fetch_daily_marketcap(
    ticker: str,
    api_key: str,
    *,
    get_fn: Callable[..., dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    getter = get_fn or (lambda table, params: _ndl_get(table, params, api_key=api_key))
    rows: list[dict[str, Any]] = []
    cursor = None
    for _ in range(300):
        params: dict[str, Any] = {"ticker": ticker, "qopts.columns": DAILY_COLUMNS}
        if cursor:
            params["qopts.cursor_id"] = cursor
        payload = getter("SHARADAR/DAILY", params)
        rows.extend(_rows_from_datatable(payload))
        cursor = (payload.get("meta") or {}).get("next_cursor_id")
        if not cursor:
            break
    return rows


def _write_series(path: Path, series: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "marketcap"])
        writer.writeheader()
        for row in series:
            writer.writerow({"date": row.get("date"), "marketcap": row.get("marketcap")})
    return _sha256(path)


def hydrate_daily_marketcap(
    securities: list[dict[str, str]],
    *,
    api_key: str,
    cache_dir: Path,
    resume: bool = True,
    sleep_s: float = SLEEP_S,
    get_fn: Callable[..., dict[str, Any]] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    retrieved_at = retrieved_at or datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
    per_ticker: dict[str, dict[str, Any]] = {}
    hydrated = skipped = empty = failed = 0

    for row in securities:
        ticker = row["ticker"]
        path = cache_dir / _safe_ticker_file(ticker)
        if resume and path.exists():
            skipped += 1
            with path.open("r", encoding="utf-8", newline="") as handle:
                series = list(csv.DictReader(handle))
            dates = [str(item.get("date") or "") for item in series if item.get("date")]
            per_ticker[ticker] = {
                "status": "skipped_existing",
                "security_id": row["security_id"],
                "rows": len(series),
                "first": min(dates) if dates else None,
                "last": max(dates) if dates else None,
                "sha256": _sha256(path),
            }
            continue
        try:
            series = daily_rows_to_series(fetch_daily_marketcap(ticker, api_key, get_fn=get_fn))
        except Exception as exc:
            failed += 1
            per_ticker[ticker] = {
                "status": "failed",
                "security_id": row["security_id"],
                "rows": 0,
                "reason": type(exc).__name__,
            }
            continue
        if not series:
            empty += 1
            per_ticker[ticker] = {
                "status": "empty",
                "security_id": row["security_id"],
                "rows": 0,
                "reason": "no_daily_marketcap_rows_returned",
            }
        else:
            sha = _write_series(path, series)
            hydrated += 1
            per_ticker[ticker] = {
                "status": "hydrated",
                "security_id": row["security_id"],
                "rows": len(series),
                "first": series[0]["date"],
                "last": series[-1]["date"],
                "sha256": sha,
            }
        if get_fn is None and sleep_s:
            sleep_fn(sleep_s)

    row_counts = {ticker: int(stats.get("rows") or 0) for ticker, stats in per_ticker.items()}
    all_first = [stats["first"] for stats in per_ticker.values() if stats.get("first")]
    all_last = [stats["last"] for stats in per_ticker.values() if stats.get("last")]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "source_table": "SHARADAR/DAILY",
        "requested_columns": ["ticker", "date", "marketcap"],
        "retrieved_at": retrieved_at,
        "security_count": len(securities),
        "hydrated": hydrated,
        "skipped_existing": skipped,
        "empty": empty,
        "failed": failed,
        "total_rows": sum(row_counts.values()),
        "date_range": [min(all_first), max(all_last)] if all_first and all_last else [None, None],
        "failed_tickers": [ticker for ticker, stats in per_ticker.items() if stats.get("status") == "failed"],
        "empty_tickers": [ticker for ticker, stats in per_ticker.items() if stats.get("status") == "empty"],
        "row_counts": row_counts,
        "per_ticker": per_ticker,
    }
    (cache_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hydrate SHARADAR/DAILY marketcap for PIT security master.")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--security-master", default=DEFAULT_SECURITY_MASTER)
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    parser.add_argument("--tickers", default=None, help="Comma-separated ticker smoke subset.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--sleep", type=float, default=SLEEP_S)
    args = parser.parse_args(argv)

    if args.env_file:
        _load_env_file(args.env_file)
    api_key = resolve_api_key(args.api_key)
    if not api_key:
        print(
            "[DAILY_MARKETCAP_HYDRATE][REFUSED] No API key. Use --api-key, --env-file, "
            "NASDAQ_DATA_LINK_API_KEY, or QUANDL_API_KEY. (Key never logged.)",
            file=sys.stderr,
        )
        return 2

    if args.tickers:
        securities = select_tickers_from_master(
            Path(args.security_master),
            [ticker.strip() for ticker in args.tickers.split(",") if ticker.strip()],
        )
    else:
        securities = load_security_master_tickers(Path(args.security_master))
    if args.limit:
        securities = securities[: args.limit]
    manifest = hydrate_daily_marketcap(
        securities,
        api_key=api_key,
        cache_dir=Path(args.cache_dir),
        resume=not args.no_resume,
        sleep_s=args.sleep,
    )
    status = "OK" if manifest["total_rows"] else "NO_ROWS"
    print(json.dumps({
        "status": status,
        "security_count": manifest["security_count"],
        "hydrated": manifest["hydrated"],
        "skipped_existing": manifest["skipped_existing"],
        "empty": manifest["empty"],
        "failed": manifest["failed"],
        "total_rows": manifest["total_rows"],
        "date_range": manifest["date_range"],
        "cache_dir": args.cache_dir,
    }, indent=2, sort_keys=True))
    if manifest["failed"]:
        return 3
    return 0 if manifest["total_rows"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
