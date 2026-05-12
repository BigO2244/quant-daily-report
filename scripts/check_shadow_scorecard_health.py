#!/usr/bin/env python3
"""Read-only health check for the Shadow model scorecard.

The command is intended for post-recovery/operator checks. It reads existing
Shadow scorecard, NAV, and hydration artifacts and writes compact diagnostics.
It does not generate trading artifacts, run broker code, or mutate historical
Shadow outputs.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.send_shadow_cio_report import MODEL_ORDER, build_report  # noqa: E402


MODEL_SLUGS = ("caerus_polaris", "caerus_orion", "caerus_lyra")
BAD_REASON_TOKENS = ("PRICE_CACHE_STALE", "NO_PRIOR", "NO_DATA", "BROKEN_CHAIN", "missing-shadow-dir")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Shadow scorecard freshness and continuity check.")
    parser.add_argument("--repo-root", default=str(_REPO_ROOT))
    parser.add_argument("--baseline-date", default="2026-05-11")
    parser.add_argument("--baseline-valid-days", type=int, default=16)
    parser.add_argument("--expected-date", default=None, help="Expected latest completed trading date. Defaults to ET-aware weekday estimate.")
    parser.add_argument("--diagnostics-dir", default="outputs/diagnostics")
    parser.add_argument("--strict", action="store_true")
    return parser


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _business_days_after(start_date: str, end_date: str) -> list[str]:
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    days: list[str] = []
    current = start + dt.timedelta(days=1)
    while current <= end:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += dt.timedelta(days=1)
    return days


def _previous_business_day(day: dt.date) -> dt.date:
    current = day - dt.timedelta(days=1)
    while current.weekday() >= 5:
        current -= dt.timedelta(days=1)
    return current


def _expected_completed_trading_day(now: dt.datetime | None = None) -> str:
    current = now or dt.datetime.now(ZoneInfo("America/New_York"))
    day = current.date()
    if day.weekday() >= 5:
        return _previous_business_day(day).isoformat()
    if current.time() < dt.time(18, 0):
        return _previous_business_day(day).isoformat()
    return day.isoformat()


def _load_nav_dates(path: Path) -> list[str]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [str(row.get("date") or "") for row in csv.DictReader(handle) if row.get("date")]


def _scan_post_baseline_artifacts(repo_root: Path, *, baseline_date: str, expected_date: str) -> list[dict[str, str]]:
    root = repo_root / "outputs" / "shadow_candidates"
    issues: list[dict[str, str]] = []
    for day in _business_days_after(baseline_date, expected_date):
        dated_dir = root / day
        if not dated_dir.exists():
            issues.append({"date": day, "reason": "missing-shadow-dir"})
            continue
        evaluation = _read_json(dated_dir / "shadow_evaluation.json") or {}
        performance = _read_json(dated_dir / "shadow_performance.json") or {}
        comparison = _read_json(dated_dir / "comparison.json") or {}
        reasons: list[str] = []
        if performance.get("status") in ("NO_PRIOR", "BROKEN_CHAIN"):
            reasons.append(str(performance.get("status")))
        if performance.get("data_status") == "NO_DATA":
            reasons.append(str(performance.get("data_reason") or "NO_DATA"))
        if comparison.get("reason_code"):
            reasons.append(str(comparison["reason_code"]))
        for payload in (evaluation.get("strategies") or {}).values():
            if not isinstance(payload, dict):
                continue
            if payload.get("status") in ("NO_PRIOR", "BROKEN_CHAIN"):
                reasons.append(str(payload.get("status")))
            if payload.get("data_status") == "NO_DATA":
                reasons.append(str(payload.get("data_reason") or "NO_DATA"))
        bad = [reason for reason in dict.fromkeys(reasons) if any(token in reason for token in BAD_REASON_TOKENS)]
        if bad:
            issues.append({"date": day, "reason": "; ".join(bad)})
    return issues


def _model_by_slug(report, slug: str):
    return next((model for model in report.models if model.slug == slug), None)


def build_health_payload(
    *,
    repo_root: Path,
    baseline_date: str,
    baseline_valid_days: int,
    expected_date: str,
    strict: bool,
) -> dict[str, Any]:
    report = build_report(repo_root)
    hydration_path = repo_root / "outputs" / "price_hydration" / str(report.latest_source_date or report.trade_date) / "status.json"
    hydration = _read_json(hydration_path) or {}
    shadow_refresh = hydration.get("shadow_refresh") or {}
    nav_dates = _load_nav_dates(repo_root / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv")
    valid_days = {
        slug: (_model_by_slug(report, slug).valid_day_count if _model_by_slug(report, slug) else None)
        for slug in MODEL_SLUGS
    }
    seven_day_through = next((model.seven_day_end_date for model in report.models if model.slug in MODEL_ORDER and model.seven_day_end_date), None)
    ytd_through = next((model.period_end_date for model in report.models if model.slug in MODEL_ORDER and model.period_end_date), None)
    post_baseline_issues = _scan_post_baseline_artifacts(repo_root, baseline_date=baseline_date, expected_date=expected_date)

    checks: list[dict[str, Any]] = []

    def add_check(name: str, passed: bool, detail: str, severity: str = "FAIL") -> None:
        checks.append({"name": name, "passed": bool(passed), "severity": severity, "detail": detail})

    add_check("scorecard_fresh", report.data_health == "Fresh", report.data_health_reason)
    add_check("shadow_refresh_ok", shadow_refresh.get("status") == "OK", f"shadow_refresh.status={shadow_refresh.get('status')}; reason={shadow_refresh.get('reason')}")
    add_check("max_cache_current", str(hydration.get("max_cache_date") or "") >= expected_date, f"max_cache_date={hydration.get('max_cache_date')}; expected={expected_date}")
    add_check("data_through_expected", report.as_of_date == expected_date, f"data_through={report.as_of_date}; expected={expected_date}")
    add_check("nav_not_regressed", (nav_dates[-1] if nav_dates else "") >= baseline_date, f"nav_series_latest_date={nav_dates[-1] if nav_dates else None}; baseline={baseline_date}")
    new_trading_day_available = expected_date > baseline_date
    min_valid = min((value for value in valid_days.values() if value is not None), default=None)
    if new_trading_day_available:
        add_check(
            "valid_days_advanced",
            min_valid is not None and min_valid > baseline_valid_days,
            f"min_valid_days={min_valid}; baseline={baseline_valid_days}; expected_date={expected_date}",
        )
    else:
        add_check(
            "valid_days_at_or_above_baseline",
            min_valid is not None and min_valid >= baseline_valid_days,
            f"min_valid_days={min_valid}; baseline={baseline_valid_days}; no newer trading date expected",
        )
    add_check(
        "no_post_baseline_bad_reasons",
        not post_baseline_issues,
        f"issues={post_baseline_issues}",
    )

    failed = [check for check in checks if not check["passed"] and check["severity"] == "FAIL"]
    warnings = [check for check in checks if not check["passed"] and check["severity"] == "WARN"]
    status = "FAIL" if failed and strict else "WARN" if failed or warnings else "OK"
    recommendation = (
        "Scorecard is fresh and recovery appears to be holding."
        if status == "OK"
        else "Investigate Shadow scorecard freshness before using promotion signals."
    )
    return {
        "status": status,
        "strict": bool(strict),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "baseline_date": baseline_date,
        "baseline_valid_days": baseline_valid_days,
        "expected_latest_completed_trading_day": expected_date,
        "latest_source_date": report.latest_source_date,
        "data_through_date": report.as_of_date,
        "seven_day_through_date": seven_day_through,
        "ytd_through_date": ytd_through,
        "scorecard_data_health": report.data_health,
        "scorecard_data_health_reason": report.data_health_reason,
        "hydration_status": hydration.get("status"),
        "max_cache_date": hydration.get("max_cache_date"),
        "shadow_refresh_status": shadow_refresh.get("status"),
        "shadow_refresh_reason": shadow_refresh.get("reason"),
        "nav_series_latest_date": nav_dates[-1] if nav_dates else None,
        "valid_days": valid_days,
        "post_baseline_issues": post_baseline_issues,
        "checks": checks,
        "operator_recommendation": recommendation,
    }


def _write_artifacts(payload: dict[str, Any], diagnostics_dir: Path) -> tuple[Path, Path]:
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    json_path = diagnostics_dir / f"shadow_scorecard_health_{stamp}.json"
    md_path = diagnostics_dir / f"shadow_scorecard_health_{stamp}.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# Shadow Scorecard Health",
        "",
        f"- Status: `{payload['status']}`",
        f"- Expected latest completed trading day: `{payload['expected_latest_completed_trading_day']}`",
        f"- Data through: `{payload['data_through_date']}`",
        f"- NAV latest date: `{payload['nav_series_latest_date']}`",
        f"- Hydration: `{payload['hydration_status']}`",
        f"- Shadow refresh: `{payload['shadow_refresh_status']}`",
        f"- Valid days: `{payload['valid_days']}`",
        f"- Recommendation: {payload['operator_recommendation']}",
        "",
        "## Checks",
        "",
    ]
    for check in payload["checks"]:
        marker = "PASS" if check["passed"] else check["severity"]
        lines.append(f"- `{marker}` {check['name']}: {check['detail']}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    expected_date = args.expected_date or _expected_completed_trading_day()
    payload = build_health_payload(
        repo_root=repo_root,
        baseline_date=args.baseline_date,
        baseline_valid_days=args.baseline_valid_days,
        expected_date=expected_date,
        strict=bool(args.strict),
    )
    json_path, md_path = _write_artifacts(payload, repo_root / args.diagnostics_dir)
    print(json.dumps({"status": payload["status"], "json_path": str(json_path), "md_path": str(md_path)}, indent=2))
    return 1 if args.strict and payload["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
