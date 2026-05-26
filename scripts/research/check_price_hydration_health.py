"""Read-only price hydration health diagnostics for research packets."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

from core.price_hydration import EOD_READY_TIME, current_et, resolve_completed_trading_day


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HYDRATION_WINDOW_TIME = dt.time(hour=18, minute=30)


def _read_json(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {}, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"json_read_error:{exc}"
    if not isinstance(payload, dict):
        return {}, "json_read_error:not_object"
    return payload, None


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _latest_dated_dir(root: Path) -> str | None:
    if not root.exists():
        return None
    dates = [path.name for path in root.iterdir() if path.is_dir() and DATE_RE.match(path.name)]
    return sorted(dates)[-1] if dates else None


def _latest_expected_trade_date(repo_root: Path) -> str | None:
    shadow_latest = _latest_dated_dir(repo_root / "outputs" / "shadow_candidates")
    if shadow_latest:
        return shadow_latest
    return _latest_dated_dir(repo_root / "outputs" / "price_hydration")


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _nested_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _field(payload: dict[str, Any], *keys: str) -> Any:
    direct = _first_present(payload, *keys)
    if direct is not None:
        return direct
    for nested_key in ("cache", "coverage", "metadata", "summary", "hydration"):
        nested = _nested_dict(payload, nested_key)
        nested_value = _first_present(nested, *keys)
        if nested_value is not None:
            return nested_value
    return None


def _symbol_list(payload: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return sorted({str(item).upper() for item in value if str(item).strip()})
        if isinstance(value, dict):
            return sorted({str(item).upper() for item in value.keys() if str(item).strip()})
    for nested_key in ("coverage", "cache", "summary"):
        nested = _nested_dict(payload, nested_key)
        if not nested:
            continue
        nested_values = _symbol_list(nested, *keys)
        if nested_values:
            return nested_values
    return []


def _last_successful_hydration(repo_root: Path) -> dict[str, Any]:
    hydration_root = repo_root / "outputs" / "price_hydration"
    best: dict[str, Any] = {}
    if not hydration_root.exists():
        return best
    for status_path in sorted(hydration_root.glob("*/status.json")):
        payload, _ = _read_json(status_path)
        status = str(_field(payload, "status", "hydration_status") or "").upper()
        if status != "OK":
            continue
        trade_date = status_path.parent.name
        cache_max_date = _field(
            payload,
            "cache_max_date",
            "max_cache_date",
            "max_price_date",
            "data_through_date",
            "as_of_date",
            "trade_date",
        )
        best = {
            "trade_date": trade_date,
            "status_path": str(status_path.relative_to(repo_root)),
            "cache_max_date": cache_max_date,
            "hydrated_at": _field(payload, "hydrated_at", "completed_at", "finished_at", "produced_at", "timestamp_utc"),
        }
    return best


def _recommended_next_action(classification: str) -> str:
    if classification == "healthy":
        return "Hydration appears complete for the expected trade date; run Orion.command normally."
    if classification == "waiting_for_post_close":
        return "Post-close hydration window has not occurred yet. Wait until after 18:30 ET, then rerun the source readiness check."
    if classification == "partial":
        return "Hydration appears partial; inspect missing symbols and rerun the approved hydration workflow if needed."
    if classification == "stale_but_recoverable":
        return "Run or wait for the approved post-close hydration workflow, then refresh shadow artifacts before using FR-030."
    return "Inspect hydration status artifacts and shadow refresh logs before using FR-030 for research review."


def inspect_hydration_health(
    *,
    repo_root: Path,
    trade_date: str | None = None,
    latest: bool = False,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    now_et = current_et(now)
    expected_latest_trade_date = resolve_completed_trading_day(now=now_et)
    expected_trade_date = trade_date
    if latest or not expected_trade_date:
        expected_trade_date = _latest_expected_trade_date(repo_root)

    market_close_reference_time = EOD_READY_TIME.strftime("%H:%M:%S")
    hydration_window_time = HYDRATION_WINDOW_TIME.strftime("%H:%M:%S")

    def _hydration_window_passed(target_trade_date: str | None) -> bool:
        target_dt = _parse_date(target_trade_date)
        today = now_et.date()
        if target_dt is None:
            return False
        if target_dt < today:
            return True
        if target_dt > today:
            return False
        return now_et.time() >= HYDRATION_WINDOW_TIME

    if not expected_trade_date:
        return {
            "expected_trade_date": None,
            "expected_latest_trade_date": expected_latest_trade_date,
            "current_et_time": now_et.isoformat(),
            "market_close_reference_time": market_close_reference_time,
            "hydration_window_time": hydration_window_time,
            "hydration_window_passed": False,
            "hydration_status": "UNKNOWN",
            "hydration_source": "none",
            "hydrated_at": None,
            "cache_max_date": None,
            "stale_days": None,
            "symbols_expected": None,
            "symbols_present": None,
            "symbols_missing_count": None,
            "missing_symbols_sample": [],
            "partial_or_complete": "UNKNOWN",
            "hydration_state_classification": "unknown",
            "hydration_interpretation": "structurally_broken",
            "last_successful_hydration": {},
            "recommended_next_action": _recommended_next_action("structurally_broken"),
        }

    status_path = repo_root / "outputs" / "price_hydration" / expected_trade_date / "status.json"
    hydration, read_error = _read_json(status_path)
    last_success = _last_successful_hydration(repo_root)

    hydration_status = str(_field(hydration, "status", "hydration_status") or "MISSING").upper()
    hydration_source = str(_field(hydration, "source", "hydration_source", "producer") or "outputs/price_hydration")
    hydrated_at = _field(hydration, "hydrated_at", "completed_at", "finished_at", "produced_at", "timestamp_utc")
    cache_max_date = _field(
        hydration,
        "cache_max_date",
        "max_cache_date",
        "max_price_date",
        "data_through_date",
        "as_of_date",
        "trade_date",
    )
    if cache_max_date is None:
        cache_max_date = last_success.get("cache_max_date")

    expected_dt = _parse_date(expected_trade_date)
    cache_dt = _parse_date(cache_max_date)
    stale_days = (expected_dt - cache_dt).days if expected_dt and cache_dt else None
    hydration_window_passed = _hydration_window_passed(expected_trade_date)

    expected_symbols = _symbol_list(hydration, "symbols_expected", "expected_symbols", "requested_symbols", "universe")
    present_symbols = _symbol_list(hydration, "symbols_present", "present_symbols", "hydrated_symbols", "cached_symbols")
    missing_symbols = _symbol_list(hydration, "symbols_missing", "missing_symbols")
    if not missing_symbols and expected_symbols and present_symbols:
        missing_symbols = sorted(set(expected_symbols) - set(present_symbols))

    symbols_expected_count = len(expected_symbols) if expected_symbols else None
    symbols_present_count = len(present_symbols) if present_symbols else None
    symbols_missing_count = len(missing_symbols) if missing_symbols else 0

    if not status_path.exists():
        partial_or_complete = "MISSING"
    elif str(hydration_status).upper() == "PARTIAL" or symbols_missing_count > 0:
        partial_or_complete = "PARTIAL"
    elif hydration_status == "OK":
        partial_or_complete = "COMPLETE"
    else:
        partial_or_complete = "UNKNOWN"

    cache_matches_latest_completed = bool(cache_max_date and str(cache_max_date) >= str(expected_latest_trade_date))
    is_same_day_before_hydration = (
        expected_dt == now_et.date()
        and not hydration_window_passed
        and not status_path.exists()
        and cache_matches_latest_completed
    )

    if status_path.exists() and read_error:
        interpretation = "structurally_broken"
    elif partial_or_complete == "PARTIAL":
        interpretation = "partial"
    elif hydration_status == "OK" and (stale_days is None or stale_days <= 0):
        interpretation = "healthy"
    elif is_same_day_before_hydration:
        interpretation = "waiting_for_post_close"
    elif not status_path.exists() and last_success:
        interpretation = "stale_but_recoverable"
    elif stale_days is not None and stale_days > 0:
        interpretation = "stale_but_recoverable"
    elif not status_path.exists() and hydration_window_passed:
        interpretation = "structurally_broken"
    elif not status_path.exists():
        interpretation = "waiting_for_post_close"
    else:
        interpretation = "structurally_broken"

    return {
        "expected_trade_date": expected_trade_date,
        "expected_latest_trade_date": expected_latest_trade_date,
        "current_et_time": now_et.isoformat(),
        "market_close_reference_time": market_close_reference_time,
        "hydration_window_time": hydration_window_time,
        "hydration_window_passed": hydration_window_passed,
        "hydration_status_path": str(status_path.relative_to(repo_root)),
        "hydration_status": hydration_status,
        "hydration_source": hydration_source,
        "hydrated_at": hydrated_at,
        "cache_max_date": cache_max_date,
        "stale_days": stale_days,
        "symbols_expected": symbols_expected_count,
        "symbols_present": symbols_present_count,
        "symbols_missing_count": symbols_missing_count,
        "missing_symbols_sample": missing_symbols[:20],
        "partial_or_complete": partial_or_complete,
        "hydration_state_classification": interpretation,
        "hydration_interpretation": interpretation,
        "read_error": read_error,
        "last_successful_hydration": last_success,
        "recommended_next_action": _recommended_next_action(interpretation),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Price Hydration Health",
        "",
        f"- Expected trade date: {payload.get('expected_trade_date') or 'UNKNOWN'}",
        f"- Expected latest completed trade date: {payload.get('expected_latest_trade_date') or 'UNKNOWN'}",
        f"- Current ET time: {payload.get('current_et_time') or 'unknown'}",
        f"- Market close reference time: {payload.get('market_close_reference_time') or 'unknown'}",
        f"- Hydration window time: {payload.get('hydration_window_time') or 'unknown'}",
        f"- Hydration window passed: {payload.get('hydration_window_passed')}",
        f"- Hydration status: {payload.get('hydration_status')}",
        f"- Classification: {payload.get('hydration_state_classification') or payload.get('hydration_interpretation')}",
        f"- Partial or complete: {payload.get('partial_or_complete')}",
        f"- Hydrated at: {payload.get('hydrated_at') or 'unknown'}",
        f"- Cache max date: {payload.get('cache_max_date') or 'unknown'}",
        f"- Stale days: {payload.get('stale_days') if payload.get('stale_days') is not None else 'unknown'}",
        f"- Symbols expected: {payload.get('symbols_expected') if payload.get('symbols_expected') is not None else 'unknown'}",
        f"- Symbols present: {payload.get('symbols_present') if payload.get('symbols_present') is not None else 'unknown'}",
        f"- Symbols missing: {payload.get('symbols_missing_count') if payload.get('symbols_missing_count') is not None else 'unknown'}",
        "",
        "## Missing Symbols Sample",
    ]
    sample = list(payload.get("missing_symbols_sample") or [])
    lines.extend([f"- {symbol}" for symbol in sample] if sample else ["- none"])
    lines.extend(["", "## Recommended Next Action", "", str(payload.get("recommended_next_action") or "")])
    return "\n".join(lines) + "\n"


def render_text(payload: dict[str, Any]) -> str:
    return (
        f"[HYDRATION] Expected Trade Date: {payload.get('expected_trade_date') or 'UNKNOWN'}\n"
        f"[HYDRATION] Expected Latest Completed Trade Date: {payload.get('expected_latest_trade_date') or 'UNKNOWN'}\n"
        f"[HYDRATION] Status: {payload.get('hydration_status')}\n"
        f"[HYDRATION] Classification: {payload.get('hydration_state_classification') or payload.get('hydration_interpretation')}\n"
        f"[HYDRATION] Hydration Window Passed: {payload.get('hydration_window_passed')}\n"
        f"[HYDRATION] Cache Max Date: {payload.get('cache_max_date') or 'unknown'}\n"
        f"[HYDRATION] Stale Days: {payload.get('stale_days') if payload.get('stale_days') is not None else 'unknown'}\n"
        f"[HYDRATION] Missing Symbols: {payload.get('symbols_missing_count') if payload.get('symbols_missing_count') is not None else 'unknown'}\n"
        f"[HYDRATION] Next Action: {payload.get('recommended_next_action')}\n"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check read-only price hydration health.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--trade-date", help="Expected trade date to inspect, YYYY-MM-DD.")
    group.add_argument("--latest", action="store_true", help="Inspect latest expected research trade date.")
    parser.add_argument("--repo-root", default=".", help="Repository root to inspect.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument("--markdown", action="store_true", help="Emit Markdown.")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero unless hydration interpretation is ready.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = inspect_hydration_health(
        repo_root=Path(args.repo_root),
        trade_date=args.trade_date,
        latest=bool(args.latest),
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.markdown:
        print(render_markdown(payload))
    else:
        print(render_text(payload), end="")
    if args.strict and payload.get("hydration_state_classification") != "healthy":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
