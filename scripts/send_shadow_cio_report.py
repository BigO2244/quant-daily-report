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
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.strategy_registry import load_strategy_registry  # noqa: E402
from paper.trading_calendar import is_trading_day  # noqa: E402

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
# Keep governed PAPER challengers visible in research-only comparison reports;
# execution status and promotion eligibility are separate authority concepts.
PROMOTION_SLUGS = list(reversed(_REGISTRY.research_challenger_ids()))
BASELINE_SLUG = _REGISTRY.baseline_strategy_id()
BENCHMARK_SLUG = "spy_benchmark"
MIN_VALID_DAYS = 10
MAX_NAV_AGE_TRADING_DAYS = int(os.environ.get("SHADOW_CIO_MAX_NAV_AGE_TRADING_DAYS", "2"))
DISPLAY_NAMES = {
    entry.strategy_id: entry.display_name.replace("Caerus ", "")
    for entry in _REGISTRY.active_shadow_security_selection_entries()
} | {
    "spy_benchmark": "SPY",
}


def _render_operating_capital_state(repo_root: Path) -> str:
    payload = _read_json(
        repo_root / "outputs" / "operating_state" / "current" / "operating_truth.json"
    )
    if not payload:
        return "=== CAPITAL LANES ===\nOperating truth unavailable; context is not certified."
    lines = ["=== CAPITAL LANES ==="]
    for lane in payload.get("lanes") or []:
        kind = str(lane.get("lane_kind") or "UNKNOWN")
        if kind not in {"LIVE", "PAPER", "SHADOW"}:
            continue
        strategies = ", ".join(
            str(item).replace("caerus_", "").replace("_", " ").title()
            for item in lane.get("strategy_ids") or []
        ) or "None"
        lines.append(
            f"{kind}: {strategies} — {lane.get('operating_status', 'UNKNOWN')} "
            f"(authority {((lane.get('authority') or {}).get('status') or 'UNKNOWN')})"
        )
    integrity = payload.get("context_integrity") or {}
    lines.append(f"Context integrity: {integrity.get('status', 'UNKNOWN')}")
    return "\n".join(lines)


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
    period_return_source: str | None


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
    performance_evidence: "PerformanceEvidence"
    paper_snapshot: "PaperSnapshot"
    publication_withheld: bool = False
    publication_withheld_reason: str | None = None


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


@dataclass(frozen=True)
class PublicationGate:
    withheld: bool
    reason_code: str | None
    detail: str | None = None


@dataclass(frozen=True)
class PerformanceEvidence:
    status: str
    reason_code: str | None
    detail: str
    previous_date: str | None = None
    current_date: str | None = None
    return_convention: str | None = None
    attribution_by_slug: dict[str, list[dict[str, Any]]] | None = None


@dataclass(frozen=True)
class PaperSnapshot:
    status: str
    reason: str
    source_path: str
    daily_return: float | None = None
    seven_day_return: float | None = None
    period_return: float | None = None
    period_start_date: str | None = None
    period_end_date: str | None = None
    equity: float | None = None


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


def _close_enough(left: float, right: float, *, tolerance: float = 1e-8) -> bool:
    return abs(left - right) <= max(tolerance, tolerance * max(abs(left), abs(right), 1.0))


