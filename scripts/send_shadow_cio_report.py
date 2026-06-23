#!/usr/bin/env python3
"""Send the daily Shadow CIO model scorecard email.

This report is artifact-only. It reads Shadow outputs, formats a plain-English
scorecard, and sends it through the existing email utility.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.strategy_registry import load_strategy_registry  # noqa: E402

_REGISTRY = load_strategy_registry()
MODEL_ORDER = [*_REGISTRY.active_shadow_security_selection_ids(), "spy_benchmark"]
ESTABLISHED_MODEL_ORDER = [
    *(
        entry.strategy_id
        for entry in _REGISTRY.active_shadow_security_selection_entries()
        if not (entry.shadow_tracking or {}).get("baseline_strategy_id")
    ),
    "spy_benchmark",
]
PROMOTION_SLUGS = list(reversed(_REGISTRY.promotion_candidate_ids()))
BASELINE_SLUG = _REGISTRY.baseline_strategy_id()
BENCHMARK_SLUG = "spy_benchmark"
DISPLAY_NAMES = {
    entry.strategy_id: entry.display_name.replace("Caerus ", "")
    for entry in _REGISTRY.active_shadow_security_selection_entries()
} | {
    "spy_benchmark": "SPY",
}


@dataclass(frozen=True)
class ModelSnapshot:
    slug: str
    name: str
    daily_return: float | None
    seven_day_return: float | None
    seven_day_start_date: str | None
    seven_day_end_date: str | None
    period_return: float | None
    period_label: str
    period_start_date: str | None
    period_end_date: str | None
    excess_vs_spy_period: float | None
    valid_day_count: int | None
    data_status: str | None
    data_reason: str | None


@dataclass(frozen=True)
class ShadowCioReport:
    trade_date: str
    as_of_date: str
    subject: str
    body: str
    models: list[ModelSnapshot]
    data_health: str
    data_health_reason: str
    latest_source_path: str
    latest_source_date: str | None
    requested_report_date: str
    latest_source_warning: str | None


@dataclass(frozen=True)
class PeriodReturn:
    value: float | None
    label: str
    start_date: str | None
    end_date: str | None


@dataclass(frozen=True)
class PerformanceIntegrity:
    status: str
    reason_code: str | None
    detail: str
    offending_date: str | None = None


def _load_dotenv(repo_root: Path) -> None:
    env_path = repo_root / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fmt_pct(value: float | None, *, stale: bool = False) -> str:
    if value is None:
        return "N/A (stale)" if stale else "N/A"
    return f"{value:+.2%}"


def _load_nav_history(path: Path) -> dict[str, list[tuple[str, float]]]:
    history: dict[str, list[tuple[str, float]]] = {slug: [] for slug in MODEL_ORDER}
    if not path.exists():
        return history
    try:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                date = str(row.get("date") or "")
                if not date:
                    continue
                for slug in MODEL_ORDER:
                    value = _as_float(row.get(slug))
                    if value is not None:
                        history.setdefault(slug, []).append((date, value))
    except Exception:
        return {slug: [] for slug in MODEL_ORDER}
    return history


def assess_nav_history_integrity(nav_history: dict[str, list[tuple[str, float]]]) -> PerformanceIntegrity:
    populated = {slug: points for slug, points in nav_history.items() if points}
    if not populated:
        return PerformanceIntegrity("INSUFFICIENT_EVIDENCE", "SHADOW_PERFORMANCE_SUPPRESSED", "shadow_nav_series.csv has no usable rows")
    missing = [slug for slug in ESTABLISHED_MODEL_ORDER if not nav_history.get(slug)]
    if missing:
        return PerformanceIntegrity("CORRUPT", "SHADOW_NAV_SCHEMA_MISMATCH", f"missing NAV history for: {', '.join(missing)}")
    common_dates = sorted(set.intersection(*(set(date for date, _ in nav_history[slug]) for slug in ESTABLISHED_MODEL_ORDER)))
    if len(common_dates) < 2:
        return PerformanceIntegrity("INSUFFICIENT_EVIDENCE", "SHADOW_PERFORMANCE_SUPPRESSED", "fewer than two common NAV dates")
    values = {slug: dict(nav_history[slug]) for slug in ESTABLISHED_MODEL_ORDER}
    for previous_date, current_date in zip(common_dates, common_dates[1:]):
        reset_slugs: list[str] = []
        mismatch_slugs: list[str] = []
        for slug in ESTABLISHED_MODEL_ORDER:
            prior = values[slug].get(previous_date)
            current = values[slug].get(current_date)
            if prior is None or current is None or prior <= 0 or current <= 0:
                mismatch_slugs.append(slug)
                continue
            ratio = current / prior
            if ratio < 0.5 or ratio > 1.5:
                reset_slugs.append(slug)
        if mismatch_slugs:
            return PerformanceIntegrity(
                "CORRUPT",
                "SHADOW_NAV_SCHEMA_MISMATCH",
                f"non-positive or missing NAV value on {current_date}: {', '.join(mismatch_slugs)}",
                current_date,
            )
        if len(reset_slugs) >= 2:
            return PerformanceIntegrity(
                "CORRUPT",
                "SHADOW_NAV_CHAIN_RESET",
                f"simultaneous implausible NAV ratio on {current_date}: {', '.join(reset_slugs)}",
                current_date,
            )
    return PerformanceIntegrity("OK", None, "shadow_nav_series.csv continuity checks passed")


def _latest_valid_shadow_performance_date(nav_history: dict[str, list[tuple[str, float]]]) -> str | None:
    date_sets = [set(points_date for points_date, _ in nav_history.get(slug, [])) for slug in ESTABLISHED_MODEL_ORDER]
    if not date_sets or any(not dates for dates in date_sets):
        return None
    common_dates = set.intersection(*date_sets)
    return max(common_dates) if common_dates else None


def _daily_return_for_as_of(points: list[tuple[str, float]], as_of_date: str) -> float | None:
    filtered = _filtered_points(points, as_of_date)
    if len(filtered) < 2 or filtered[-1][0] != as_of_date:
        return None
    previous = filtered[-2][1]
    current = filtered[-1][1]
    if previous == 0:
        return None
    return (current / previous) - 1.0


def _return_over_last_valid_days(points: list[tuple[str, float]], days: int = 7) -> PeriodReturn:
    if len(points) < 2:
        return PeriodReturn(None, "7-Day", None, points[-1][0] if points else None)
    window = points[-days:] if len(points) >= days else points
    start = window[0][1]
    end = window[-1][1]
    if start == 0:
        return PeriodReturn(None, "7-Day", window[0][0], window[-1][0])
    return PeriodReturn((end / start) - 1.0, "7-Day", window[0][0], window[-1][0])


def _period_return(points: list[tuple[str, float]], trade_date: str) -> PeriodReturn:
    if len(points) < 2:
        return PeriodReturn(None, "YTD", None, points[-1][0] if points else None)
    year = trade_date[:4]
    ytd_points = [point for point in points if point[0].startswith(year)]
    if len(ytd_points) >= 2:
        window = ytd_points
        first_ytd_date = ytd_points[0][0]
        label = "YTD" if first_ytd_date.startswith(f"{year}-01-") else "Since Observation Inception"
    else:
        window = points
        label = "Since Observation Inception"
    start = window[0][1]
    end = window[-1][1]
    if start == 0:
        return PeriodReturn(None, label, window[0][0], window[-1][0])
    return PeriodReturn((end / start) - 1.0, label, window[0][0], window[-1][0])


def _filtered_points(points: list[tuple[str, float]], trade_date: str) -> list[tuple[str, float]]:
    return [point for point in points if point[0] <= trade_date]


def _strategy_payloads(evaluation: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not evaluation:
        return {}
    strategies = evaluation.get("strategies")
    return strategies if isinstance(strategies, dict) else {}


def _collect_data_reasons(
    *,
    evaluation: dict[str, Any] | None,
    comparison: dict[str, Any] | None,
    hydration_status: dict[str, Any] | None,
    nav_history: dict[str, list[tuple[str, float]]],
    trade_date: str,
    as_of_date: str,
) -> tuple[str, str]:
    reasons: list[str] = []
    if evaluation is None:
        reasons.append("shadow_evaluation.json is missing or unreadable")
    if comparison and comparison.get("reason_code"):
        reasons.append(str(comparison["reason_code"]))
    if comparison and comparison.get("data_reason"):
        reasons.append(str(comparison["data_reason"]))
    if comparison and comparison.get("status") == "NO_DATA" and not reasons:
        reasons.append("NO_DATA")

    for payload in _strategy_payloads(evaluation).values():
        if payload.get("data_status") == "NO_DATA":
            reason = payload.get("data_reason") or "NO_DATA"
            reasons.append(str(reason))

    if as_of_date < trade_date:
        reasons.append("Current trade date not yet available; report uses latest fully available shadow data.")
    if hydration_status:
        shadow_refresh = hydration_status.get("shadow_refresh") or {}
        if shadow_refresh.get("status") not in (None, "OK"):
            refresh_reason = shadow_refresh.get("reason") or shadow_refresh.get("status")
            latest_nav_date = shadow_refresh.get("nav_series_latest_date")
            cache_path = hydration_status.get("canonical_cache_path")
            max_cache_date = hydration_status.get("max_cache_date")
            details = f"shadow refresh blocked: {refresh_reason}"
            if latest_nav_date:
                details += f"; nav_series_latest_date={latest_nav_date}"
            if max_cache_date:
                details += f"; max_cache_date={max_cache_date}"
            if cache_path:
                details += f"; canonical_cache_path={cache_path}"
            reasons.append(details)

    if not reasons:
        return "Fresh", "Shadow artifacts are current."
    unique = list(dict.fromkeys(reasons))
    return "Stale", "; ".join(unique)


def _build_model_snapshots(
    *,
    evaluation: dict[str, Any] | None,
    nav_history: dict[str, list[tuple[str, float]]],
    as_of_date: str,
    suppress_performance: bool = False,
) -> list[ModelSnapshot]:
    strategies = _strategy_payloads(evaluation)
    period_by_slug: dict[str, PeriodReturn] = {}
    seven_by_slug: dict[str, PeriodReturn] = {}
    for slug in MODEL_ORDER:
        points = _filtered_points(nav_history.get(slug, []), as_of_date)
        seven_by_slug[slug] = _return_over_last_valid_days(points)
        period_by_slug[slug] = _period_return(points, as_of_date)

    snapshots: list[ModelSnapshot] = []
    spy_period = period_by_slug.get(BENCHMARK_SLUG)
    spy_period_return = spy_period.value if spy_period else None
    for slug in MODEL_ORDER:
        payload = strategies.get(slug) or {}
        period = period_by_slug.get(slug) or PeriodReturn(None, "YTD", None, None)
        period_return = period.value
        period_label = period.label
        period_start_date = period.start_date
        period_end_date = period.end_date
        if period_return is None:
            period_return = _as_float(payload.get("cumulative_return"))
            period_start_date = as_of_date if period_start_date is None else period_start_date
            period_end_date = as_of_date if period_end_date is None else period_end_date
        seven_day = seven_by_slug.get(slug) or PeriodReturn(None, "7-Day", None, None)
        daily_return = _daily_return_for_as_of(nav_history.get(slug, []), as_of_date)
        if daily_return is None and str((evaluation or {}).get("trade_date") or "") == as_of_date:
            daily_return = _as_float(payload.get("daily_return"))
        if slug == BENCHMARK_SLUG:
            excess_vs_spy = 0.0 if period_return is not None else None
        elif period_return is not None and spy_period_return is not None:
            excess_vs_spy = period_return - spy_period_return
        else:
            excess_vs_spy = _as_float(payload.get("excess_return_vs_spy"))
        snapshots.append(
            ModelSnapshot(
                slug=slug,
                name=DISPLAY_NAMES[slug],
                daily_return=None if suppress_performance else daily_return,
                seven_day_return=None if suppress_performance else seven_day.value,
                seven_day_start_date=seven_day.start_date,
                seven_day_end_date=seven_day.end_date,
                period_return=None if suppress_performance else period_return,
                period_label=period_label,
                period_start_date=period_start_date,
                period_end_date=period_end_date,
                excess_vs_spy_period=None if suppress_performance else excess_vs_spy,
                valid_day_count=_as_int(payload.get("rolling_count_of_valid_days")),
                data_status=payload.get("data_status"),
                data_reason=payload.get("data_reason"),
            )
        )
    return snapshots


def _rankable_models(models: list[ModelSnapshot]) -> list[ModelSnapshot]:
    return sorted(
        [model for model in models if model.period_return is not None],
        key=lambda model: model.period_return if model.period_return is not None else float("-inf"),
        reverse=True,
    )


def _model_by_slug(models: list[ModelSnapshot], slug: str) -> ModelSnapshot | None:
    return next((model for model in models if model.slug == slug), None)


def _promotion_signal(
    model: ModelSnapshot,
    polaris: ModelSnapshot | None,
    spy: ModelSnapshot | None,
    *,
    data_health: str,
    data_health_reason: str,
) -> tuple[str, str]:
    if model.slug == BASELINE_SLUG:
        return "BASELINE", "Paper baseline for comparison."

    valid_days = model.valid_day_count or 0
    gap = (
        model.period_return - polaris.period_return
        if polaris and model.period_return is not None and polaris.period_return is not None
        else None
    )
    excess = (
        model.period_return - spy.period_return
        if spy and model.period_return is not None and spy.period_return is not None
        else model.excess_vs_spy_period
    )
    is_stale = data_health == "Stale" or "PRICE_CACHE_STALE" in data_health_reason
    is_strong = gap is not None and gap > 0 and excess is not None and excess > 0
    if valid_days < 10:
        signal = "NOT_READY"
        if is_strong and is_stale:
            reason = f"Strong {model.period_label}, but only {valid_days} valid days and current data is stale."
        elif is_strong:
            reason = f"Strong {model.period_label}, but only {valid_days} valid days."
        elif is_stale:
            reason = f"Only {valid_days} valid days and current data is stale."
        else:
            reason = f"Only {valid_days} valid days."
        return signal, reason
    if model.period_return is None:
        signal = "NOT_READY"
    elif is_strong and not is_stale:
        signal = "PROMOTE_CANDIDATE"
    else:
        signal = "WATCH"
    if is_strong and is_stale:
        return signal, f"Strong {model.period_label}, but current data is stale."
    reason = (
        f"Gap vs Polaris: {_fmt_pct(gap)}; "
        f"valid days: {valid_days}; "
        f"excess vs SPY: {_fmt_pct(excess)}."
    )
    return signal, reason


def _takeaway(models: list[ModelSnapshot], *, data_health: str, data_health_reason: str) -> str:
    if "corrupt" in data_health.lower():
        return (
            "Shadow performance is unavailable because of artifact corruption. "
            f"Reason: {data_health_reason}. Do not use rankings, promotion signals, "
            "YTD returns, or seven-day returns until the NAV chain is repaired."
        )
    polaris = _model_by_slug(models, BASELINE_SLUG)
    spy = _model_by_slug(models, BENCHMARK_SLUG)
    ranked_candidates = [model for model in _rankable_models(models) if model.slug != BENCHMARK_SLUG]
    if not ranked_candidates:
        return "Shadow performance is not decision-useful yet. Keep monitoring until complete model data is available."
    leader = ranked_candidates[0]
    end_date = leader.period_end_date or "the latest fully available shadow date"
    sentences = [f"Through {end_date}, {leader.name} leads the model set on {leader.period_label} performance."]
    if polaris and spy and polaris.excess_vs_spy_period is not None:
        sentences.append(f"Polaris is {_fmt_pct(polaris.excess_vs_spy_period)} versus SPY {polaris.period_label}.")
    else:
        sentences.append("Promotion decisions should wait for complete Polaris and SPY comparisons.")
    return " ".join(sentences[:2])


def _first_period_model(models: list[ModelSnapshot]) -> ModelSnapshot | None:
    return next((model for model in models if model.period_end_date or model.period_start_date), None)


def _period_header(models: list[ModelSnapshot], report_date: str) -> str:
    model = _first_period_model(models)
    if not model:
        return "YTD"
    header = model.period_label
    if model.period_start_date:
        header = f"{header} (from {model.period_start_date})"
    if model.period_end_date and model.period_end_date != report_date:
        header = f"{header} through {model.period_end_date}"
    return header


def _seven_day_header(models: list[ModelSnapshot], report_date: str) -> str:
    model = next((item for item in models if item.seven_day_end_date), None)
    if not model:
        return "7-Day"
    header = "7-Day"
    if model.seven_day_end_date != report_date:
        header = f"{header} (through {model.seven_day_end_date})"
    return header


def render_email_body(
    report_date: str,
    as_of_date: str,
    models: list[ModelSnapshot],
    data_health: str,
    data_health_reason: str,
    latest_source_path: str,
    latest_source_date: str | None,
    requested_report_date: str,
    latest_source_warning: str | None,
) -> str:
    ranked_all = _rankable_models(models)
    ranked_models = [model for model in ranked_all if model.slug != BENCHMARK_SLUG]
    leader = ranked_models[0] if ranked_models else None
    runner_up = ranked_models[1] if len(ranked_models) > 1 else None
    laggard = ranked_models[-1] if ranked_models else None
    polaris = _model_by_slug(models, BASELINE_SLUG)
    spy = _model_by_slug(models, BENCHMARK_SLUG)
    period_model = _first_period_model(models)
    period_header = _period_header(models, report_date)
    label_source = period_model or leader or polaris
    period_label = label_source.period_label if label_source else "YTD"
    seven_day_header = _seven_day_header(models, report_date)
    period_dates = (
        f"{period_model.period_start_date} through {period_model.period_end_date}"
        if period_model and period_model.period_start_date and period_model.period_end_date
        else "N/A"
    )

    lines: list[str] = [
        "=== DAILY MODEL SCORECARD ===",
        "",
        f"Data through: {as_of_date}",
    ]
    if report_date > as_of_date:
        lines.extend(
            [
                "Current trade date not yet available; report uses latest fully available shadow data.",
            ]
        )
    lines.extend(
        [
            "",
            f"Leader: {leader.name if leader else 'N/A'} ({_fmt_pct(leader.period_return) if leader else 'N/A'} {period_label})",
            f"Runner-up: {runner_up.name if runner_up else 'N/A'} ({_fmt_pct(runner_up.period_return) if runner_up else 'N/A'} {period_label})",
            f"Laggard: {laggard.name if laggard else 'N/A'} ({_fmt_pct(laggard.period_return) if laggard else 'N/A'} {period_label})",
            "",
            "=== PERFORMANCE SNAPSHOT ===",
            "",
            f"Model | Daily | {seven_day_header} | {period_header} | Excess vs SPY ({period_label})",
            "--- | ---: | ---: | ---: | ---:",
        ]
    )
    for slug in MODEL_ORDER:
        model = _model_by_slug(models, slug)
        if model is None:
            continue
        lines.append(
            " | ".join(
                [
                    model.name,
                    _fmt_pct(model.daily_return),
                    _fmt_pct(model.seven_day_return),
                    _fmt_pct(model.period_return),
                    _fmt_pct(model.excess_vs_spy_period),
                ]
            )
        )

    lines.extend(["", "=== RANKING ===", ""])
    performance_corrupt = "corrupt" in data_health.lower()
    if performance_corrupt:
        lines.append("N/A - SHADOW_PERFORMANCE_SUPPRESSED")
    else:
        rank = 1
        for model in ranked_models:
            lines.append(f"{rank}. {model.name} -> {_fmt_pct(model.period_return)} {model.period_label}")
            rank += 1
        if spy:
            lines.append(f"SPY -> {_fmt_pct(spy.period_return)} {spy.period_label}")

    lines.extend(
        [
            "",
            "=== PROMOTION SIGNAL ===",
            "",
            "Advisory research labels only; no promotion, retirement, allocation, or lifecycle action is authorized by this report.",
        ]
    )
    if performance_corrupt:
        lines.append("- N/A: SHADOW_PERFORMANCE_SUPPRESSED - performance is unavailable because of artifact corruption.")
    elif polaris:
        signal, reason = _promotion_signal(polaris, polaris, spy, data_health=data_health, data_health_reason=data_health_reason)
        lines.append(f"- Polaris: {signal} - {reason}")
        for slug in PROMOTION_SLUGS:
            model = _model_by_slug(models, slug)
            if not model:
                continue
            signal, reason = _promotion_signal(model, polaris, spy, data_health=data_health, data_health_reason=data_health_reason)
            lines.append(f"- {model.name}: {signal} - {reason}")

    lines.extend(
        [
            "",
            "=== DATA HEALTH ===",
            "",
            f"- {data_health}",
            f"- {data_health_reason}",
            f"- Latest source path: {latest_source_path}",
            f"- Latest source date: {latest_source_date or 'UNAVAILABLE'}",
            f"- Requested/report date: {requested_report_date}",
            f"- Daily, 7-Day, and {period_label} are anchored to Data through: {as_of_date}.",
            f"- Performance windows use shadow_nav_series.csv over {period_dates}.",
        ]
    )
    if latest_source_warning:
        lines.append(f"- WARNING: {latest_source_warning}")
    lines.extend(
        [
            "",
            "=== CIO TAKEAWAY ===",
            "",
            _takeaway(models, data_health=data_health, data_health_reason=data_health_reason),
        ]
    )
    return "\n".join(lines)


def build_report(repo_root: Path = _REPO_ROOT) -> ShadowCioReport:
    latest_dir = repo_root / "outputs" / "shadow_candidates" / "latest"
    evaluation = _read_json(latest_dir / "shadow_evaluation.json")
    comparison = _read_json(latest_dir / "comparison.json")
    trade_date = (
        str((evaluation or {}).get("trade_date") or (comparison or {}).get("trade_date") or dt.date.today().isoformat())
    )
    latest_source_date = str((evaluation or {}).get("trade_date") or (comparison or {}).get("trade_date") or "") or None
    latest_source_warning = (
        f"latest shadow source date {latest_source_date} is older than requested/report date {trade_date}"
        if latest_source_date and latest_source_date < trade_date
        else None
    )
    nav_history = _load_nav_history(repo_root / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv")
    as_of_date = _latest_valid_shadow_performance_date(nav_history) or trade_date
    integrity = assess_nav_history_integrity(nav_history)
    hydration_status = _read_json(repo_root / "outputs" / "price_hydration" / trade_date / "status.json")
    data_health, data_health_reason = _collect_data_reasons(
        evaluation=evaluation,
        comparison=comparison,
        hydration_status=hydration_status,
        nav_history=nav_history,
        trade_date=trade_date,
        as_of_date=as_of_date,
    )
    suppress_performance = integrity.status == "CORRUPT"
    if suppress_performance:
        data_health = "Fresh but corrupt" if data_health == "Fresh" else f"{data_health} and corrupt"
        data_health_reason = f"{integrity.reason_code}: {integrity.detail}"
    models = _build_model_snapshots(
        evaluation=evaluation,
        nav_history=nav_history,
        as_of_date=as_of_date,
        suppress_performance=suppress_performance,
    )
    subject = f"Caerus Model Scorecard \u2014 {trade_date}"
    body = render_email_body(
        trade_date,
        as_of_date,
        models,
        data_health,
        data_health_reason,
        str(latest_dir.as_posix()),
        latest_source_date,
        trade_date,
        latest_source_warning,
    )
    return ShadowCioReport(
        trade_date=trade_date,
        as_of_date=as_of_date,
        subject=subject,
        body=body,
        models=models,
        data_health=data_health,
        data_health_reason=data_health_reason,
        latest_source_path=str(latest_dir.as_posix()),
        latest_source_date=latest_source_date,
        requested_report_date=trade_date,
        latest_source_warning=latest_source_warning,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send the Shadow CIO model scorecard email.")
    parser.add_argument("--repo-root", default=str(_REPO_ROOT), help="Repository root containing outputs/")
    parser.add_argument("--dry-run", action="store_true", help="Print the email instead of sending it")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    report = build_report(repo_root)
    if args.dry_run:
        print(f"Subject: {report.subject}\n")
        print(report.body)
        return 0

    _load_dotenv(repo_root)
    from core.quant_report import send_email

    send_email(subject=report.subject, body_text=report.body)
    print(f"[SHADOW_CIO_REPORT][OK] sent: {report.subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
