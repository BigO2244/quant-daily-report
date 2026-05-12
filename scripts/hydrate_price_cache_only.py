#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.price_hydration import (  # noqa: E402
    DEFAULT_CACHE_PATH,
    build_status_payload,
    cache_max_date,
    resolve_completed_trading_day,
    write_status,
)
from research.shadow_tracking import run as shadow_run  # noqa: E402
from research.flow_detection.data import ensure_price_panel, load_universe  # noqa: E402


BENCHMARK_SYMBOL = "SPY"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hydrate the canonical shadow price cache only, without running shadow strategy artifacts."
    )
    parser.add_argument("--trade-date", default=None, help="Optional completed trading day. Defaults to latest completed trading day.")
    parser.add_argument("--start-date", default="2014-01-01")
    parser.add_argument("--universe-path", default="data/universe.csv")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--status-dir", default="outputs/price_hydration")
    parser.add_argument("--ticker-exceptions-path", default="data/ticker_exceptions.json")
    parser.add_argument("--hydration-source", default="vm_cache_only")
    parser.add_argument("--chunk-size", type=int, default=25)
    parser.add_argument(
        "--refresh-shadow-artifacts",
        action="store_true",
        help="After verified cache hydration, regenerate and publish artifact-only shadow outputs for the completed day.",
    )
    parser.add_argument("--shadow-output-dir", default="outputs/shadow_candidates")
    parser.add_argument("--dry-run", action="store_true", help="Resolve inputs and print planned action without downloads or writes.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if cache coverage is not verified.")
    return parser


def _resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else _REPO_ROOT / path


def _load_symbols(universe_path: Path) -> list[str]:
    universe = load_universe(universe_path)
    return sorted(set(universe + [BENCHMARK_SYMBOL]))


def _publish_shadow_latest(shadow_output_dir: Path, trade_date: str) -> dict[str, Any]:
    dated_dir = shadow_output_dir / trade_date
    latest_dir = shadow_output_dir / "latest"
    artifacts = ("comparison.md", "comparison.json", "delta.json", "shadow_evaluation.json")
    missing: list[str] = []
    latest_dir.mkdir(parents=True, exist_ok=True)
    for artifact in artifacts:
        source = dated_dir / artifact
        if not source.exists():
            missing.append(artifact)
            continue
        (latest_dir / artifact).write_bytes(source.read_bytes())
    return {
        "latest_dir": str(latest_dir),
        "published_artifacts": [artifact for artifact in artifacts if artifact not in missing],
        "missing_artifacts": missing,
        "status": "OK" if not missing else "PARTIAL",
    }


def _refresh_shadow_artifacts(
    *,
    trade_date: str,
    start_date: str,
    cache_path: Path,
    shadow_output_dir: Path,
) -> dict[str, Any]:
    argv = [
        "--trade-date",
        trade_date,
        "--start-date",
        start_date,
        "--end-date",
        trade_date,
        "--output-dir",
        str(shadow_output_dir),
        "--price-cache-path",
        str(cache_path),
    ]
    rc = shadow_run.main(argv)
    publish = _publish_shadow_latest(shadow_output_dir, trade_date) if rc == 0 else {}
    status = "OK" if rc == 0 and publish.get("status") == "OK" else "FAILED" if rc != 0 else "PARTIAL"
    return {
        "status": status,
        "exit_code": int(rc),
        "trade_date": trade_date,
        "shadow_output_dir": str(shadow_output_dir),
        "publish": publish,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    as_of_date = resolve_completed_trading_day(explicit_trade_date=args.trade_date)
    cache_path = _resolve_path(args.cache_path)
    status_path = _resolve_path(args.status_dir) / as_of_date / "status.json"
    universe_path = _resolve_path(args.universe_path)
    ticker_exceptions_path = _resolve_path(args.ticker_exceptions_path)
    shadow_output_dir = _resolve_path(args.shadow_output_dir)
    before_max_date = cache_max_date(cache_path)

    symbols = _load_symbols(universe_path)
    planned = {
        "as_of_date": as_of_date,
        "start_date": args.start_date,
        "cache_path": str(cache_path),
        "status_path": str(status_path),
        "ticker_exceptions_path": str(ticker_exceptions_path),
        "symbols_requested": len(symbols),
        "before_max_cache_date": before_max_date,
        "hydration_source": args.hydration_source,
        "refresh_shadow_artifacts": bool(args.refresh_shadow_artifacts),
        "shadow_output_dir": str(shadow_output_dir),
        "dry_run": bool(args.dry_run),
        "artifact_only": True,
    }
    print(json.dumps(planned, indent=2, sort_keys=True))

    if args.dry_run:
        return 0

    hydration_exit_code = 0
    panel_meta: dict = {}
    try:
        _panel, panel_meta = ensure_price_panel(
            symbols=symbols,
            start_date=args.start_date,
            end_date=as_of_date,
            cache_path=cache_path,
            prefer_local=True,
            allow_download=True,
            chunk_size=args.chunk_size,
            ticker_exceptions_path=ticker_exceptions_path,
        )
    except Exception as exc:
        hydration_exit_code = 1
        panel_meta = {"error": str(exc)}
        print(f"[PRICE_CACHE_ONLY][WARN] hydration failed: {exc}", file=sys.stderr)

    max_date = cache_max_date(cache_path)
    payload = build_status_payload(
        as_of_date=as_of_date,
        max_cache_date=max_date,
        hydration_exit_code=hydration_exit_code,
        download_attempted=bool(panel_meta.get("download_performed", True)),
        provider="yfinance",
        hydration_source=args.hydration_source,
    )
    payload["cache_only"] = True
    payload["symbols_requested"] = len(symbols)
    payload["before_max_cache_date"] = before_max_date
    payload["canonical_cache_path"] = str(cache_path)
    payload["ignored_tickers"] = list(panel_meta.get("ignored_tickers") or [])
    payload["aliased_tickers"] = dict(panel_meta.get("aliased_tickers") or {})
    payload["panel_meta"] = panel_meta

    shadow_refresh: dict[str, Any] | None = None
    if args.refresh_shadow_artifacts and payload.get("status") == "OK":
        try:
            shadow_refresh = _refresh_shadow_artifacts(
                trade_date=as_of_date,
                start_date=args.start_date,
                cache_path=cache_path,
                shadow_output_dir=shadow_output_dir,
            )
        except Exception as exc:
            shadow_refresh = {
                "status": "FAILED",
                "exit_code": 1,
                "trade_date": as_of_date,
                "shadow_output_dir": str(shadow_output_dir),
                "error": str(exc),
            }
            print(f"[PRICE_CACHE_ONLY][WARN] shadow refresh failed: {exc}", file=sys.stderr)
    elif args.refresh_shadow_artifacts:
        shadow_refresh = {
            "status": "SKIPPED",
            "trade_date": as_of_date,
            "reason": f"price hydration status is {payload.get('status')}",
        }
    if shadow_refresh is not None:
        payload["shadow_refresh"] = shadow_refresh

    write_status(status_path, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))

    if args.strict and payload.get("status") != "OK":
        return 1
    if args.strict and shadow_refresh is not None and shadow_refresh.get("status") != "OK":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