def assess_dated_performance_integrity(
    *,
    repo_root: Path,
    as_of_date: str,
    nav_history: dict[str, list[tuple[str, float]]],
) -> PerformanceEvidence:
    """Reconcile the published daily return to dated performance and NAV.

    This is intentionally independent of the scorecard's evaluation payload.
    A report cannot publish solely because two derived display artifacts agree.
    """
    path = repo_root / "outputs" / "shadow_candidates" / as_of_date / "shadow_performance.json"
    payload = _read_json(path)
    if payload is None:
        return PerformanceEvidence(
            "CORRUPT",
            "SHADOW_DATED_PERFORMANCE_MISSING",
            f"missing or unreadable {path}",
            current_date=as_of_date,
        )
    if str(payload.get("trade_date") or "") != as_of_date:
        return PerformanceEvidence(
            "CORRUPT",
            "SHADOW_DATED_PERFORMANCE_DATE_MISMATCH",
            f"shadow_performance.trade_date={payload.get('trade_date')}; expected={as_of_date}",
            current_date=as_of_date,
        )
    if payload.get("status") != "OK" or payload.get("data_status") != "OK":
        return PerformanceEvidence(
            "CORRUPT",
            "SHADOW_DATED_PERFORMANCE_NOT_OK",
            f"status={payload.get('status')}; data_status={payload.get('data_status')}; reason={payload.get('data_reason')}",
            previous_date=str(payload.get("previous_trade_date") or "") or None,
            current_date=as_of_date,
            return_convention=str(payload.get("return_convention") or "") or None,
        )

    return_convention = str(payload.get("return_convention") or "")
    if return_convention != "weights_as_of_t":
        return PerformanceEvidence(
            "CORRUPT",
            "SHADOW_RETURN_CONVENTION_INVALID",
            f"return_convention={return_convention or 'missing'}; expected=weights_as_of_t",
            previous_date=str(payload.get("previous_trade_date") or "") or None,
            current_date=as_of_date,
            return_convention=return_convention or None,
        )

    provenance = payload.get("calculation_provenance")
    if isinstance(provenance, dict):
        expected_provenance = {
            "price_field": "close",
            "start_date": str(payload.get("previous_trade_date") or ""),
            "end_date": as_of_date,
            "missing_price_policy": "fail_closed",
        }
        mismatches = [
            f"{key}={provenance.get(key)}"
            for key, expected in expected_provenance.items()
            if provenance.get(key) != expected
        ]
        if mismatches:
            return PerformanceEvidence(
                "CORRUPT",
                "SHADOW_RETURN_PROVENANCE_INVALID",
                "; ".join(mismatches),
                previous_date=str(payload.get("previous_trade_date") or "") or None,
                current_date=as_of_date,
                return_convention=return_convention,
            )

    previous_date = str(payload.get("previous_trade_date") or "") or None
    strategies = payload.get("strategies") if isinstance(payload.get("strategies"), dict) else {}
    attribution_by_slug: dict[str, list[dict[str, Any]]] = {}
    for slug in MODEL_ORDER:
        points = _filtered_points(nav_history.get(slug, []), as_of_date)
        if not points:
            continue
        if len(points) < 2 or points[-1][0] != as_of_date:
            return PerformanceEvidence(
                "CORRUPT",
                "SHADOW_DATED_NAV_MISSING",
                f"{slug} has no complete NAV pair through {as_of_date}",
                previous_date=previous_date,
                current_date=as_of_date,
            )
        strategy = strategies.get(slug) if isinstance(strategies.get(slug), dict) else None
        if strategy is None:
            return PerformanceEvidence(
                "CORRUPT",
                "SHADOW_DATED_STRATEGY_MISSING",
                f"shadow_performance.json missing {slug}",
                previous_date=previous_date,
                current_date=as_of_date,
            )
        numeric = {
            key: _as_float(strategy.get(key))
            for key in ("daily_return", "previous_nav", "nav")
        }
        if any(value is None or not math.isfinite(value) for value in numeric.values()):
            return PerformanceEvidence(
                "CORRUPT",
                "SHADOW_DATED_PERFORMANCE_NONFINITE",
                f"{slug} has missing or non-finite daily_return/previous_nav/nav",
                previous_date=previous_date,
                current_date=as_of_date,
            )
        prior_date, prior_nav = points[-2]
        current_date, current_nav = points[-1]
        if previous_date != prior_date:
            return PerformanceEvidence(
                "CORRUPT",
                "SHADOW_DATED_PRIOR_DATE_MISMATCH",
                f"{slug} NAV prior date={prior_date}; shadow_performance.previous_trade_date={previous_date}",
                previous_date=previous_date,
                current_date=current_date,
            )
        reported_return = float(numeric["daily_return"])
        expected_return = (current_nav / prior_nav) - 1.0
        if not (
            _close_enough(float(numeric["previous_nav"]), prior_nav)
            and _close_enough(float(numeric["nav"]), current_nav)
            and _close_enough(reported_return, expected_return)
        ):
            return PerformanceEvidence(
                "CORRUPT",
                "SHADOW_DATED_PERFORMANCE_RECONCILIATION_FAILED",
                f"{slug} dated performance does not reconcile to canonical NAV",
                previous_date=previous_date,
                current_date=current_date,
            )
        attribution = strategy.get("daily_attribution")
        if "daily_attribution" in strategy and (not isinstance(attribution, list) or not attribution):
            return PerformanceEvidence(
                "CORRUPT",
                "SHADOW_DAILY_ATTRIBUTION_MISSING",
                f"{slug} has an empty or invalid daily_attribution field",
                previous_date=previous_date,
                current_date=current_date,
            )
        if isinstance(attribution, list) and attribution:
            contribution_sum = 0.0
            weight_sum = 0.0
            clean_rows: list[dict[str, Any]] = []
            tickers_seen: set[str] = set()
            for row in attribution:
                if not isinstance(row, dict):
                    return PerformanceEvidence(
                        "CORRUPT",
                        "SHADOW_DAILY_ATTRIBUTION_INVALID",
                        f"{slug} contains a non-object attribution row",
                        previous_date=previous_date,
                        current_date=current_date,
                    )
                ticker = str(row.get("ticker") or "")
                weight = _as_float(row.get("target_weight"))
                ticker_return = _as_float(row.get("close_to_close_return"))
                contribution = _as_float(row.get("contribution"))
                if (
                    not ticker
                    or ticker in tickers_seen
                    or any(value is None or not math.isfinite(value) for value in (weight, ticker_return, contribution))
                    or float(weight) < 0.0
                ):
                    return PerformanceEvidence(
                        "CORRUPT",
                        "SHADOW_DAILY_ATTRIBUTION_INVALID",
                        f"{slug} has a missing/duplicate ticker, invalid weight, or non-finite attribution",
                        previous_date=previous_date,
                        current_date=current_date,
                    )
                tickers_seen.add(ticker)
                if not _close_enough(float(contribution), float(weight) * float(ticker_return)):
                    return PerformanceEvidence(
                        "CORRUPT",
                        "SHADOW_DAILY_ATTRIBUTION_RECONCILIATION_FAILED",
                        f"{slug} contribution does not equal weight times return for {row.get('ticker')}",
                        previous_date=previous_date,
                        current_date=current_date,
                    )
                contribution_sum += float(contribution)
                weight_sum += float(weight)
                clean_rows.append(dict(row))
            reported_gross = _as_float(strategy.get("gross_exposure"))
            reported_cash = _as_float(strategy.get("cash_weight"))
            if (
                reported_gross is None
                or reported_cash is None
                or not math.isfinite(reported_gross)
                or not math.isfinite(reported_cash)
                or weight_sum > 1.0 + 1e-8
                or not _close_enough(weight_sum, reported_gross)
                or not _close_enough(max(0.0, 1.0 - weight_sum), reported_cash)
                or not _close_enough(contribution_sum, reported_return)
            ):
                return PerformanceEvidence(
                    "CORRUPT",
                    "SHADOW_DAILY_ATTRIBUTION_RECONCILIATION_FAILED",
                    f"{slug} attribution sum or gross exposure does not reconcile",
                    previous_date=previous_date,
                    current_date=current_date,
                )
            attribution_by_slug[slug] = clean_rows

    return PerformanceEvidence(
        "OK",
        None,
        "dated shadow performance reconciles to canonical NAV"
        + (" and per-position attribution" if attribution_by_slug else " (legacy artifact has no per-position attribution)"),
        previous_date=previous_date,
        current_date=as_of_date,
        return_convention=return_convention,
        attribution_by_slug=attribution_by_slug,
    )


