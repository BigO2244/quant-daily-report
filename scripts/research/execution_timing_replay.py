"""Research-only execution-timing replay engine.

Iterates persisted plans (``outputs/precompute/<DATE>/planned_execution_payload.json``)
and the frozen intraday minute-bar cache produced by
``scripts.research.intraday_research_cache``, and emits a deterministic
report comparing hypothetical fills at the offsets the spec calls out:
``T+0`` (09:30 ET), ``T+1m``..``T+4m``, ``T+5m`` (baseline — the current
production cron minute), and ``T+10m``.

This script is **read-only** with respect to every execution-path artifact
listed in ``specs/execution_timing_sensitivity_study.md``: no order is
submitted, no broker call is made, no cron file is touched, no
reconciliation artifact is modified, ``core/timing_policy.py`` is not
imported or read. The only files this script writes are under
``outputs/research/execution_timing/`` and ``reports/execution_timing/``.

Output artifacts (one set per ``--run-date``)
---------------------------------------------
``outputs/research/execution_timing/<RUN_DATE>/per_trade_timing.json``
    One record per (date, trade) with the modeled fill at every offset and
    a per-fill ``bar_start_ts`` / ``asof_cutoff_ts`` so the no-look-ahead
    invariant can be audited offline.

``outputs/research/execution_timing/<RUN_DATE>/timing_summary.json``
    Cross-day rollup: opportunity vs. the 9:35 baseline in USD and bps,
    plus coverage diagnostics (days with full bar coverage, missing-bar
    counts, days dropped because plan or cache was unavailable).

``reports/execution_timing/<RUN_DATE>/summary.md``
    Short narrative for humans, derived from ``timing_summary.json``.

CLI
---
::

    python -m scripts.research.execution_timing_replay --run-date 2026-05-29
    python -m scripts.research.execution_timing_replay --run-date 2026-05-29 --trade-date 2026-03-24
    python -m scripts.research.execution_timing_replay --run-date 2026-05-29 --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd
from zoneinfo import ZoneInfo

from core.research.timing_fill_model import (
    DEFAULT_BASELINE_OFFSET_MINUTES,
    DEFAULT_OFFSETS_MINUTES,
    FillRecord,
    aggregate_offsets,
    compute_day_costs,
    compute_opportunity_vs_baseline,
    fill_record_to_dict,
    replay_trade,
)
from scripts.research.intraday_research_cache import (
    CACHE_KEY_VERSION,
    DEFAULT_CACHE_ROOT,
    DEFAULT_PLAN_ROOT,
    cache_path_for,
    load_plan_symbols,
    resolve_plan_path,
)


logger = logging.getLogger(__name__)

UTC = dt.timezone.utc
ET = ZoneInfo("America/New_York")

DEFAULT_OUTPUT_ROOT = Path("outputs/research/execution_timing")
DEFAULT_REPORT_ROOT = Path("reports/execution_timing")
SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Plan discovery
# ---------------------------------------------------------------------------


def discover_plan_dates(plan_root: Path = DEFAULT_PLAN_ROOT) -> list[str]:
    """Return sorted ISO trade dates that have a precompute plan on disk."""
    if not plan_root.exists():
        return []
    dates: list[str] = []
    for child in sorted(plan_root.iterdir()):
        if not child.is_dir():
            continue
        if (child / "planned_execution_payload.json").exists():
            try:
                dt.date.fromisoformat(child.name)
            except ValueError:
                continue
            dates.append(child.name)
    return dates


@dataclass(frozen=True)
class PlanData:
    """A precompute plan, decoded with its execution date already resolved.

    ``plan_date`` is the precompute folder name (i.e. the date the plan was
    generated). ``execution_date`` is the date the plan was scheduled to run
    — taken from ``payload.planned_for`` when present, otherwise ``plan_date``.

    The replay anchors its simulated offsets to ``09:30 ET`` on
    ``execution_date`` (NOT on the time portion of ``planned_for``), since
    the study compares fills relative to the regular-session open, not the
    cron's wall-clock minute. The cache lookup also uses ``execution_date``,
    because that is the date the minute bars come from.
    """

    plan_date: str
    execution_date: str
    planned_for_raw: Optional[str]
    trades: list[dict[str, Any]]


def parse_execution_date(payload: dict[str, Any], plan_date: str) -> str:
    """Return the ET calendar date the plan was scheduled to execute on.

    Falls back to ``plan_date`` if ``planned_for`` is absent or unparseable.
    If ``planned_for`` is tz-aware the value is converted to ET before its
    date is taken; if it is naive it is treated as ET-local (which matches
    how precompute writes the field today).
    """
    planned_for = payload.get("planned_for")
    if not planned_for:
        return plan_date
    try:
        ts = pd.Timestamp(planned_for)
    except (TypeError, ValueError):
        return plan_date
    if pd.isna(ts):
        return plan_date
    if ts.tzinfo is not None:
        ts = ts.tz_convert(ET)
    return str(ts.date())


def load_plan(plan_path: Path, plan_date: str) -> PlanData:
    """Read and normalise a precompute plan, deriving ``execution_date``."""
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    execution_date = parse_execution_date(payload, plan_date)
    trades = _normalise_trades(payload.get("trades") or [])
    return PlanData(
        plan_date=plan_date,
        execution_date=execution_date,
        planned_for_raw=payload.get("planned_for"),
        trades=trades,
    )


def _normalise_trades(trades: Iterable[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        ticker = trade.get("ticker")
        side = trade.get("side")
        shares = trade.get("shares")
        if not isinstance(ticker, str) or side not in {"BUY", "SELL"}:
            continue
        try:
            shares = float(shares)
        except (TypeError, ValueError):
            continue
        if shares == 0:
            continue
        out.append(
            {
                "ticker": ticker.strip().upper(),
                "side": side,
                "shares": shares,
                "prev_close_ref": trade.get("entry_price"),
                "reason": trade.get("reason"),
            }
        )
    return out


def load_plan_trades(plan_path: Path) -> list[dict[str, Any]]:
    """Backwards-compatible accessor returning only the normalised trade list."""
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    return _normalise_trades(payload.get("trades") or [])


# ---------------------------------------------------------------------------
# Bar lookup
# ---------------------------------------------------------------------------


def load_cached_bars(
    *,
    symbol: str,
    trade_date: str,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    cache_key_version: str = CACHE_KEY_VERSION,
) -> Optional[pd.DataFrame]:
    path = cache_path_for(symbol, trade_date, cache_root, cache_key_version)
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception as exc:
        logger.warning("[REPLAY] failed to read cached bars %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Per-day replay
# ---------------------------------------------------------------------------


@dataclass
class DayReplay:
    plan_date: str
    execution_date: str
    planned_for_raw: Optional[str]
    plan_source: str
    plan_trade_count: int
    trades: list[dict[str, Any]]
    per_trade_fills: list[dict[str, FillRecord]]
    coverage: dict[str, Any]
    day_costs: dict[str, dict[str, Any]]
    opportunities: dict[str, dict[str, Any]]
    status: str  # "ok" | "no_plan" | "no_cache" | "empty_plan"


def replay_day(
    *,
    plan_date: str,
    plan_root: Path = DEFAULT_PLAN_ROOT,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    cache_key_version: str = CACHE_KEY_VERSION,
    offsets_minutes: Iterable[int] = DEFAULT_OFFSETS_MINUTES,
    baseline_offset_minutes: int = DEFAULT_BASELINE_OFFSET_MINUTES,
) -> DayReplay:
    """Replay a single plan.

    The plan is located at ``plan_root/<plan_date>/planned_execution_payload.json``.
    The execution date is parsed from ``payload.planned_for`` (date portion
    only, ET-local), falling back to ``plan_date``. Both the cache lookup
    and the simulated 09:30-ET offset anchor use the **execution date**,
    not the plan date.
    """
    offsets = tuple(offsets_minutes)
    plan_path = resolve_plan_path(plan_date, plan_root)
    if not plan_path.exists():
        return DayReplay(
            plan_date=plan_date,
            execution_date=plan_date,
            planned_for_raw=None,
            plan_source=str(plan_path),
            plan_trade_count=0,
            trades=[],
            per_trade_fills=[],
            coverage={"reason": "plan_payload_missing"},
            day_costs={},
            opportunities={},
            status="no_plan",
        )

    plan = load_plan(plan_path, plan_date)
    if not plan.trades:
        return DayReplay(
            plan_date=plan.plan_date,
            execution_date=plan.execution_date,
            planned_for_raw=plan.planned_for_raw,
            plan_source=str(plan_path),
            plan_trade_count=0,
            trades=[],
            per_trade_fills=[],
            coverage={"reason": "empty_plan"},
            day_costs={},
            opportunities={},
            status="empty_plan",
        )

    bars_by_symbol: dict[str, pd.DataFrame | None] = {}
    cache_hits = 0
    cache_misses = 0
    for symbol in sorted({t["ticker"] for t in plan.trades}):
        bars = load_cached_bars(
            symbol=symbol,
            trade_date=plan.execution_date,
            cache_root=cache_root,
            cache_key_version=cache_key_version,
        )
        bars_by_symbol[symbol] = bars
        if bars is None or bars.empty:
            cache_misses += 1
        else:
            cache_hits += 1

    if cache_hits == 0:
        return DayReplay(
            plan_date=plan.plan_date,
            execution_date=plan.execution_date,
            planned_for_raw=plan.planned_for_raw,
            plan_source=str(plan_path),
            plan_trade_count=len(plan.trades),
            trades=plan.trades,
            per_trade_fills=[],
            coverage={
                "reason": "no_cache_for_any_symbol",
                "cache_hits": 0,
                "cache_misses": cache_misses,
                "cache_lookup_date": plan.execution_date,
            },
            day_costs={},
            opportunities={},
            status="no_cache",
        )

    per_trade_fills: list[dict[str, FillRecord]] = []
    for trade in plan.trades:
        bars = bars_by_symbol.get(trade["ticker"])
        if bars is None or bars.empty:
            per_trade_fills.append(
                {
                    f"T+{offset}m": FillRecord(
                        offset_label=f"T+{offset}m",
                        offset_minutes=offset,
                        simulated_execution_ts="",
                        asof_cutoff_ts="",
                        bar_start_ts=None,
                        ref_price=None,
                        modeled_fill=None,
                        bar_source=None,
                        bar_feed=None,
                        status="no_bar_in_window",
                    )
                    for offset in offsets
                }
            )
            continue
        per_trade_fills.append(
            replay_trade(
                side=trade["side"],
                shares=trade["shares"],
                bars=bars,
                trade_date=plan.execution_date,
                offsets_minutes=offsets,
            )
        )

    day_costs = compute_day_costs(
        trades=plan.trades,
        fills_by_trade=per_trade_fills,
        offsets_minutes=offsets,
    )
    opportunities = compute_opportunity_vs_baseline(
        day_costs=day_costs,
        baseline_offset_minutes=baseline_offset_minutes,
    )

    coverage = {
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "cache_lookup_date": plan.execution_date,
        "trades_total": len(plan.trades),
        "trades_fully_filled_at_baseline": int(
            day_costs.get(f"T+{baseline_offset_minutes}m", {}).get("fillable_trades", 0)
        ),
    }

    return DayReplay(
        plan_date=plan.plan_date,
        execution_date=plan.execution_date,
        planned_for_raw=plan.planned_for_raw,
        plan_source=str(plan_path),
        plan_trade_count=len(plan.trades),
        trades=plan.trades,
        per_trade_fills=per_trade_fills,
        coverage=coverage,
        day_costs=day_costs,
        opportunities=opportunities,
        status="ok",
    )


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------


@dataclass
class ReplayResult:
    run_date: str
    per_trade_path: Path
    summary_path: Path
    report_path: Path
    days_in_scope: int
    days_with_full_coverage: int
    overall_status: str
    headline_offsets: list[str] = field(default_factory=list)


def _no_look_ahead_violations(per_day: list[DayReplay]) -> list[dict[str, Any]]:
    """Audit every recorded fill — defense in depth on top of pick_reference_bar."""
    violations: list[dict[str, Any]] = []
    for day in per_day:
        for trade, fills in zip(day.trades, day.per_trade_fills):
            for label, fill in fills.items():
                if fill.status != "ok" or fill.bar_start_ts is None:
                    continue
                if fill.bar_start_ts < fill.asof_cutoff_ts:
                    violations.append(
                        {
                            "plan_date": day.plan_date,
                            "execution_date": day.execution_date,
                            "ticker": trade["ticker"],
                            "offset_label": label,
                            "bar_start_ts": fill.bar_start_ts,
                            "asof_cutoff_ts": fill.asof_cutoff_ts,
                        }
                    )
    return violations


def run_replay(
    *,
    run_date: str,
    plan_dates: Optional[list[str]] = None,
    trade_dates: Optional[list[str]] = None,  # backwards-compat alias for plan_dates
    plan_root: Path = DEFAULT_PLAN_ROOT,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
    cache_key_version: str = CACHE_KEY_VERSION,
    offsets_minutes: Iterable[int] = DEFAULT_OFFSETS_MINUTES,
    baseline_offset_minutes: int = DEFAULT_BASELINE_OFFSET_MINUTES,
    now: Optional[dt.datetime] = None,
) -> ReplayResult:
    offsets = tuple(offsets_minutes)
    scope = plan_dates if plan_dates is not None else trade_dates
    if scope is None:
        scope = discover_plan_dates(plan_root)
    scope = sorted(set(scope))

    now_utc = (now or dt.datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    generated_at = now_utc.isoformat().replace("+00:00", "Z")

    per_day: list[DayReplay] = []
    for plan_date in scope:
        per_day.append(
            replay_day(
                plan_date=plan_date,
                plan_root=plan_root,
                cache_root=cache_root,
                cache_key_version=cache_key_version,
                offsets_minutes=offsets,
                baseline_offset_minutes=baseline_offset_minutes,
            )
        )

    violations = _no_look_ahead_violations(per_day)
    if violations:
        raise RuntimeError(
            f"no_look_ahead_violation_detected: {len(violations)} violations; "
            f"first={violations[0]}"
        )

    per_trade_path = output_root / run_date / "per_trade_timing.json"
    summary_path = output_root / run_date / "timing_summary.json"
    report_path = report_root / run_date / "summary.md"

    per_trade_payload = _build_per_trade_payload(
        per_day=per_day,
        run_date=run_date,
        generated_at=generated_at,
        cache_key_version=cache_key_version,
        offsets=offsets,
        baseline_offset_minutes=baseline_offset_minutes,
    )
    summary_payload = _build_summary_payload(
        per_day=per_day,
        run_date=run_date,
        generated_at=generated_at,
        cache_key_version=cache_key_version,
        offsets=offsets,
        baseline_offset_minutes=baseline_offset_minutes,
    )

    per_trade_path.parent.mkdir(parents=True, exist_ok=True)
    per_trade_path.write_text(
        json.dumps(per_trade_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        _render_summary_markdown(summary_payload, baseline_offset_minutes),
        encoding="utf-8",
    )

    days_with_full_coverage = sum(
        1 for d in per_day
        if d.status == "ok"
        and d.coverage.get("cache_misses", 0) == 0
        and d.coverage.get("trades_fully_filled_at_baseline", 0) == d.plan_trade_count
    )

    return ReplayResult(
        run_date=run_date,
        per_trade_path=per_trade_path,
        summary_path=summary_path,
        report_path=report_path,
        days_in_scope=len(per_day),
        days_with_full_coverage=days_with_full_coverage,
        overall_status="OK" if days_with_full_coverage > 0 else "INSUFFICIENT_DATA",
        headline_offsets=[f"T+{m}m" for m in offsets],
    )


def _build_per_trade_payload(
    *,
    per_day: list[DayReplay],
    run_date: str,
    generated_at: str,
    cache_key_version: str,
    offsets: tuple[int, ...],
    baseline_offset_minutes: int,
) -> dict[str, Any]:
    days_payload: list[dict[str, Any]] = []
    for day in per_day:
        trades_payload: list[dict[str, Any]] = []
        for trade, fills in zip(day.trades, day.per_trade_fills):
            trades_payload.append(
                {
                    "ticker": trade["ticker"],
                    "side": trade["side"],
                    "shares": trade["shares"],
                    "prev_close_ref": trade.get("prev_close_ref"),
                    "reason": trade.get("reason"),
                    "fills_by_offset": {
                        label: fill_record_to_dict(fill)
                        for label, fill in fills.items()
                    },
                }
            )
        days_payload.append(
            {
                "plan_date": day.plan_date,
                "execution_date": day.execution_date,
                "planned_for_raw": day.planned_for_raw,
                "plan_source": day.plan_source,
                "status": day.status,
                "coverage": day.coverage,
                "trades": trades_payload,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "run_date": run_date,
        "generated_at": generated_at,
        "cache_key_version": cache_key_version,
        "offsets": [f"T+{m}m" for m in offsets],
        "baseline_offset": f"T+{baseline_offset_minutes}m",
        "days": days_payload,
        "notes": (
            "Research-only timing replay; no orders submitted, no execution-path "
            "files modified. See specs/execution_timing_sensitivity_study.md."
        ),
    }


def _build_summary_payload(
    *,
    per_day: list[DayReplay],
    run_date: str,
    generated_at: str,
    cache_key_version: str,
    offsets: tuple[int, ...],
    baseline_offset_minutes: int,
) -> dict[str, Any]:
    ok_days = [d for d in per_day if d.status == "ok"]
    day_costs_list = [d.day_costs for d in ok_days]
    opps_list = [d.opportunities for d in ok_days]

    cost_agg = aggregate_offsets(day_costs_list, "cost_usd")
    notional_agg = aggregate_offsets(day_costs_list, "gross_notional_usd")
    opp_usd_agg = aggregate_offsets(opps_list, "opportunity_usd")
    opp_bps_agg = aggregate_offsets(opps_list, "opportunity_bps")

    by_offset: dict[str, dict[str, Any]] = {}
    for m in offsets:
        label = f"T+{m}m"
        by_offset[label] = {
            "opportunity_usd": opp_usd_agg.get(label, {}),
            "opportunity_bps": opp_bps_agg.get(label, {}),
            "cost_usd": cost_agg.get(label, {}),
            "gross_notional_usd": notional_agg.get(label, {}),
        }

    coverage_summary = {
        "days_in_scope": len(per_day),
        "days_replayed": len(ok_days),
        "days_dropped_no_plan": sum(1 for d in per_day if d.status == "no_plan"),
        "days_dropped_empty_plan": sum(1 for d in per_day if d.status == "empty_plan"),
        "days_dropped_no_cache": sum(1 for d in per_day if d.status == "no_cache"),
        "days_with_partial_cache": sum(
            1 for d in ok_days if d.coverage.get("cache_misses", 0) > 0
        ),
    }

    dates_payload = [
        {
            "plan_date": d.plan_date,
            "execution_date": d.execution_date,
            "planned_for_raw": d.planned_for_raw,
            "status": d.status,
        }
        for d in per_day
    ]
    dates_payload.sort(key=lambda r: (r["plan_date"], r["execution_date"]))

    return {
        "schema_version": SCHEMA_VERSION,
        "run_date": run_date,
        "generated_at": generated_at,
        "cache_key_version": cache_key_version,
        "offsets": [f"T+{m}m" for m in offsets],
        "baseline_offset": f"T+{baseline_offset_minutes}m",
        "dates": dates_payload,
        "plan_dates": sorted(d.plan_date for d in per_day),
        "execution_dates": sorted(d.execution_date for d in per_day),
        "coverage_summary": coverage_summary,
        "by_offset": by_offset,
        "notes": (
            "Phase-1 model: modeled_fill = open of the first minute bar with "
            "bar_start_ts >= simulated_execution_ts. No half-spread or open-vol "
            "term yet; see specs/execution_timing_sensitivity_study.md §1.2."
        ),
    }


def _render_summary_markdown(summary: dict[str, Any], baseline_offset_minutes: int) -> str:
    baseline_label = f"T+{baseline_offset_minutes}m"
    cov = summary["coverage_summary"]
    lines: list[str] = []
    lines.append(f"# Execution Timing Replay — {summary['run_date']}")
    lines.append("")
    lines.append(f"_Generated: {summary['generated_at']}_  ")
    lines.append(f"_Cache: `{summary['cache_key_version']}`_  ")
    lines.append(f"_Baseline: **{baseline_label}** (current 9:35 cron)_  ")
    lines.append("")
    lines.append("## Coverage")
    lines.append("")
    lines.append(f"- Days in scope: **{cov['days_in_scope']}**")
    lines.append(f"- Days replayed: **{cov['days_replayed']}**")
    lines.append(f"- Days dropped (no plan): {cov['days_dropped_no_plan']}")
    lines.append(f"- Days dropped (empty plan): {cov['days_dropped_empty_plan']}")
    lines.append(f"- Days dropped (no cache for any symbol): {cov['days_dropped_no_cache']}")
    lines.append(f"- Days with partial cache: {cov['days_with_partial_cache']}")
    lines.append("")

    dates = summary.get("dates") or []
    dates_with_offset = [
        d for d in dates
        if isinstance(d, dict)
        and d.get("plan_date")
        and d.get("execution_date")
        and d["plan_date"] != d["execution_date"]
    ]
    if dates_with_offset:
        lines.append("## Plan → execution date mapping")
        lines.append("")
        lines.append("| plan_date | execution_date | planned_for | status |")
        lines.append("| --- | --- | --- | --- |")
        for d in dates_with_offset:
            lines.append(
                "| {p} | {e} | {pf} | {s} |".format(
                    p=d["plan_date"],
                    e=d["execution_date"],
                    pf=(d.get("planned_for_raw") or "—"),
                    s=d.get("status") or "—",
                )
            )
        lines.append("")
    else:
        lines.append("_All days have ``plan_date == execution_date``; cache is keyed on the same date as the precompute folder._")
        lines.append("")

    lines.append(f"## Opportunity vs. {baseline_label}")
    lines.append("")
    lines.append("Positive = offset is *cheaper* than the baseline (less cash out for the same order set).")
    lines.append("")
    lines.append("| Offset | n | mean USD | median USD | p10 USD | p90 USD | mean bps | median bps |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for offset_label in summary["offsets"]:
        usd = summary["by_offset"].get(offset_label, {}).get("opportunity_usd", {})
        bps = summary["by_offset"].get(offset_label, {}).get("opportunity_bps", {})
        if offset_label == baseline_label:
            lines.append(
                f"| {offset_label} (baseline) | {usd.get('n', 0)} | — | — | — | — | — | — |"
            )
            continue
        lines.append(
            "| {label} | {n} | {mean_usd} | {med_usd} | {p10_usd} | {p90_usd} | {mean_bps} | {med_bps} |".format(
                label=offset_label,
                n=usd.get("n", 0),
                mean_usd=_fmt(usd.get("mean")),
                med_usd=_fmt(usd.get("median")),
                p10_usd=_fmt(usd.get("p10")),
                p90_usd=_fmt(usd.get("p90")),
                mean_bps=_fmt(bps.get("mean")),
                med_bps=_fmt(bps.get("median")),
            )
        )
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- This is research-only output. The 9:35 baseline is retained as the production")
    lines.append("  cron minute pending a HIGH-blast-radius FR per the governance model.")
    lines.append("- Phase 1 fill = bar open at-or-after the simulated timestamp. Half-spread and")
    lines.append("  open-volatility terms (spec §1.2) are deferred to a follow-up.")
    lines.append("")
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _today_run_date() -> str:
    return dt.datetime.now(UTC).date().isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Research-only execution-timing replay engine. Compares hypothetical "
            "fills across offsets T+0..T+10m using the frozen intraday cache. "
            "Never submits orders or modifies execution-path artifacts."
        )
    )
    parser.add_argument(
        "--run-date",
        default=None,
        help="ISO date used to namespace output artifacts. Defaults to today (UTC).",
    )
    parser.add_argument(
        "--plan-date",
        "--trade-date",
        dest="plan_date",
        action="append",
        default=None,
        help=(
            "Restrict to specific plan dates (precompute folder names); may be "
            "passed multiple times. The execution date is derived per-plan from "
            "payload.planned_for. ``--trade-date`` is accepted as a back-compat alias. "
            "Default: all plans on disk."
        ),
    )
    parser.add_argument("--plan-root", default=str(DEFAULT_PLAN_ROOT))
    parser.add_argument("--cache-root", default=str(DEFAULT_CACHE_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--report-root", default=str(DEFAULT_REPORT_ROOT))
    parser.add_argument("--dry-run", action="store_true", help="Resolve scope and print plan; do not write artifacts.")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)

    run_date = args.run_date or _today_run_date()
    plan_root = Path(args.plan_root)
    cache_root = Path(args.cache_root)
    output_root = Path(args.output_root)
    report_root = Path(args.report_root)

    if args.dry_run:
        plan_dates = sorted(set(args.plan_date)) if args.plan_date else discover_plan_dates(plan_root)
        # Resolve execution dates so a dry-run shows the exact cache lookups
        # that the live replay will perform (date alignment is the #1 cause of
        # no_cache misses; surface it here, before the real run).
        date_map: list[dict[str, Any]] = []
        for pd_ in plan_dates:
            plan_path = resolve_plan_path(pd_, plan_root)
            if not plan_path.exists():
                date_map.append({
                    "plan_date": pd_,
                    "execution_date": pd_,
                    "planned_for_raw": None,
                    "plan_exists": False,
                })
                continue
            try:
                plan = load_plan(plan_path, pd_)
            except Exception as exc:
                date_map.append({
                    "plan_date": pd_,
                    "execution_date": pd_,
                    "planned_for_raw": None,
                    "plan_exists": True,
                    "error": str(exc),
                })
                continue
            date_map.append({
                "plan_date": plan.plan_date,
                "execution_date": plan.execution_date,
                "planned_for_raw": plan.planned_for_raw,
                "plan_exists": True,
            })
        print(
            json.dumps(
                {
                    "run_date": run_date,
                    "plan_dates": plan_dates,
                    "n_plan_dates": len(plan_dates),
                    "dates": date_map,
                    "plan_root": str(plan_root),
                    "cache_root": str(cache_root),
                    "cache_key_version": CACHE_KEY_VERSION,
                    "offsets": [f"T+{m}m" for m in DEFAULT_OFFSETS_MINUTES],
                    "baseline_offset": f"T+{DEFAULT_BASELINE_OFFSET_MINUTES}m",
                    "dry_run": True,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    result = run_replay(
        run_date=run_date,
        plan_dates=args.plan_date,
        plan_root=plan_root,
        cache_root=cache_root,
        output_root=output_root,
        report_root=report_root,
    )
    print(
        json.dumps(
            {
                "run_date": result.run_date,
                "overall_status": result.overall_status,
                "days_in_scope": result.days_in_scope,
                "days_with_full_coverage": result.days_with_full_coverage,
                "per_trade_path": str(result.per_trade_path),
                "summary_path": str(result.summary_path),
                "report_path": str(result.report_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result.overall_status == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
