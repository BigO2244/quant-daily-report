from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from paper.trading_calendar import is_trading_day, prev_trading_day


ET = ZoneInfo("America/New_York")
EOD_READY_TIME = dt.time(hour=16, minute=15)
DEFAULT_CACHE_PATH = Path("outputs/research/flow_detection_v1/price_panel.parquet")


def current_et(now: dt.datetime | None = None) -> dt.datetime:
    if now is None:
        return dt.datetime.now(ET)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(ET)


def resolve_completed_trading_day(*, explicit_trade_date: str | None = None, now: dt.datetime | None = None) -> str:
    if explicit_trade_date:
        return str(explicit_trade_date)
    now_et = current_et(now)
    today = now_et.date().isoformat()
    if is_trading_day(today) and now_et.time() >= EOD_READY_TIME:
        return today
    return prev_trading_day(today)


def cache_max_date(cache_path: Path = DEFAULT_CACHE_PATH) -> str | None:
    if not cache_path.exists():
        return None
    try:
        frame = pd.read_parquet(cache_path, columns=["date"])
    except Exception:
        return None
    if frame.empty or "date" not in frame.columns:
        return None
    dates = pd.to_datetime(frame["date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return str(dates.max().date())


def build_status_payload(
    *,
    as_of_date: str,
    max_cache_date: str | None,
    run_timestamp: str | None = None,
    download_attempted: bool = True,
    provider: str = "yfinance",
    hydration_exit_code: int = 0,
    hydration_source: str | None = None,
) -> dict[str, Any]:
    if run_timestamp is None:
        run_timestamp = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if max_cache_date is None:
        status = "FAILED"
        reason = "price cache missing or unreadable"
    elif hydration_exit_code != 0 and max_cache_date >= as_of_date:
        status = "PARTIAL"
        reason = f"hydration command exited {hydration_exit_code}, but cache coverage is verified"
    elif hydration_exit_code != 0:
        status = "FAILED"
        reason = f"hydration command exited {hydration_exit_code}"
    elif max_cache_date >= as_of_date:
        status = "OK"
        reason = "cache coverage verified"
    else:
        status = "PARTIAL"
        reason = f"cache max date {max_cache_date} is before requested as_of_date {as_of_date}"
    payload = {
        "run_timestamp": run_timestamp,
        "as_of_date": as_of_date,
        "status": status,
        "max_cache_date": max_cache_date,
        "reason": reason,
        "notes": "Artifact-only VM price hydration; no trading or execution path invoked.",
        "download_attempted": bool(download_attempted),
        "provider": provider,
    }
    if hydration_source:
        payload["hydration_source"] = hydration_source
    return payload


def write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_now(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ET)
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Price hydration date/status helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subparsers.add_parser("resolve-date")
    resolve_parser.add_argument("--trade-date", default=None)
    resolve_parser.add_argument("--now", default=None)

    verify_parser = subparsers.add_parser("write-status")
    verify_parser.add_argument("--as-of-date", required=True)
    verify_parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    verify_parser.add_argument("--status-path", required=True)
    verify_parser.add_argument("--hydration-exit-code", type=int, default=0)
    verify_parser.add_argument("--download-attempted", default="true")
    verify_parser.add_argument("--hydration-source", default=None)

    args = parser.parse_args(argv)
    if args.command == "resolve-date":
        print(resolve_completed_trading_day(explicit_trade_date=args.trade_date, now=_parse_now(args.now)))
        return 0
    if args.command == "write-status":
        max_date = cache_max_date(Path(args.cache_path))
        payload = build_status_payload(
            as_of_date=str(args.as_of_date),
            max_cache_date=max_date,
            hydration_exit_code=int(args.hydration_exit_code),
            download_attempted=str(args.download_attempted).strip().lower() in {"1", "true", "yes", "y", "on"},
            hydration_source=args.hydration_source,
        )
        write_status(Path(args.status_path), payload)
        print(json.dumps(payload, sort_keys=True))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