def _load_paper_snapshot(
    *,
    repo_root: Path,
    as_of_date: str,
    period_start_date: str | None,
) -> PaperSnapshot:
    path = repo_root / "outputs" / "perf" / "live_overlay_nav_series.csv"
    if not path.exists():
        return PaperSnapshot("UNAVAILABLE", "broker-derived Paper NAV series is missing", str(path))
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = [row for row in csv.DictReader(handle) if str(row.get("date") or "") <= as_of_date]
    except Exception as exc:
        return PaperSnapshot("CORRUPT", f"unable to read Paper NAV series: {exc}", str(path))
    parsed: list[tuple[str, float, float | None]] = []
    for row in rows:
        date = str(row.get("date") or "")
        equity = _as_float(row.get("equity"))
        reported_return = _as_float(row.get("return_1d"))
        if date and equity is not None and math.isfinite(equity) and equity > 0:
            parsed.append((date, equity, reported_return))
    dates = [row[0] for row in parsed]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        return PaperSnapshot("CORRUPT", "Paper NAV dates are duplicate or out of order", str(path))
    if len(parsed) < 2 or parsed[-1][0] != as_of_date:
        return PaperSnapshot("UNAVAILABLE", f"Paper NAV does not contain {as_of_date}", str(path))
    for previous, current in zip(parsed, parsed[1:]):
        expected = current[1] / previous[1] - 1.0
        if current[2] is None or not math.isfinite(current[2]) or not _close_enough(current[2], expected):
            return PaperSnapshot(
                "CORRUPT",
                f"Paper return_1d does not reconcile to equity on {current[0]}",
                str(path),
            )
    # Seven trading-day returns require eight closing equity observations.
    # Using seven observations silently reports only six return intervals.
    window = parsed[-8:] if len(parsed) >= 8 else []
    period_rows = [row for row in parsed if period_start_date is None or row[0] >= period_start_date]
    if len(period_rows) < 2:
        return PaperSnapshot("UNAVAILABLE", "Paper NAV lacks the common comparison window", str(path))
    return PaperSnapshot(
        "OK",
        "broker-derived Paper equity chain reconciles",
        str(path),
        daily_return=parsed[-1][2],
        seven_day_return=(window[-1][1] / window[0][1]) - 1.0 if window else None,
        period_return=(period_rows[-1][1] / period_rows[0][1]) - 1.0,
        period_start_date=period_rows[0][0],
        period_end_date=period_rows[-1][0],
        equity=parsed[-1][1],
    )


