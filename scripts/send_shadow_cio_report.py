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

MODEL_ORDER = ["caerus_polaris", "caerus_orion", "caerus_lyra", "spy_benchmark"]
PROMOTION_SLUGS = ["caerus_lyra", "caerus_orion"]
BASELINE_SLUG = "caerus_polaris"
BENCHMARK_SLUG = "spy_benchmark"
DISPLAY_NAMES = {
    "caerus_polaris": "Polaris",
    "caerus_orion": "Orion",
    "caerus_lyra": "Lyra",
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
    subject: str
    body: str
    models: list[ModelSnapshot]
    data_health: str
    data_health_reason: str


@dataclass(frozen=True)
class PeriodReturn:
    value: float | None
    label: str
    start_date: str | None
    end_date: str | None


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
        label = "YTD"
    else:
        window = points
        label = "Since Shadow Inception"
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
    nav_history: dict[str, list[tuple[str, float]]],
    trade_date: str,
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

    latest_nav_dates = [points[-1][0] for points in nav_history.values() if points]
    latest_nav_date = max(latest_nav_dates) if latest_nav_dates else None
    if latest_nav_date and latest_nav_date < trade_date:
        reasons.append("Current trade date missing; results reflect last valid data")

    if not reasons:
        return "Fresh", "Shadow artifacts are current."
    unique = list(dict.fromkeys(reasons))
    return "Stale", "; ".join(unique)


def _build_model_snapshots(
    *,
    evaluation: dict[str, Any] | None,
    nav_history: dict[str, list[tuple[str, float]]],
    trade_date: str,
    stale_daily: bool,
) -> list[ModelSnapshot]:
    strategies = _strategy_payloads(evaluation)
    period_by_slug: dict[str, PeriodReturn] = {}
    seven_by_slug: dict[str, PeriodReturn] = {}
    for slug in MODEL_ORDER:
        points = _filtered_points(nav_history.get(slug, []), trade_date)
        seven_by_slug[slug] = _return_over_last_valid_days(points)
        period_by_slug[slug] = _period_return(points, trade_date)

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
            period_start_date = trade_date if period_start_date is None else period_start_date
            period_end_date = trade_date if period_end_date is None else period_end_date
        seven_day = seven_by_slug.get(slug) or PeriodReturn(None, "7-Day", None, None)
        daily_return = _as_float(payload.get("daily_return"))
        if stale_daily or payload.get("data_status") == "NO_DATA":
            daily_return = None
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
                daily_return=daily_return,
                seven_day_return=seven_day.value,
                seven_day_start_date=seven_day.start_date,
                seven_day_end_date=seven_day.end_date,
                period_return=period_return,
                period_label=period_label,
                period_start_date=period_start_date,
                period_end_date=period_end_date,
                excess_vs_spy_period=excess_vs_spy,
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


def _promotion_signal(model: ModelSnapshot, polaris: ModelSnapshot | None, spy: ModelSnapshot | None) -> tuple[str, str]:
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
    if valid_days < 7 or model.period_return is None:
        signal = "NOT_READY"
    elif gap is not None and gap > 0 and excess is not None and excess > 0:
        signal = "PROMOTE_CANDIDATE"
    else:
        signal = "WATCH"
    reason = (
        f"Gap vs Polaris: {_fmt_pct(gap)}; "
        f"valid days: {valid_days}; "
        f"excess vs SPY: {_fmt_pct(excess)}."
    )
    return signal, reason


def _takeaway(models: list[ModelSnapshot]) -> str:
    polaris = _model_by_slug(models, BASELINE_SLUG)
    spy = _model_by_slug(models, BENCHMARK_SLUG)
    ranked_candidates = [model for model in _rankable_models(models) if model.slug != BENCHMARK_SLUG]
    if not ranked_candidates:
        return "Shadow performance is not decision-useful yet. Keep monitoring until fresh model data is available."
    leader = ranked_candidates[0]
    sentences = [f"{leader.name} leads the model set on {leader.period_label} performance."]
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


def render_email_body(report_date: str, models: list[ModelSnapshot], data_health: str, data_health_reason: str) -> str:
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
        f"Leader: {leader.name if leader else 'N/A'} ({_fmt_pct(leader.period_return) if leader else 'N/A'} {period_label})",
        f"Runner-up: {runner_up.name if runner_up else 'N/A'} ({_fmt_pct(runner_up.period_return) if runner_up else 'N/A'} {period_label})",
        f"Laggard: {laggard.name if laggard else 'N/A'} ({_fmt_pct(laggard.period_return) if laggard else 'N/A'} {period_label})",
        "",
        "=== PERFORMANCE SNAPSHOT ===",
        "",
        f"Model | Daily | {seven_day_header} | {period_header} | Excess vs SPY ({period_label})",
        "--- | ---: | ---: | ---: | ---:",
    ]
    for slug in MODEL_ORDER:
        model = _model_by_slug(models, slug)
        if model is None:
            continue
        daily_stale = model.daily_return is None and (model.data_status == "NO_DATA" or data_health == "Stale")
        lines.append(
            " | ".join(
                [
                    model.name,
                    _fmt_pct(model.daily_return, stale=daily_stale),
                    _fmt_pct(model.seven_day_return),
                    _fmt_pct(model.period_return),
                    _fmt_pct(model.excess_vs_spy_period),
                ]
            )
        )

    lines.extend(["", "=== RANKING ===", ""])
    rank = 1
    for model in ranked_models:
        lines.append(f"{rank}. {model.name} -> {_fmt_pct(model.period_return)} {model.period_label}")
        rank += 1
    if spy:
        lines.append(f"SPY -> {_fmt_pct(spy.period_return)} {spy.period_label}")

    lines.extend(["", "=== PROMOTION SIGNAL ===", ""])
    if polaris:
        signal, reason = _promotion_signal(polaris, polaris, spy)
        lines.append(f"- Polaris: {signal} - {reason}")
    for slug in PROMOTION_SLUGS:
        model = _model_by_slug(models, slug)
        if not model:
            continue
        signal, reason = _promotion_signal(model, polaris, spy)
        lines.append(f"- {model.name}: {signal} - {reason}")

    lines.extend(
        [
            "",
            "=== DATA HEALTH ===",
            "",
            f"- {data_health}",
            f"- {data_health_reason}",
            f"- Daily uses latest shadow_evaluation.json for {report_date}.",
            f"- 7-Day and {period_label} use shadow_nav_series.csv over {period_dates}.",
            "",
            "=== CIO TAKEAWAY ===",
            "",
            _takeaway(models),
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
    nav_history = _load_nav_history(repo_root / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv")
    data_health, data_health_reason = _collect_data_reasons(
        evaluation=evaluation,
        comparison=comparison,
        nav_history=nav_history,
        trade_date=trade_date,
    )
    stale_daily = data_health == "Stale" and "PRICE_CACHE_STALE" in data_health_reason
    models = _build_model_snapshots(
        evaluation=evaluation,
        nav_history=nav_history,
        trade_date=trade_date,
        stale_daily=stale_daily,
    )
    subject = f"Caerus Model Scorecard \u2014 {trade_date}"
    body = render_email_body(trade_date, models, data_health, data_health_reason)
    return ShadowCioReport(
        trade_date=trade_date,
        subject=subject,
        body=body,
        models=models,
        data_health=data_health,
        data_health_reason=data_health_reason,
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
