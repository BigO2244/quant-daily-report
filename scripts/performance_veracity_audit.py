#!/usr/bin/env python3
"""Independent performance veracity audit for Caerus Shadow reporting.

This command is intentionally read-only with respect to trading, strategy, and
execution paths. It challenges existing reporting artifacts by recomputing
returns from persisted NAV chains and by reviewing temporal/accounting
assumptions that can inflate reported performance.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


MODEL_SLUGS = ("caerus_polaris", "caerus_orion", "caerus_lyra", "spy_benchmark")
DISPLAY_NAMES = {
    "caerus_polaris": "Polaris",
    "caerus_orion": "Orion",
    "caerus_lyra": "Lyra",
    "spy_benchmark": "SPY",
}
BAD_REASON_TOKENS = ("NO_PRIOR", "BROKEN_CHAIN", "NO_DATA", "PRICE_CACHE_STALE")
EPSILON = 1e-8


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Performance Veracity Audit.")
    parser.add_argument("--repo-root", default=str(_REPO_ROOT))
    parser.add_argument("--output-root", default="outputs/audits/performance_veracity")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--ytd-year", default=None)
    parser.add_argument("--strict", action="store_true", help="Exit non-zero unless every strategy is VERIFIED.")
    return parser


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.2%}"


def _check(name: str, passed: bool, severity: str, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "severity": severity, "detail": detail}


def _business_day_gap(left: str, right: str) -> int:
    start = dt.date.fromisoformat(left)
    end = dt.date.fromisoformat(right)
    days = 0
    current = start + dt.timedelta(days=1)
    while current < end:
        if current.weekday() < 5:
            days += 1
        current += dt.timedelta(days=1)
    return days


def _load_nav_series(path: Path, *, through_date: str | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            date = str(row.get("date") or "")
            if not date or (through_date and date > through_date):
                continue
            parsed: dict[str, Any] = {"date": date}
            for slug in MODEL_SLUGS:
                parsed[slug] = _as_float(row.get(slug))
            rows.append(parsed)
    return rows


def _date_dirs(output_root: Path, *, through_date: str | None = None) -> list[str]:
    dates: list[str] = []
    for child in output_root.iterdir() if output_root.exists() else []:
        if not child.is_dir():
            continue
        try:
            dt.date.fromisoformat(child.name)
        except ValueError:
            continue
        if through_date is None or child.name <= through_date:
            dates.append(child.name)
    return sorted(dates)


def _nav_points(rows: list[dict[str, Any]], slug: str) -> list[tuple[str, float]]:
    return [(str(row["date"]), float(row[slug])) for row in rows if row.get(slug) is not None]


def _daily_returns_from_nav(points: list[tuple[str, float]]) -> list[dict[str, Any]]:
    returns: list[dict[str, Any]] = []
    for idx in range(1, len(points)):
        prev_date, prev_nav = points[idx - 1]
        date, nav = points[idx]
        value = None if prev_nav == 0 else nav / prev_nav - 1.0
        returns.append({"date": date, "previous_date": prev_date, "return": value})
    return returns


def _period_return(points: list[tuple[str, float]], *, start_prefix: str | None = None) -> dict[str, Any]:
    window = [point for point in points if start_prefix is None or point[0].startswith(start_prefix)]
    if len(window) < 2:
        return {"value": None, "start_date": window[0][0] if window else None, "end_date": window[-1][0] if window else None}
    start_date, start_nav = window[0]
    end_date, end_nav = window[-1]
    return {
        "value": None if start_nav == 0 else end_nav / start_nav - 1.0,
        "start_date": start_date,
        "end_date": end_date,
    }


def _max_drawdown(points: list[tuple[str, float]]) -> float | None:
    peak: float | None = None
    worst = 0.0
    for _, nav in points:
        peak = nav if peak is None else max(peak, nav)
        if peak and peak > 0:
            worst = min(worst, nav / peak - 1.0)
    return worst if points else None


def _rolling_return(points: list[tuple[str, float]], days: int) -> dict[str, Any]:
    if len(points) < 2:
        return {"value": None, "start_date": None, "end_date": points[-1][0] if points else None}
    window = points[-days:] if len(points) >= days else points
    start_date, start_nav = window[0]
    end_date, end_nav = window[-1]
    return {
        "value": None if start_nav == 0 else end_nav / start_nav - 1.0,
        "start_date": start_date,
        "end_date": end_date,
    }


def _classify(checks: list[dict[str, Any]]) -> str:
    hard_failures = [check for check in checks if not check["passed"] and check["severity"] == "FAIL"]
    warnings = [check for check in checks if not check["passed"] and check["severity"] == "WARN"]
    if hard_failures:
        return "INVALIDATED"
    if warnings:
        return "PARTIAL_CONFIDENCE"
    return "VERIFIED"


def _combine_classifications(values: list[str]) -> str:
    if any(value == "INVALIDATED" for value in values):
        return "INVALIDATED"
    if any(value == "PARTIAL_CONFIDENCE" for value in values):
        return "PARTIAL_CONFIDENCE"
    return "VERIFIED"


def audit_nav_series(repo_root: Path, *, through_date: str | None, ytd_year: str) -> dict[str, Any]:
    path = repo_root / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    rows = _load_nav_series(path, through_date=through_date)
    dates = [str(row["date"]) for row in rows]
    checks = [
        _check("nav_series_exists", path.exists(), "FAIL", str(path)),
        _check("nav_series_non_empty", bool(rows), "FAIL", f"rows={len(rows)}"),
        _check("strictly_increasing_dates", dates == sorted(set(dates)), "FAIL", "dates must be unique and sorted"),
    ]
    gap_count = sum(1 for left, right in zip(dates, dates[1:]) if _business_day_gap(left, right) > 0)
    weekend_rows = [date for date in dates if dt.date.fromisoformat(date).weekday() >= 5]
    checks.append(_check("no_weekend_rows", not weekend_rows, "FAIL", f"weekend_rows={weekend_rows[:10]}"))
    checks.append(_check("weekday_gap_review", gap_count == 0, "WARN", f"weekday_gaps={gap_count}; holidays are not calendar-adjusted"))

    strategies: dict[str, Any] = {}
    for slug in MODEL_SLUGS:
        points = _nav_points(rows, slug)
        returns = _daily_returns_from_nav(points)
        invalid_nav = [(date, nav) for date, nav in points if nav <= 0 or not math.isfinite(nav)]
        large_daily = [item for item in returns if item["return"] is not None and abs(float(item["return"])) > 0.25]
        strategy_checks = [
            _check("nav_present", bool(points), "FAIL", f"points={len(points)}"),
            _check("nav_positive_finite", not invalid_nav, "FAIL", f"invalid={invalid_nav[:5]}"),
            _check("large_daily_return_review", not large_daily, "WARN", f"abs(return)>25% days={large_daily[:5]}"),
        ]
        cumulative = _period_return(points)
        ytd = _period_return(points, start_prefix=ytd_year)
        strategies[slug] = {
            "name": DISPLAY_NAMES[slug],
            "classification": _classify(strategy_checks),
            "checks": strategy_checks,
            "start_date": points[0][0] if points else None,
            "end_date": points[-1][0] if points else None,
            "point_count": len(points),
            "recomputed_daily_returns": returns[-10:],
            "cumulative_return": cumulative,
            "ytd_return": ytd,
            "drawdown": {"max_drawdown": _max_drawdown(points)},
            "rolling_windows": {
                "7d": _rolling_return(points, 7),
                "21d": _rolling_return(points, 21),
                "63d": _rolling_return(points, 63),
            },
        }
    classifications = [_classify(checks), *[payload["classification"] for payload in strategies.values()]]
    return {
        "path": str(path.relative_to(repo_root)),
        "classification": _combine_classifications(classifications),
        "checks": checks,
        "row_count": len(rows),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "strategies": strategies,
    }


def audit_dated_shadow_chain(repo_root: Path, *, through_date: str | None, ytd_year: str) -> dict[str, Any]:
    output_root = repo_root / "outputs" / "shadow_candidates"
    dates = _date_dirs(output_root, through_date=through_date)
    rows: list[tuple[str, dict[str, Any] | None]] = [
        (date, _read_json(output_root / date / "shadow_performance.json")) for date in dates
    ]
    checks = [
        _check("dated_dirs_present", bool(dates), "FAIL", f"dates={dates}"),
        _check("performance_present_for_all_dates", all(payload is not None for _, payload in rows), "FAIL", f"missing={[date for date, payload in rows if payload is None]}"),
    ]
    bad_reasons: list[dict[str, str]] = []
    operational_points: dict[str, list[tuple[str, float]]] = {slug: [] for slug in MODEL_SLUGS}
    strategy_checks: dict[str, list[dict[str, Any]]] = {slug: [] for slug in MODEL_SLUGS}
    for idx, (date, payload) in enumerate(rows):
        if not payload:
            continue
        status = str(payload.get("status") or "")
        data_status = str(payload.get("data_status") or "")
        data_reason = str(payload.get("data_reason") or "")
        if any(token in f"{status} {data_status} {data_reason}" for token in BAD_REASON_TOKENS):
            bad_reasons.append({"date": date, "status": status, "data_status": data_status, "data_reason": data_reason})
        if status == "NO_PRIOR" and idx > 0:
            checks.append(_check(f"hidden_reinitialization_{date}", False, "FAIL", f"NO_PRIOR after prior dated artifacts exist"))
        if data_status == "NO_DATA":
            checks.append(_check(f"no_data_carry_forward_{date}", data_reason != "", "WARN", f"data_reason={data_reason or 'missing'}"))
        for slug in MODEL_SLUGS:
            item = ((payload.get("strategies") or {}).get(slug) or {})
            nav = _as_float(item.get("nav"))
            prev_nav = _as_float(item.get("previous_nav"))
            daily_return = _as_float(item.get("daily_return"))
            if nav is not None:
                operational_points[slug].append((date, nav))
            if prev_nav is not None and daily_return is not None and nav is not None:
                expected = prev_nav * (1.0 + daily_return)
                strategy_checks[slug].append(
                    _check(
                        f"nav_math_{date}",
                        abs(nav - expected) <= max(EPSILON, abs(expected) * 1e-8),
                        "FAIL",
                        f"nav={nav}; previous_nav={prev_nav}; daily_return={daily_return}; expected={expected}",
                    )
                )
            if data_status == "NO_DATA":
                strategy_checks[slug].append(
                    _check(
                        f"no_data_zero_return_{date}",
                        daily_return == 0.0 and (nav == prev_nav if nav is not None and prev_nav is not None else True),
                        "WARN",
                        f"nav={nav}; previous_nav={prev_nav}; daily_return={daily_return}",
                    )
                )

    for slug, points in operational_points.items():
        lookup = dict(points)
        for left, right in zip(points, points[1:]):
            prev_date, prev_nav = left
            date, _ = right
            payload = dict(rows).get(date) or {}
            current = ((payload.get("strategies") or {}).get(slug) or {})
            artifact_prev_nav = _as_float(current.get("previous_nav"))
            strategy_checks[slug].append(
                _check(
                    f"prior_nav_continuity_{date}",
                    artifact_prev_nav is not None and abs(artifact_prev_nav - prev_nav) <= max(EPSILON, abs(prev_nav) * 1e-8),
                    "FAIL",
                    f"previous artifact {prev_date} nav={lookup.get(prev_date)}; current previous_nav={artifact_prev_nav}",
                )
            )

    strategies: dict[str, Any] = {}
    for slug in MODEL_SLUGS:
        points = operational_points[slug]
        cumulative = _period_return(points)
        ytd = _period_return(points, start_prefix=ytd_year)
        strategies[slug] = {
            "name": DISPLAY_NAMES[slug],
            "classification": _classify(strategy_checks[slug]),
            "checks": strategy_checks[slug],
            "point_count": len(points),
            "cumulative_return": cumulative,
            "ytd_return": ytd,
            "drawdown": {"max_drawdown": _max_drawdown(points)},
            "rolling_windows": {
                "7d": _rolling_return(points, 7),
                "21d": _rolling_return(points, 21),
                "63d": _rolling_return(points, 63),
            },
        }
    checks.append(_check("no_bad_chain_reasons", not bad_reasons, "FAIL", f"bad_reasons={bad_reasons}"))
    return {
        "classification": _combine_classifications([_classify(checks), *[value["classification"] for value in strategies.values()]]),
        "dates": dates,
        "checks": checks,
        "bad_reasons": bad_reasons,
        "strategies": strategies,
    }


def audit_cross_surface_consistency(repo_root: Path, nav_audit: dict[str, Any], chain_audit: dict[str, Any], *, through_date: str | None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    strategies: dict[str, Any] = {}
    nav_rows = _load_nav_series(
        repo_root / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv",
        through_date=through_date,
    )
    nav_by_date = {str(row["date"]): row for row in nav_rows}
    output_root = repo_root / "outputs" / "shadow_candidates"
    for slug in MODEL_SLUGS:
        chain_dates = chain_audit.get("dates") or []
        comparable_dates = [date for date in chain_dates if date in nav_by_date]
        missing_nav_dates = [date for date in chain_dates if date not in nav_by_date]
        missing_payload_dates: list[str] = []
        mismatches: list[dict[str, Any]] = []
        for date in comparable_dates:
            payload = _read_json(output_root / date / "shadow_performance.json") or {}
            chain_nav = _as_float(((payload.get("strategies") or {}).get(slug) or {}).get("nav"))
            long_nav = _as_float(nav_by_date[date].get(slug))
            if chain_nav is None:
                missing_payload_dates.append(date)
                continue
            if chain_nav is None or long_nav is None:
                continue
            if abs(chain_nav - long_nav) > max(EPSILON, abs(long_nav) * 1e-6):
                mismatches.append({"date": date, "shadow_performance_nav": chain_nav, "shadow_nav_series_nav": long_nav})
        detail = (
            f"comparable_dates={comparable_dates}; missing_nav_dates={missing_nav_dates}; "
            f"missing_payload_dates={missing_payload_dates}; mismatches={mismatches[:5]}"
        )
        partial = bool(missing_nav_dates or missing_payload_dates)
        checks.append(_check(f"{slug}_surface_coverage_complete", not partial, "WARN", detail))
        checks.append(_check(f"{slug}_surface_nav_match", not mismatches, "WARN", detail))
        strategies[slug] = {
            "name": DISPLAY_NAMES[slug],
            "comparable_dates": comparable_dates,
            "missing_nav_dates": missing_nav_dates,
            "missing_payload_dates": missing_payload_dates,
            "mismatches": mismatches,
            "classification": "PARTIAL_CONFIDENCE" if partial or mismatches else "VERIFIED",
            "notes": detail,
        }
    return {
        "classification": _combine_classifications([item["classification"] for item in strategies.values()]),
        "checks": checks,
        "strategies": strategies,
    }


def audit_execution_assumptions(repo_root: Path) -> dict[str, Any]:
    run_path = repo_root / "research" / "shadow_tracking" / "run.py"
    engine_path = repo_root / "research" / "alpha_lab_v2" / "engine.py"
    run_text = run_path.read_text(encoding="utf-8") if run_path.exists() else ""
    engine_text = engine_path.read_text(encoding="utf-8") if engine_path.exists() else ""
    checks = [
        _check(
            "operational_shadow_return_uses_same_date_weights",
            "return_convention\": \"weights_as_of_t\"" not in run_text and '"return_convention": "weights_as_of_t"' not in run_text,
            "FAIL",
            "shadow_performance declares weights_as_of_t; same-date weights are multiplied by trade-date close-to-close returns",
        ),
        _check(
            "backtest_uses_forward_return_labeling",
            "pct_change().shift(-1)" not in engine_text,
            "WARN",
            "alpha_lab_v2 backtest uses next close return labeled by signal date; valid only if reported as signal-date-to-next-session return",
        ),
        _check(
            "transaction_cost_model_present",
            "transaction_cost_bps" in engine_text,
            "WARN",
            "cost model exists but fills remain model-close based, not broker-authoritative",
        ),
    ]
    return {
        "classification": _classify(checks),
        "checks": checks,
        "reviewed_files": [str(run_path.relative_to(repo_root)), str(engine_path.relative_to(repo_root))],
    }


def audit_lookahead_bias(repo_root: Path) -> dict[str, Any]:
    signals_path = repo_root / "research" / "alpha_lab_v1" / "signals.py"
    engine_path = repo_root / "research" / "alpha_lab_v2" / "engine.py"
    run_path = repo_root / "research" / "shadow_tracking" / "run.py"
    signals = signals_path.read_text(encoding="utf-8") if signals_path.exists() else ""
    engine = engine_path.read_text(encoding="utf-8") if engine_path.exists() else ""
    run = run_path.read_text(encoding="utf-8") if run_path.exists() else ""
    checks = [
        _check(
            "momentum_features_are_lagged_for_long_horizons",
            "shift(21)" in signals and "shift(126)" in signals and "shift(252)" in signals,
            "WARN",
            "long horizon momentum excludes latest month via shifts",
        ),
        _check(
            "snapshot_filters_to_trade_date_only",
            "frame[\"date\"] <= pd.Timestamp(trade_date)" in engine,
            "WARN",
            "snapshot filters future rows, but same-day close remains available to the model",
        ),
        _check(
            "operational_daily_return_not_same_day_close_leaked",
            "compute_returns_for_trade_date(panel=panel, trade_date=trade_date)" not in run,
            "FAIL",
            "daily shadow_performance uses trade-date returns with trade-date target weights; this is temporal leakage unless decisions are assumed before the same close",
        ),
    ]
    return {
        "classification": _classify(checks),
        "checks": checks,
        "reviewed_files": [str(signals_path.relative_to(repo_root)), str(engine_path.relative_to(repo_root)), str(run_path.relative_to(repo_root))],
    }


def audit_stale_and_repair_state(repo_root: Path, *, through_date: str | None) -> dict[str, Any]:
    output_root = repo_root / "outputs" / "shadow_candidates"
    issues: list[dict[str, Any]] = []
    for date in _date_dirs(output_root, through_date=through_date):
        for filename in ("shadow_performance.json", "shadow_evaluation.json", "comparison.json", "delta.json"):
            payload = _read_json(output_root / date / filename)
            if not payload:
                continue
            blob = json.dumps(payload, sort_keys=True)
            tokens = [token for token in BAD_REASON_TOKENS if token in blob]
            if tokens:
                issues.append({"date": date, "file": filename, "tokens": sorted(set(tokens))})
    checks = [
        _check("no_stale_or_repair_tokens", not issues, "FAIL", f"issues={issues}"),
        _check(
            "hydration_artifacts_available",
            (repo_root / "outputs" / "price_hydration").exists(),
            "WARN",
            "outputs/price_hydration missing locally; cache freshness cannot be independently confirmed from hydration status artifacts",
        ),
    ]
    return {"classification": _classify(checks), "checks": checks, "issues": issues}


def build_recomputed_performance(nav_audit: dict[str, Any], chain_audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "independent recomputation from persisted NAV artifacts",
        "nav_series": {
            slug: {
                "name": payload["name"],
                "cumulative_return": payload["cumulative_return"],
                "ytd_return": payload["ytd_return"],
                "max_drawdown": payload["drawdown"]["max_drawdown"],
                "rolling_windows": payload["rolling_windows"],
            }
            for slug, payload in (nav_audit.get("strategies") or {}).items()
        },
        "dated_shadow_performance": {
            slug: {
                "name": payload["name"],
                "cumulative_return": payload["cumulative_return"],
                "ytd_return": payload["ytd_return"],
                "max_drawdown": payload["drawdown"]["max_drawdown"],
                "rolling_windows": payload["rolling_windows"],
            }
            for slug, payload in (chain_audit.get("strategies") or {}).items()
        },
    }


def build_summary(
    *,
    nav_audit: dict[str, Any],
    chain_audit: dict[str, Any],
    cross_surface: dict[str, Any],
    execution: dict[str, Any],
    lookahead: dict[str, Any],
    stale: dict[str, Any],
    recomputed: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    section_classes = {
        "nav_chain_validation": nav_audit["classification"],
        "dated_shadow_chain_validation": chain_audit["classification"],
        "cross_surface_consistency": cross_surface["classification"],
        "execution_assumption_review": execution["classification"],
        "lookahead_bias_review": lookahead["classification"],
        "stale_repair_review": stale["classification"],
    }
    overall = _combine_classifications(list(section_classes.values()))
    return {
        "schema_version": "performance_veracity_audit_v1",
        "generated_at": generated_at,
        "overall_classification": overall,
        "section_classifications": section_classes,
        "strategy_classifications": {
            slug: _combine_classifications(
                [
                    (nav_audit.get("strategies") or {}).get(slug, {}).get("classification", "INVALIDATED"),
                    (chain_audit.get("strategies") or {}).get(slug, {}).get("classification", "INVALIDATED"),
                    (cross_surface.get("strategies") or {}).get(slug, {}).get("classification", "INVALIDATED"),
                    execution["classification"],
                    lookahead["classification"],
                    stale["classification"],
                ]
            )
            for slug in MODEL_SLUGS
        },
        "key_findings": [
            "Operational shadow daily performance is not independently verified because same-date target weights are applied to same-date close-to-close returns.",
            "Stale/repair markers are present in available shadow artifacts." if stale["issues"] else "No stale/repair markers found in scanned dated shadow artifacts.",
            "Long-run NAV series can be mathematically recomputed, but it is a backtest-style chain and must not be mixed with dated operational shadow_performance NAV bases.",
        ],
        "recomputed_performance": recomputed,
    }


def build_markdown_report(summary: dict[str, Any], payloads: dict[str, Any]) -> str:
    recomputed = summary["recomputed_performance"]
    lines = [
        "# Performance Veracity Audit",
        "",
        "## Executive Summary",
        f"- Overall classification: `{summary['overall_classification']}`",
        "- Scope: Polaris, Orion, Lyra, and SPY benchmark shadow/reporting artifacts.",
        "- Conclusion: reported performance should not be treated as fully decision-grade until temporal leakage and artifact-base issues are remediated.",
        "",
        "## Recomputed Performance",
        "| Surface | Strategy | Period | Return | Max Drawdown |",
        "|---|---|---:|---:|---:|",
    ]
    for surface, strategies in recomputed.items():
        if surface == "source":
            continue
        for slug in MODEL_SLUGS:
            item = strategies.get(slug) or {}
            ytd = item.get("ytd_return") or {}
            period = f"{ytd.get('start_date') or 'N/A'} to {ytd.get('end_date') or 'N/A'}"
            lines.append(
                f"| {surface} | {DISPLAY_NAMES[slug]} | {period} | {_pct(ytd.get('value'))} | {_pct(item.get('max_drawdown'))} |"
            )
    lines.extend(
        [
            "",
            "## Methodology",
            "- Recomputed daily returns from persisted NAV ratios, independent of reported daily return fields.",
            "- Recomputed cumulative return, YTD return, drawdown, and 7/21/63-observation rolling returns.",
            "- Checked dated `shadow_performance.json` chain math against `previous_nav * (1 + daily_return)`.",
            "- Reviewed temporal assumptions in shadow performance and alpha-lab engine source files.",
            "- Scanned dated artifacts for stale, NO_PRIOR, BROKEN_CHAIN, NO_DATA, and PRICE_CACHE_STALE markers.",
            "",
            "## Findings",
        ]
    )
    for finding in summary["key_findings"]:
        lines.append(f"- {finding}")
    lines.extend(["", "## Section Classifications"])
    for name, classification in summary["section_classifications"].items():
        lines.append(f"- `{name}`: `{classification}`")
    lines.extend(["", "## Identified Risks"])
    lines.extend(
        [
            "- Same-day-close leakage risk in operational shadow daily return accounting.",
            "- Different NAV bases across long-run backtest NAV series and dated operational shadow chains.",
            "- Stale cache contamination risk when hydration artifacts are absent or dated artifacts contain PRICE_CACHE_STALE.",
            "- Shadow/live divergence risk because shadow model portfolios are not broker-authoritative fills.",
            "- Execution realism risk because model-close fills and fixed transaction costs are not equivalent to broker fills, slippage, partial fills, or rejected orders.",
        ]
    )
    lines.extend(["", "## Confidence Assessment"])
    for slug, classification in summary["strategy_classifications"].items():
        lines.append(f"- {DISPLAY_NAMES[slug]}: `{classification}`")
    lines.extend(
        [
            "",
            "## Remediation Recommendations",
            "- Separate reported backtest performance, operational shadow chain performance, and live paper broker performance in every report.",
            "- Change operational shadow daily return convention to use prior-day weights against next-session returns, or label same-day-close assumptions as non-verifiable.",
            "- Persist price-as-of timestamps and signal-as-of timestamps in shadow artifacts.",
            "- Add a mandatory audit gate that blocks promotion use when NO_PRIOR, PRICE_CACHE_STALE, or repaired-chain markers appear inside the evaluation window.",
            "- Reconcile Polaris live paper NAV against broker account snapshots and fills before comparing it to shadow model portfolios.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_outputs(run_dir: Path, payloads: dict[str, Any], report: str) -> list[Path]:
    run_dir.mkdir(parents=True, exist_ok=False)
    files = {
        "audit_summary.json": payloads["summary"],
        "nav_chain_validation.json": payloads["nav_chain_validation"],
        "execution_assumption_review.json": payloads["execution_assumption_review"],
        "lookahead_bias_review.json": payloads["lookahead_bias_review"],
        "recomputed_performance.json": payloads["recomputed_performance"],
        "continuity_gap_report.json": payloads["continuity_gap_report"],
        "cross_surface_consistency.json": payloads["cross_surface_consistency"],
        "stale_repair_review.json": payloads["stale_repair_review"],
    }
    written: list[Path] = []
    for filename, payload in files.items():
        path = run_dir / filename
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        written.append(path)
    findings = run_dir / "audit_findings.md"
    findings.write_text(report, encoding="utf-8")
    written.append(findings)
    return written


def run_audit(*, repo_root: Path, output_root: Path, run_id: str | None, as_of_date: str | None, ytd_year: str | None) -> tuple[dict[str, Any], list[Path]]:
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    effective_ytd_year = ytd_year or (as_of_date or dt.date.today().isoformat())[:4]
    nav_audit = audit_nav_series(repo_root, through_date=as_of_date, ytd_year=effective_ytd_year)
    chain_audit = audit_dated_shadow_chain(repo_root, through_date=as_of_date, ytd_year=effective_ytd_year)
    cross_surface = audit_cross_surface_consistency(repo_root, nav_audit, chain_audit, through_date=as_of_date)
    execution = audit_execution_assumptions(repo_root)
    lookahead = audit_lookahead_bias(repo_root)
    stale = audit_stale_and_repair_state(repo_root, through_date=as_of_date)
    recomputed = build_recomputed_performance(nav_audit, chain_audit)
    summary = build_summary(
        nav_audit=nav_audit,
        chain_audit=chain_audit,
        cross_surface=cross_surface,
        execution=execution,
        lookahead=lookahead,
        stale=stale,
        recomputed=recomputed,
        generated_at=generated_at,
    )
    payloads = {
        "summary": summary,
        "nav_chain_validation": nav_audit,
        "execution_assumption_review": execution,
        "lookahead_bias_review": lookahead,
        "recomputed_performance": recomputed,
        "continuity_gap_report": chain_audit,
        "cross_surface_consistency": cross_surface,
        "stale_repair_review": stale,
    }
    stamp = run_id or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root / stamp
    if run_dir.exists():
        raise SystemExit(f"audit output directory already exists: {run_dir}")
    report = build_markdown_report(summary, payloads)
    written = _write_outputs(run_dir, payloads, report)
    return summary, written


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    output_root = (repo_root / args.output_root).resolve() if not Path(args.output_root).is_absolute() else Path(args.output_root)
    summary, written = run_audit(
        repo_root=repo_root,
        output_root=output_root,
        run_id=args.run_id,
        as_of_date=args.as_of_date,
        ytd_year=args.ytd_year,
    )
    print(f"[AUDIT] classification={summary['overall_classification']}")
    for path in written:
        print(f"[AUDIT] wrote {path.relative_to(repo_root)}")
    if args.strict and summary["overall_classification"] != "VERIFIED":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