def assess_nav_history_integrity(nav_history: dict[str, list[tuple[str, float]]]) -> PerformanceIntegrity:
    populated = {slug: points for slug, points in nav_history.items() if points}
    if not populated:
        return PerformanceIntegrity("INSUFFICIENT_EVIDENCE", "SHADOW_PERFORMANCE_SUPPRESSED", "shadow_nav_series.csv has no usable rows")
    for slug, points in populated.items():
        dates = [date for date, _ in points]
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            return PerformanceIntegrity(
                "CORRUPT",
                "SHADOW_NAV_DATE_ORDER_INVALID",
                f"{slug} has duplicate or out-of-order NAV dates",
            )
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
    observations_required = days + 1
    if len(points) < observations_required:
        return PeriodReturn(None, "7-Day", None, points[-1][0] if points else None)
    window = points[-observations_required:]
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


def _trading_day_lag(as_of_date: str, trade_date: str) -> int | None:
    try:
        start = dt.date.fromisoformat(as_of_date)
        end = dt.date.fromisoformat(trade_date)
    except ValueError:
        return None
    if start >= end:
        return 0

    lag = 0
    current = start + dt.timedelta(days=1)
    while current <= end:
        if is_trading_day(current.isoformat()):
            lag += 1
        current += dt.timedelta(days=1)
    return lag


def _publication_gate(
    *,
    hydration_status: dict[str, Any] | None,
    as_of_date: str,
    trade_date: str,
    integrity: PerformanceIntegrity,
    performance_evidence: PerformanceEvidence,
) -> PublicationGate:
    if integrity.status == "CORRUPT":
        return PublicationGate(True, "ARTIFACT_CORRUPT", f"{integrity.reason_code}: {integrity.detail}")
    if performance_evidence.status != "OK":
        return PublicationGate(
            True,
            "DAILY_RETURN_UNRECONCILED",
            f"{performance_evidence.reason_code}: {performance_evidence.detail}",
        )

    shadow_refresh = (hydration_status or {}).get("shadow_refresh") or {}
    refresh_status = shadow_refresh.get("status")
    if refresh_status not in (None, "OK"):
        refresh_reason = shadow_refresh.get("reason") or refresh_status
        return PublicationGate(True, "FAILED_REFRESH", str(refresh_reason))

    nav_lag = _trading_day_lag(as_of_date, trade_date)
    if nav_lag is not None and nav_lag > MAX_NAV_AGE_TRADING_DAYS:
        return PublicationGate(
            True,
            "NAV_STALE",
            f"latest valid NAV date lags requested report date by {nav_lag} trading days",
        )

    return PublicationGate(False, None)


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
        period_return_source = "nav" if period_return is not None else None
        if period_return is None:
            period_return = _as_float(payload.get("cumulative_return"))
            period_return_source = "fallback" if period_return is not None else None
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
                period_return_source=None if suppress_performance else period_return_source,
            )
        )
    return snapshots


