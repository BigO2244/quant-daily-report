#!/usr/bin/env python3
"""FR-067 Vela Stage 0 — Sharadar delisted small-cap price-coverage verifier.

Trial-period diligence before any annual Sharadar license commitment. Samples
delisted small-cap tickers and measures what fraction have COMPLETE adjusted
price history through their delisting date — the dimension that yfinance fails
and that decides whether Sharadar can anchor the Vela PIT universe.

RESEARCH_ONLY / NON_EXECUTIONAL. Read-only data-vendor access; no orders, no
registry, no execution, no strategy behavior.

Data source: Nasdaq Data Link (Sharadar). Tables used:
  - SHARADAR/TICKERS : ticker, isdelisted, firstpricedate, lastpricedate,
                       scalemarketcap, category  (universe + delist date)
  - SHARADAR/SEP     : ticker, date, closeadj                  (price history)

Membership note (Stage 0 finding): Sharadar does NOT carry S&P SmallCap 600
constituent history. "Left the S&P 600 via delisting" is therefore approximated
by Sharadar's small-cap scale categories (Micro/Small) with a last price date in
the requested window, UNLESS an explicit membership file is supplied via
--membership-file (one ticker per line). The approximation is recorded in the
report.

Key handling: the API key is read from --api-key, --env-file, or the env vars
NASDAQ_DATA_LINK_API_KEY / QUANDL_API_KEY. It is never printed or written to the
report. The script REFUSES to run without a key. Run only after the owner
supplies the trial key.

Usage (after the owner supplies the trial key):
    python3 scripts/research/verify_sharadar_coverage.py --env-file .env.sharadar
    python3 scripts/research/verify_sharadar_coverage.py --list-only   # preview sample, no price fetch
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from paper.trading_calendar import is_trading_day, next_trading_day
except Exception:  # pragma: no cover - defensive
    is_trading_day = None  # type: ignore[assignment]
    next_trading_day = None  # type: ignore[assignment]

SCHEMA_VERSION = "caerus_vela_sharadar_coverage_v1"
NDL_BASE = "https://data.nasdaq.com/api/v3/datatables"
SMALL_CAP_SCALES = {"2 - Micro", "3 - Small"}  # Sharadar scalemarketcap proxy for small-cap
DEFAULT_SAMPLE_SIZE = 50
DEFAULT_COMPLETE_THRESHOLD = 0.95
DEFAULT_OUTPUT = "outputs/research/vela/sharadar_coverage_report.json"


# --------------------------------------------------------------------------- #
# Key handling (never logged)
# --------------------------------------------------------------------------- #
def _load_env_file(path: str | None) -> None:
    if not path:
        return
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"env file not found: {p}")
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        os.environ[k.strip()] = v


def resolve_api_key(cli_key: str | None) -> str | None:
    return cli_key or os.environ.get("NASDAQ_DATA_LINK_API_KEY") or os.environ.get("QUANDL_API_KEY")


# --------------------------------------------------------------------------- #
# REST access (injected in tests)
# --------------------------------------------------------------------------- #
def _ndl_get(table: str, params: dict[str, Any], *, api_key: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({**{k: v for k, v in params.items() if v not in (None, "")},
                                    "api_key": api_key})
    url = f"{NDL_BASE}/{table}.json?{query}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=60) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _rows_from_datatable(payload: dict[str, Any]) -> list[dict[str, Any]]:
    dt = payload.get("datatable") or {}
    cols = [c.get("name") for c in (dt.get("columns") or [])]
    return [dict(zip(cols, row)) for row in (dt.get("data") or [])]


# --------------------------------------------------------------------------- #
# Sample selection
# --------------------------------------------------------------------------- #
def select_delisted_small_caps(
    tickers_rows: list[dict[str, Any]],
    *,
    start_year: int,
    end_year: int,
    sample_size: int,
    membership: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Pick delisted small-cap names whose last price date falls in the window."""
    picked: list[dict[str, Any]] = []
    for row in tickers_rows:
        if str(row.get("isdelisted") or "").upper() not in ("Y", "YES", "TRUE"):
            continue
        last = str(row.get("lastpricedate") or "")[:10]
        if len(last) != 10:
            continue
        year = int(last[:4])
        if not (start_year <= year <= end_year):
            continue
        if membership is not None:
            if str(row.get("ticker") or "").upper() not in membership:
                continue
        elif str(row.get("scalemarketcap") or "") not in SMALL_CAP_SCALES:
            continue
        picked.append(row)
    picked.sort(key=lambda r: str(r.get("lastpricedate")))
    # Evenly sample across the window to avoid clustering in one era.
    if len(picked) <= sample_size:
        return picked
    step = len(picked) / sample_size
    return [picked[int(i * step)] for i in range(sample_size)]


# --------------------------------------------------------------------------- #
# Coverage computation (pure; unit-tested)
# --------------------------------------------------------------------------- #
def expected_trading_days(first: str, last: str) -> int | None:
    if is_trading_day is None or next_trading_day is None or first > last:
        return None
    count = 1 if is_trading_day(first) else 0
    cursor = first
    for _ in range(20000):
        cursor = next_trading_day(cursor)
        if cursor > last:
            break
        count += 1
    return count


