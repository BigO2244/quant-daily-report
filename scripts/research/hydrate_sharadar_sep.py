#!/usr/bin/env python3
"""FR-068 Phase 2.5 — Sharadar SEP price hydration (incl. delisted) — RESEARCH_ONLY.

Pulls adjusted daily prices (SHARADAR/SEP) for a list of securities — including
delisted names — into a deterministic, gitignored research cache, with a manifest
and resumable, rate-limit-safe execution. This is the price layer the faithful
Polaris PIT priced rebaseline needs (the local matrix is current-names-only).

Key handling: --api-key / --env-file / NASDAQ_DATA_LINK_API_KEY; the key is never
printed or written. Network fetch is injected for tests. Refuses to run without a
key (no fabricated prices).

Cache: data/research_cache/sharadar_sep/<TICKER>.csv (data/research_cache/ is
gitignored). Resumable: already-cached tickers are skipped unless --refresh.

Usage (owner, with key):
    python3 scripts/research/hydrate_sharadar_sep.py --api-key "$NASDAQ_DATA_LINK_API_KEY" \
        --from-family data/pit_universe/membership_universe.csv --family caerus_large_cap
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.research.verify_sharadar_coverage import (  # noqa: E402
    _load_env_file,
    _ndl_get,
    _rows_from_datatable,
    resolve_api_key,
)

SCHEMA_VERSION = "caerus_sharadar_sep_hydration_v1"
DEFAULT_CACHE_DIR = "data/research_cache/sharadar_sep"
SEP_COLUMNS = "ticker,date,closeadj,close"
EDGAR_SLEEP_S = 0.34  # ~3 req/s, conservative under the API rate limit


# --------------------------------------------------------------------------- #
# Pure helpers (network-free; unit-tested)
# --------------------------------------------------------------------------- #
def sep_rows_to_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sorted, de-duplicated (date, closeadj, close) series from raw SEP rows."""
    by_date: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = str(r.get("date") or "")[:10]
        if len(d) != 10:
            continue
        by_date[d] = {"date": d, "closeadj": r.get("closeadj"), "close": r.get("close")}
    return [by_date[d] for d in sorted(by_date)]


def coverage_through(series: list[dict[str, Any]], last_price_date: str, *, tolerance_days: int = 7) -> bool:
    """True if the series extends to within tolerance of the declared last date."""
    from datetime import date, timedelta

    if not series:
        return False
    try:
        obs_last = date.fromisoformat(series[-1]["date"])
        declared = date.fromisoformat(str(last_price_date)[:10])
    except (ValueError, KeyError):
        return False
    return obs_last >= declared - timedelta(days=tolerance_days)


def load_tickers_from_family(path: Path, family: str | None = None) -> list[str]:
    out: list[str] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if family and str(row.get("membership_family")) != family:
                continue
            tk = str(row.get("ticker") or "").strip().upper()
            if tk:
                out.append(tk)
    return sorted(set(out))


def _write_series_csv(path: Path, series: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "closeadj", "close"])
        writer.writeheader()
        for row in series:
            writer.writerow({k: row.get(k) for k in ("date", "closeadj", "close")})
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# Fetch (injected in tests)
# --------------------------------------------------------------------------- #
def fetch_sep_series(ticker: str, api_key: str, *, get_fn: Callable[..., dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    getter = get_fn or (lambda table, params: _ndl_get(table, params, api_key=api_key))
    rows: list[dict[str, Any]] = []
    cursor = None
    for _ in range(200):  # safety cap
        params: dict[str, Any] = {"ticker": ticker, "qopts.columns": SEP_COLUMNS}
        if cursor:
            params["qopts.cursor_id"] = cursor
        payload = getter("SHARADAR/SEP", params)
        rows.extend(_rows_from_datatable(payload))
        cursor = (payload.get("meta") or {}).get("next_cursor_id")
        if not cursor:
            break
    return rows


def hydrate(
    tickers: list[str],
    *,
    api_key: str,
    cache_dir: Path,
    resume: bool = True,
    sleep_s: float = EDGAR_SLEEP_S,
    get_fn: Callable[..., dict[str, Any]] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Hydrate SEP series for `tickers` into the cache. Returns a manifest dict."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    retrieved_at = retrieved_at or datetime.now(tz=timezone.utc).isoformat()
    per_ticker: dict[str, dict[str, Any]] = {}
    hydrated, skipped, empty = 0, 0, 0
    for tk in tickers:
        path = cache_dir / f"{tk.replace('/', '_')}.csv"
        if resume and path.exists():
            skipped += 1
            continue
        series = sep_rows_to_series(fetch_sep_series(tk, api_key, get_fn=get_fn))
        if not series:
            empty += 1
            per_ticker[tk] = {"rows": 0, "reason": "no_sep_rows_returned"}
        else:
            sha = _write_series_csv(path, series)
            hydrated += 1
            per_ticker[tk] = {"rows": len(series), "first": series[0]["date"],
                              "last": series[-1]["date"], "sha256": sha}
        if get_fn is None and sleep_s:
            sleep_fn(sleep_s)

    all_dates = [v["first"] for v in per_ticker.values() if "first" in v] + \
                [v["last"] for v in per_ticker.values() if "last" in v]
    manifest = {
        "schema_version": SCHEMA_VERSION, "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL", "source_table": "SHARADAR/SEP",
        "retrieved_at": retrieved_at, "requested": len(tickers),
        "hydrated": hydrated, "skipped_existing": skipped, "empty": empty,
        "date_range": [min(all_dates), max(all_dates)] if all_dates else [None, None],
        "per_ticker": per_ticker,
    }
    (cache_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FR-068 Phase 2.5: hydrate Sharadar SEP prices (incl. delisted).")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--tickers", default=None, help="Comma-separated tickers.")
    parser.add_argument("--from-family", default=None, help="membership_universe.csv to read tickers from.")
    parser.add_argument("--family", default=None, help="Filter membership rows to this family.")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    parser.add_argument("--no-resume", action="store_true", help="Re-fetch even if cached.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=EDGAR_SLEEP_S)
    args = parser.parse_args(argv)

    if args.env_file:
        _load_env_file(args.env_file)
    api_key = resolve_api_key(args.api_key)
    if not api_key:
        print("[SEP_HYDRATE][REFUSED] No API key. Use --api-key/--env-file/NASDAQ_DATA_LINK_API_KEY. "
              "(Key never logged.)", file=sys.stderr)
        return 2

    tickers: list[str] = []
    if args.tickers:
        tickers += [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if args.from_family:
        tickers += load_tickers_from_family(Path(args.from_family), args.family)
    tickers = sorted(set(tickers))
    if args.limit:
        tickers = tickers[: args.limit]
    if not tickers:
        print("[SEP_HYDRATE][ERROR] no tickers (use --tickers or --from-family).", file=sys.stderr)
        return 1

    manifest = hydrate(tickers, api_key=api_key, cache_dir=Path(args.cache_dir),
                       resume=not args.no_resume, sleep_s=args.sleep)
    print(json.dumps({k: manifest[k] for k in
                      ("requested", "hydrated", "skipped_existing", "empty", "date_range")},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