def _rankable_models(models: list[ModelSnapshot]) -> list[ModelSnapshot]:
    return sorted(
        [
            model
            for model in models
            if model.period_return is not None
            and model.period_return_source == "nav"
            and model.valid_day_count is not None
            and model.valid_day_count >= MIN_VALID_DAYS
        ],
        key=lambda model: model.period_return if model.period_return is not None else float("-inf"),
        reverse=True,
    )


def _model_by_slug(models: list[ModelSnapshot], slug: str) -> ModelSnapshot | None:
    return next((model for model in models if model.slug == slug), None)


def _load_canonical_promotion_readiness(
    repo_root: Path,
    as_of_date: str,
    evaluation: dict[str, Any] | None,
) -> dict[str, Any] | None:
    payload = _read_json(
        repo_root / "outputs" / "shadow_candidates" / as_of_date / "promotion_readiness.json"
    )
    if not payload or str(payload.get("trade_date") or "") != as_of_date:
        return None
    if payload.get("governance_label") != "RESEARCH_ONLY":
        return None
    readiness_strategies = payload.get("strategies") if isinstance(payload.get("strategies"), dict) else {}
    evaluation_strategies = (
        evaluation.get("strategies")
        if isinstance((evaluation or {}).get("strategies"), dict)
        else {}
    )
    baseline_cumulative = _as_float((evaluation_strategies.get(BASELINE_SLUG) or {}).get("cumulative_return"))
    spy_cumulative = _as_float((evaluation_strategies.get(BENCHMARK_SLUG) or {}).get("cumulative_return"))
    reconciled_strategies: dict[str, Any] = {}
    for slug in PROMOTION_SLUGS:
        readiness = readiness_strategies.get(slug) if isinstance(readiness_strategies.get(slug), dict) else None
        evaluated = evaluation_strategies.get(slug) if isinstance(evaluation_strategies.get(slug), dict) else None
        if readiness is None or evaluated is None:
            continue
        readiness_valid = _as_int(readiness.get("valid_observation_windows"))
        evaluation_valid = _as_int(evaluated.get("rolling_count_of_valid_days"))
        candidate_cumulative = _as_float(evaluated.get("cumulative_return"))
        readiness_vs_polaris = _as_float(readiness.get("cumulative_excess_vs_polaris"))
        readiness_vs_spy = _as_float(readiness.get("cumulative_excess_vs_spy"))
        reconciled = not (
            readiness_valid is None
            or evaluation_valid is None
            or readiness_valid != evaluation_valid
            or candidate_cumulative is None
            or baseline_cumulative is None
            or spy_cumulative is None
            or readiness_vs_polaris is None
            or readiness_vs_spy is None
            or not _close_enough(readiness_vs_polaris, candidate_cumulative - baseline_cumulative)
            or not _close_enough(readiness_vs_spy, candidate_cumulative - spy_cumulative)
        )
        if reconciled:
            reconciled_strategies[slug] = readiness
    if not reconciled_strategies:
        return None
    return {**payload, "strategies": reconciled_strategies}