def assess_ticker_coverage(
    *,
    ticker: str,
    price_dates: list[str],
    first_price_date: str,
    last_price_date: str,
    complete_threshold: float = DEFAULT_COMPLETE_THRESHOLD,
    delist_gap_tolerance_td: int = 5,
) -> dict[str, Any]:
    """Coverage of a delisted ticker's price history through its delist date."""
    dates = sorted({d[:10] for d in price_dates if d})
    n_days = len(dates)
    expected = expected_trading_days(first_price_date, last_price_date)
    coverage = (n_days / expected) if expected else None
    actual_last = dates[-1] if dates else None
    # Does coverage extend to (near) the delisting date?
    reaches_delist = False
    if actual_last and last_price_date:
        gap = expected_trading_days(actual_last, last_price_date)
        reaches_delist = gap is not None and gap <= delist_gap_tolerance_td
    complete = bool(coverage is not None and coverage >= complete_threshold and reaches_delist)
    return {
        "ticker": ticker,
        "price_day_count": n_days,
        "first_price_date": first_price_date,
        "last_price_date": last_price_date,
        "actual_last_price_date": actual_last,
        "expected_trading_days": expected,
        "coverage_pct": round(coverage, 4) if coverage is not None else None,
        "reaches_delist_date": reaches_delist,
        "complete": complete,
    }


def summarize(results: list[dict[str, Any]], *, complete_threshold: float) -> dict[str, Any]:
    n = len(results)
    complete = sum(1 for r in results if r.get("complete"))
    reaches = sum(1 for r in results if r.get("reaches_delist_date"))
    covs = [r["coverage_pct"] for r in results if r.get("coverage_pct") is not None]
    return {
        "sampled": n,
        "complete_count": complete,
        "complete_pct": round(complete / n, 4) if n else None,
        "reaches_delist_count": reaches,
        "median_coverage_pct": round(sorted(covs)[len(covs) // 2], 4) if covs else None,
        "complete_threshold": complete_threshold,
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_verification(
    *,
    api_key: str,
    repo_root: Path,
    start_year: int,
    end_year: int,
    sample_size: int,
    complete_threshold: float,
    membership: set[str] | None,
    list_only: bool,
    get_fn: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    def get(table: str, params: dict[str, Any]) -> dict[str, Any]:
        if get_fn is not None:
            return get_fn(table, params)
        return _ndl_get(table, params, api_key=api_key)

    tickers_payload = get("SHARADAR/TICKERS", {"table": "SEP", "qopts.columns":
                                               "ticker,isdelisted,firstpricedate,lastpricedate,scalemarketcap,category"})
    tickers_rows = _rows_from_datatable(tickers_payload)
    sample = select_delisted_small_caps(
        tickers_rows, start_year=start_year, end_year=end_year,
        sample_size=sample_size, membership=membership,
    )

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "purpose": "Sharadar trial verification of delisted small-cap price coverage (FR-067 Stage 0).",
        "window": {"start_year": start_year, "end_year": end_year},
        "membership_source": "explicit_file" if membership is not None
        else "sharadar_smallcap_scale_proxy (Sharadar lacks S&P 600 membership)",
        "sample_size_requested": sample_size,
        "sample_size_resolved": len(sample),
        "sample_tickers": [r.get("ticker") for r in sample],
    }
    if list_only:
        report["mode"] = "list_only"
        return report

    results: list[dict[str, Any]] = []
    for row in sample:
        ticker = str(row.get("ticker") or "")
        sep = get("SHARADAR/SEP", {"ticker": ticker, "qopts.columns": "ticker,date,closeadj"})
        price_rows = _rows_from_datatable(sep)
        results.append(
            assess_ticker_coverage(
                ticker=ticker,
                price_dates=[str(r.get("date")) for r in price_rows],
                first_price_date=str(row.get("firstpricedate") or "")[:10],
                last_price_date=str(row.get("lastpricedate") or "")[:10],
                complete_threshold=complete_threshold,
            )
        )
    report["mode"] = "full"
    report["summary"] = summarize(results, complete_threshold=complete_threshold)
    report["per_ticker"] = results
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FR-067 Sharadar delisted small-cap coverage verifier.")
    parser.add_argument("--api-key", default=None, help="Sharadar/Nasdaq Data Link key (or use --env-file/env).")
    parser.add_argument("--env-file", default=None, help="Env file holding NASDAQ_DATA_LINK_API_KEY.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--complete-threshold", type=float, default=DEFAULT_COMPLETE_THRESHOLD)
    parser.add_argument("--membership-file", default=None, help="Optional: one delisted S&P 600 ticker per line.")
    parser.add_argument("--list-only", action="store_true", help="Resolve + print the sample without fetching prices.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    if args.env_file:
        _load_env_file(args.env_file)
    api_key = resolve_api_key(args.api_key)
    if not api_key:
        print("[SHARADAR][REFUSED] No API key. Supply --api-key, --env-file, or "
              "NASDAQ_DATA_LINK_API_KEY. (Key is never logged.)", file=sys.stderr)
        return 2

    membership: set[str] | None = None
    if args.membership_file:
        membership = {
            line.strip().upper()
            for line in Path(args.membership_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }

    repo_root = Path(args.repo_root).resolve()
    report = run_verification(
        api_key=api_key, repo_root=repo_root, start_year=args.start_year, end_year=args.end_year,
        sample_size=args.sample_size, complete_threshold=args.complete_threshold,
        membership=membership, list_only=args.list_only,
    )

    out_path = repo_root / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if report.get("mode") == "full":
        s = report["summary"]
        print(f"[SHARADAR] sampled {s['sampled']} delisted small-caps {args.start_year}-{args.end_year}: "
              f"{s['complete_count']} complete ({s['complete_pct']}), median coverage {s['median_coverage_pct']}")
    else:
        print(f"[SHARADAR] list-only: {report['sample_size_resolved']} tickers -> {out_path}")
    print(f"[SHARADAR] report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
