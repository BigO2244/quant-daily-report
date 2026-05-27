"""Read-only price cache coverage diagnostics.

This is an advisory FR-002 sidecar preview. It reads the existing parquet cache
and universe metadata, but never hydrates prices or writes a sidecar file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from research.flow_detection.data import load_ticker_exceptions, load_universe, standardize_panel


DEFAULT_CACHE_PATH = Path("outputs/research/flow_detection_v1/price_panel.parquet")


def _parse_date(value: Any) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    try:
        return pd.Timestamp(value).normalize()
    except Exception:
        return None


def _coverage_status(cache_exists: bool, cache_max_date: str | None, trade_date: str | None, missing: int, stale: int) -> str:
    if not cache_exists:
        return "MISSING"
    if cache_max_date is None:
        return "UNKNOWN"
    if missing or stale:
        return "INCOMPLETE"
    if trade_date and str(cache_max_date) < str(trade_date):
        return "STALE"
    return "READY"


def inspect_price_cache_coverage(
    *,
    repo_root: Path,
    cache_path: Path | None = None,
    universe_path: Path | None = None,
    ticker_exceptions_path: Path | None = None,
    trade_date: str | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo_root)
    cache = cache_path or DEFAULT_CACHE_PATH
    if not cache.is_absolute():
        cache = repo_root / cache
    universe = universe_path or Path("data/universe.csv")
    if not universe.is_absolute():
        universe = repo_root / universe
    exceptions_path = ticker_exceptions_path or Path("data/ticker_exceptions.json")
    if not exceptions_path.is_absolute():
        exceptions_path = repo_root / exceptions_path

    try:
        requested_symbols = set(load_universe(universe))
    except Exception as exc:
        return {
            "coverage_status": "UNKNOWN",
            "cache_path": str(cache),
            "cache_exists": cache.exists(),
            "trade_date": trade_date,
            "error": f"unable_to_load_universe:{exc}",
            "runtime_effect": "none",
            "sidecar_status": "advisory_preview_only",
        }

    exceptions = load_ticker_exceptions(exceptions_path)
    ignored = set(exceptions.get("ignore") or [])
    aliases = dict(exceptions.get("aliases") or {})
    active_symbols = sorted(requested_symbols - ignored)

    if not cache.exists():
        return {
            "coverage_status": "MISSING",
            "cache_path": str(cache),
            "cache_exists": False,
            "trade_date": trade_date,
            "expected_symbols": len(active_symbols),
            "symbols_present": 0,
            "symbols_missing_count": len(active_symbols),
            "symbols_missing_sample": active_symbols[:20],
            "stale_symbols_count": None,
            "cache_min_date": None,
            "cache_max_date": None,
            "row_count": 0,
            "ignored_tickers": sorted(ignored),
            "aliased_tickers": aliases,
            "runtime_effect": "none",
            "sidecar_status": "advisory_preview_only",
            "recommended_next_action": "Run the approved hydration workflow; this diagnostic does not hydrate prices.",
        }

    try:
        panel = standardize_panel(pd.read_parquet(cache))
    except Exception as exc:
        return {
            "coverage_status": "UNKNOWN",
            "cache_path": str(cache),
            "cache_exists": True,
            "trade_date": trade_date,
            "error": f"unable_to_read_cache:{exc}",
            "runtime_effect": "none",
            "sidecar_status": "advisory_preview_only",
        }

    if panel.empty:
        present_symbols: set[str] = set()
        max_by_symbol: dict[str, pd.Timestamp] = {}
        cache_min_date = None
        cache_max_date = None
    else:
        present_symbols = {str(symbol).upper() for symbol in panel["ticker"].dropna().unique()}
        max_by_symbol = {
            str(symbol).upper(): pd.Timestamp(max_date).normalize()
            for symbol, max_date in panel.groupby("ticker")["date"].max().items()
        }
        cache_min_date = str(pd.Timestamp(panel["date"].min()).date())
        cache_max_date = str(pd.Timestamp(panel["date"].max()).date())

    missing_symbols = sorted(set(active_symbols) - present_symbols)
    target_date = _parse_date(trade_date)
    stale_symbols = (
        sorted(symbol for symbol in active_symbols if symbol in max_by_symbol and target_date and max_by_symbol[symbol] < target_date)
        if target_date is not None
        else []
    )
    status = _coverage_status(
        cache_exists=True,
        cache_max_date=cache_max_date,
        trade_date=trade_date,
        missing=len(missing_symbols),
        stale=len(stale_symbols),
    )
    next_action = "Price cache coverage is sufficient for advisory review."
    if status != "READY":
        next_action = "Inspect hydration health and run the approved hydration workflow if coverage is stale or incomplete."

    return {
        "coverage_status": status,
        "cache_path": str(cache),
        "cache_exists": True,
        "trade_date": trade_date,
        "expected_symbols": len(active_symbols),
        "symbols_present": len(present_symbols & set(active_symbols)),
        "symbols_missing_count": len(missing_symbols),
        "symbols_missing_sample": missing_symbols[:20],
        "stale_symbols_count": len(stale_symbols),
        "stale_symbols_sample": stale_symbols[:20],
        "cache_min_date": cache_min_date,
        "cache_max_date": cache_max_date,
        "row_count": int(len(panel)),
        "ignored_tickers": sorted(ignored),
        "aliased_tickers": aliases,
        "runtime_effect": "none",
        "sidecar_status": "advisory_preview_only",
        "recommended_next_action": next_action,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Price Cache Coverage",
        "",
        f"- Coverage status: {payload.get('coverage_status')}",
        f"- Trade date: {payload.get('trade_date') or 'not provided'}",
        f"- Cache path: {payload.get('cache_path')}",
        f"- Cache exists: {payload.get('cache_exists')}",
        f"- Cache min date: {payload.get('cache_min_date') or 'unknown'}",
        f"- Cache max date: {payload.get('cache_max_date') or 'unknown'}",
        f"- Row count: {payload.get('row_count') if payload.get('row_count') is not None else 'unknown'}",
        f"- Expected symbols: {payload.get('expected_symbols') if payload.get('expected_symbols') is not None else 'unknown'}",
        f"- Symbols present: {payload.get('symbols_present') if payload.get('symbols_present') is not None else 'unknown'}",
        f"- Symbols missing: {payload.get('symbols_missing_count') if payload.get('symbols_missing_count') is not None else 'unknown'}",
        f"- Stale symbols: {payload.get('stale_symbols_count') if payload.get('stale_symbols_count') is not None else 'unknown'}",
        f"- Runtime effect: {payload.get('runtime_effect')}",
        f"- Sidecar status: {payload.get('sidecar_status')}",
        "",
        "## Operator Next Action",
        str(payload.get("recommended_next_action") or "Inspect price hydration health."),
    ]
    missing = list(payload.get("symbols_missing_sample") or [])
    stale = list(payload.get("stale_symbols_sample") or [])
    if missing:
        lines.extend(["", "## Missing Symbols Sample", *[f"- {symbol}" for symbol in missing]])
    if stale:
        lines.extend(["", "## Stale Symbols Sample", *[f"- {symbol}" for symbol in stale]])
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check read-only price cache coverage.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--universe-path", default="data/universe.csv")
    parser.add_argument("--ticker-exceptions-path", default="data/ticker_exceptions.json")
    parser.add_argument("--trade-date")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero unless coverage status is READY.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = inspect_price_cache_coverage(
        repo_root=Path(args.repo_root),
        cache_path=Path(args.cache_path),
        universe_path=Path(args.universe_path),
        ticker_exceptions_path=Path(args.ticker_exceptions_path),
        trade_date=args.trade_date,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.markdown:
        print(render_markdown(payload), end="")
    else:
        print(
            f"[PRICE_CACHE] Coverage: {payload.get('coverage_status')}\n"
            f"[PRICE_CACHE] Cache Max Date: {payload.get('cache_max_date') or 'unknown'}\n"
            f"[PRICE_CACHE] Missing Symbols: {payload.get('symbols_missing_count')}\n"
            f"[PRICE_CACHE] Runtime Effect: {payload.get('runtime_effect')}"
        )
    if args.strict and payload.get("coverage_status") != "READY":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