def _promotion_signal(
    model: ModelSnapshot,
    *,
    promotion_readiness: dict[str, Any] | None,
) -> tuple[str, str]:
    if model.slug == BASELINE_SLUG:
        return "SHADOW_BASELINE", "Hypothetical research control; this is not the broker-held Paper account."
    strategies = (
        promotion_readiness.get("strategies")
        if isinstance((promotion_readiness or {}).get("strategies"), dict)
        else {}
    )
    readiness = strategies.get(model.slug) if isinstance(strategies.get(model.slug), dict) else None
    if readiness is None:
        return "NOT_READY", "Canonical dated promotion-readiness evidence is unavailable."
    state = str(readiness.get("readiness_state") or "NOT_READY")
    confidence = str(readiness.get("confidence") or "UNAVAILABLE")
    reasons = [str(reason) for reason in (readiness.get("reason_codes") or [])]
    reason_text = ", ".join(reasons) if reasons else "no blocking reason codes"
    return state, f"Confidence: {confidence}; evidence: {reason_text}."


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
    performance_evidence: PerformanceEvidence,
    paper_snapshot: PaperSnapshot,
    promotion_readiness: dict[str, Any] | None,
    comparison: dict[str, Any] | None,
    publication_withheld_reason: str | None = None,
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
    broker_context = (comparison or {}).get("broker_context") if isinstance((comparison or {}).get("broker_context"), dict) else {}
    strategy_overlap = broker_context.get("strategy_overlap") if isinstance(broker_context.get("strategy_overlap"), dict) else {}
    baseline_overlap = strategy_overlap.get(BASELINE_SLUG) if isinstance(strategy_overlap.get(BASELINE_SLUG), dict) else {}
    overlap_count = _as_int(baseline_overlap.get("overlap_names_count"))
    paper_position_count = _as_int(broker_context.get("positions_count"))
    overlap_detail = (
        f"Shadow Polaris/Paper holdings overlap: {overlap_count} shared names out of {paper_position_count} Paper positions."
        if overlap_count is not None and paper_position_count is not None
        else "Shadow/Paper holdings overlap is unavailable; performance is not holdings-equivalent."
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
    if publication_withheld_reason:
        lines.extend(
            [
                "",
                "=== MODEL SCORECARD: PUBLICATION WITHHELD ===",
                f"Reason: {publication_withheld_reason}",
                f"Latest valid NAV date: {as_of_date}",
                f"Requested report date: {requested_report_date}",
                "No rankings, promotion signals, or CIO performance conclusions were generated.",
                "Action: investigate upstream shadow refresh (scripts/refresh_shadow_scorecard_artifacts.py).",
            ]
        )
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
                f"- Performance publication: WITHHELD ({publication_withheld_reason})",
                f"- Daily-return reconciliation: {performance_evidence.status} ({performance_evidence.detail})",
            ]
        )
        if latest_source_warning:
            lines.append(f"- WARNING: {latest_source_warning}")
        lines.extend(
            [
                "",
                "=== CIO TAKEAWAY ===",
                "",
                "Publication withheld. No CIO performance conclusions were generated.",
            ]
        )
        return "\n".join(lines)
    lines.extend(
        [
            "",
            "=== RETURN SCOPE ===",
            "",
            "SHADOW HYPOTHETICAL: model returns are not Paper account P&L and do not represent broker-held positions.",
            (
                f"Daily window: {performance_evidence.previous_date} close through {performance_evidence.current_date} close; "
                "target weights stamped on the ending session; residual cash earns 0%."
            ),
            f"Daily-return reconciliation: {performance_evidence.status} - {performance_evidence.detail}.",
            overlap_detail,
            "",
            f"Leader: {leader.name if leader else 'N/A'} ({_fmt_pct(leader.period_return) if leader else 'N/A'} {period_label})",
            f"Runner-up: {runner_up.name if runner_up else 'N/A'} ({_fmt_pct(runner_up.period_return) if runner_up else 'N/A'} {period_label})",
            f"Laggard: {laggard.name if laggard else 'N/A'} ({_fmt_pct(laggard.period_return) if laggard else 'N/A'} {period_label})",
            "",
            "=== SHADOW MODEL PERFORMANCE (HYPOTHETICAL) ===",
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

    attribution_by_slug = performance_evidence.attribution_by_slug or {}
    material_models = [
        model
        for model in models
        if model.slug != BENCHMARK_SLUG
        and model.daily_return is not None
        and abs(model.daily_return) >= 0.05
        and attribution_by_slug.get(model.slug)
    ]
    if material_models:
        lines.extend(["", "=== MATERIAL DAILY-MOVE ATTRIBUTION ===", ""])
        for model in material_models:
            top = attribution_by_slug[model.slug][:5]
            drivers = ", ".join(
                f"{row.get('ticker')} {_fmt_pct(_as_float(row.get('contribution')))}"
                for row in top
            )
            lines.append(f"- {model.name} {_fmt_pct(model.daily_return)}: {drivers}.")

    lines.extend(["", "=== PAPER ACCOUNT PERFORMANCE (BROKER-DERIVED) ===", ""])
    if paper_snapshot.status == "OK":
        lines.extend(
            [
                f"Paper | {_fmt_pct(paper_snapshot.daily_return)} | {_fmt_pct(paper_snapshot.seven_day_return)} | "
                f"{_fmt_pct(paper_snapshot.period_return)} ({paper_snapshot.period_start_date} through {paper_snapshot.period_end_date})",
                f"Paper equity: ${paper_snapshot.equity:,.2f}",
                overlap_detail,
                "Comparison is informational only because Shadow portfolios and the executed Paper portfolio hold different securities and cash levels.",
            ]
        )
    else:
        lines.append(f"Paper performance unavailable: {paper_snapshot.status} - {paper_snapshot.reason}.")

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
            "=== CANONICAL PROMOTION READINESS (RESEARCH ONLY) ===",
            "",
            "This section reads the dated promotion-readiness artifact; the scorecard does not infer promotion from headline returns.",
            "No promotion, retirement, allocation, or lifecycle action is authorized by this report.",
        ]
    )
    if performance_corrupt:
        lines.append("- N/A: SHADOW_PERFORMANCE_SUPPRESSED - performance is unavailable because of artifact corruption.")
    elif polaris:
        signal, reason = _promotion_signal(polaris, promotion_readiness=promotion_readiness)
        lines.append(f"- Polaris: {signal} - {reason}")
        for slug in PROMOTION_SLUGS:
            model = _model_by_slug(models, slug)
            if not model:
                continue
            signal, reason = _promotion_signal(model, promotion_readiness=promotion_readiness)
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
            f"- Dated daily-return reconciliation: {performance_evidence.status} ({performance_evidence.detail}).",
            f"- Paper NAV source: {paper_snapshot.source_path}; status={paper_snapshot.status}.",
            f"- Canonical promotion readiness: {'RECONCILED' if promotion_readiness else 'UNAVAILABLE_OR_UNRECONCILED'}.",
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
    if paper_snapshot.status == "OK" and leader and paper_snapshot.period_return is not None:
        lines.append(
            f"Over the common window, broker-derived Paper returned {_fmt_pct(paper_snapshot.period_return)} versus "
            f"{leader.name} {_fmt_pct(leader.period_return)}, but the portfolios are not holdings-equivalent."
        )
    readiness_strategies = (
        promotion_readiness.get("strategies")
        if isinstance((promotion_readiness or {}).get("strategies"), dict)
        else {}
    )
    if leader and isinstance(readiness_strategies.get(leader.slug), dict):
        lines.append(
            f"Canonical readiness for {leader.name}: {readiness_strategies[leader.slug].get('readiness_state')}; "
            "a strong single session is not a promotion decision."
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
    dated_dir = repo_root / "outputs" / "shadow_candidates" / as_of_date
    as_of_evaluation = _read_json(dated_dir / "shadow_evaluation.json") or evaluation
    as_of_comparison = _read_json(dated_dir / "comparison.json") or comparison
    performance_evidence = assess_dated_performance_integrity(
        repo_root=repo_root,
        as_of_date=as_of_date,
        nav_history=nav_history,
    )
    hydration_status = _read_json(repo_root / "outputs" / "price_hydration" / trade_date / "status.json")
    data_health, data_health_reason = _collect_data_reasons(
        evaluation=as_of_evaluation,
        comparison=as_of_comparison,
        hydration_status=hydration_status,
        nav_history=nav_history,
        trade_date=trade_date,
        as_of_date=as_of_date,
    )
    suppress_performance = integrity.status == "CORRUPT" or performance_evidence.status != "OK"
    if suppress_performance:
        data_health = "Fresh but corrupt" if data_health == "Fresh" else f"{data_health} and corrupt"
        if integrity.status == "CORRUPT":
            data_health_reason = f"{integrity.reason_code}: {integrity.detail}"
        else:
            data_health_reason = f"{performance_evidence.reason_code}: {performance_evidence.detail}"
    publication_gate = _publication_gate(
        hydration_status=hydration_status,
        as_of_date=as_of_date,
        trade_date=trade_date,
        integrity=integrity,
        performance_evidence=performance_evidence,
    )
    models = _build_model_snapshots(
        evaluation=as_of_evaluation,
        nav_history=nav_history,
        as_of_date=as_of_date,
        suppress_performance=suppress_performance,
    )
    period_model = _first_period_model(models)
    paper_snapshot = _load_paper_snapshot(
        repo_root=repo_root,
        as_of_date=as_of_date,
        period_start_date=period_model.period_start_date if period_model else None,
    )
    promotion_readiness = _load_canonical_promotion_readiness(repo_root, as_of_date, as_of_evaluation)
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
        performance_evidence,
        paper_snapshot,
        promotion_readiness,
        as_of_comparison,
        publication_gate.reason_code if publication_gate.withheld else None,
    )
    body = f"{_render_operating_capital_state(repo_root)}\n\n{body}"
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
        performance_evidence=performance_evidence,
        paper_snapshot=paper_snapshot,
        publication_withheld=publication_gate.withheld,
        publication_withheld_reason=publication_gate.reason_code,
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
