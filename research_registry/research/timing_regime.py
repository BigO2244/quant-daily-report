"""Join execution-timing replay output to the VIX regime history.

This module is the analytical heart of the ``execution_timing_by_vix_regime``
MCP tool. Every function here is **pure**: it never writes to disk, never
calls external services, never imports anything from the execution path. It
consumes the deterministic artifacts produced by

* ``scripts.research.execution_timing_replay`` →
  ``outputs/research/execution_timing/<RUN_DATE>/{timing_summary,per_trade_timing}.json``
* the VIX regime classifier →
  ``outputs/vix_regime/{regime_history.csv,regime_current.json}``

and produces a per-regime, per-offset aggregation that the MCP tool layer can
surface verbatim. The module is deliberately conservative about claims of
significance: any regime with fewer than
:data:`INSUFFICIENT_SAMPLE_THRESHOLD` days is labelled
``insufficient_sample_size`` and its summary statistics are still computed
but tagged.

Determinism / fail-closed contract
----------------------------------
* If the timing root does not exist, :func:`select_timing_run` returns
  ``None`` and the higher-level tool emits ``NO_TIMING_DATA`` cleanly.
* If the regime history is empty or malformed, joins produce
  ``regime=UNKNOWN`` rows rather than dropping them silently.
* All offsets in a timing run are preserved; ``baseline_offset`` is carried
  through so callers know which offset the opportunities are signed against.
* Dictionaries returned from this module are JSON-safe and stable under
  ``json.dumps(..., sort_keys=True)``.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_TIMING_ROOT = Path("outputs/research/execution_timing")
DEFAULT_REGIME_HISTORY = Path("outputs/vix_regime/regime_history.csv")
DEFAULT_REGIME_CURRENT = Path("outputs/vix_regime/regime_current.json")

INSUFFICIENT_SAMPLE_THRESHOLD = 5
"""Minimum days in a regime bucket before the tool will not flag the
``insufficient_sample_size`` warning. Chosen conservatively: with fewer than
five replayed days, no statistical claim can be defended.
"""


@dataclass(frozen=True)
class TimingDay:
    """One day of timing-replay output, anchored on the *execution* date."""

    plan_date: str
    execution_date: str
    status: str
    by_offset: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class JoinedDay:
    """A :class:`TimingDay` annotated with its execution-date VIX regime."""

    plan_date: str
    execution_date: str
    regime: str
    vix: Optional[float]
    by_offset: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class RegimeAggregate:
    """Per-regime, per-offset summary statistics."""

    regime: str
    n_days: int
    insufficient_sample: bool
    by_offset: dict[str, dict[str, Optional[float]]]


# ---------------------------------------------------------------------------
# Discovery / loading
# ---------------------------------------------------------------------------


def select_timing_run(timing_root: Path = DEFAULT_TIMING_ROOT) -> Optional[Path]:
    """Return the most recent timing-replay ``<RUN_DATE>`` directory, or None.

    "Most recent" is defined by lexicographic sort of ISO-formatted run-date
    directory names — same convention the replay engine and registry use.
    """
    if not timing_root.exists() or not timing_root.is_dir():
        return None
    candidates = [p for p in timing_root.iterdir() if p.is_dir() and (p / "timing_summary.json").exists()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.name)[-1]


def load_timing_summaries(timing_run_dir: Path) -> tuple[dict[str, Any], list[TimingDay]]:
    """Load the ``timing_summary.json`` + ``per_trade_timing.json`` pair.

    Returns ``(summary_payload, per_day_records)``. Per-day records are
    pulled from ``per_trade_timing.json`` because that file carries the
    full per-day per-offset opportunity numbers; ``timing_summary.json``
    carries the cross-day rollup, which we keep for headline use.
    """
    summary_path = timing_run_dir / "timing_summary.json"
    per_trade_path = timing_run_dir / "per_trade_timing.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"timing_summary_missing: {summary_path}")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    per_day: list[TimingDay] = []
    if per_trade_path.exists():
        per_trade = json.loads(per_trade_path.read_text(encoding="utf-8"))
        for day in per_trade.get("days") or []:
            if not isinstance(day, dict):
                continue
            plan_date = str(day.get("plan_date") or day.get("trade_date") or "")
            execution_date = str(day.get("execution_date") or day.get("plan_date") or plan_date)
            status = str(day.get("status") or "unknown")
            # The per-day per-offset opportunity record lives in the
            # cross-day summary at .by_offset, NOT in per_trade. So we
            # synthesize a per-day record using cost_usd / gross_notional
            # which we DO have per-day in coverage, plus we re-derive
            # opportunity from costs against the baseline.
            day_costs = _per_day_costs_from_trades(day)
            by_offset = _per_day_opportunity_from_costs(
                day_costs=day_costs,
                baseline_offset=per_trade.get("baseline_offset", "T+5m"),
            )
            per_day.append(
                TimingDay(
                    plan_date=plan_date,
                    execution_date=execution_date,
                    status=status,
                    by_offset=by_offset,
                )
            )
    return summary, per_day


def _per_day_costs_from_trades(day: dict[str, Any]) -> dict[str, dict[str, Optional[float]]]:
    """Re-derive per-day signed cost + gross notional per offset from the
    per-trade fills in ``per_trade_timing.json``.

    cost(d, Δ)        = Σ_i q_i × modeled_fill_i(Δ)
    gross_notional(d) = Σ_i |q_i × ref_price_i(Δ)|

    A trade is fillable at an offset iff its fill status is ``ok`` AND
    ``modeled_fill`` is non-null. Days where any trade fails to fill at an
    offset record ``fillable_trades`` accordingly so the caller can avoid
    apples-to-oranges comparisons.
    """
    trades = day.get("trades") or []
    out: dict[str, dict[str, Optional[float]]] = {}
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        side = str(trade.get("side", "")).upper()
        try:
            shares = float(trade.get("shares") or 0.0)
        except (TypeError, ValueError):
            continue
        if shares == 0 or side not in {"BUY", "SELL"}:
            continue
        q = shares if side == "BUY" else -shares
        for offset_label, fill in (trade.get("fills_by_offset") or {}).items():
            if not isinstance(fill, dict):
                continue
            entry = out.setdefault(offset_label, {"cost_usd": 0.0, "gross_notional_usd": 0.0, "fillable_trades": 0})
            if fill.get("status") != "ok":
                continue
            modeled_fill = fill.get("modeled_fill")
            ref_price = fill.get("ref_price")
            if modeled_fill is None or ref_price is None:
                continue
            entry["cost_usd"] = float(entry["cost_usd"]) + q * float(modeled_fill)
            entry["gross_notional_usd"] = float(entry["gross_notional_usd"]) + abs(q * float(ref_price))
            entry["fillable_trades"] = int(entry["fillable_trades"]) + 1
    # Mark offsets where no trade was fillable.
    for offset_label, entry in out.items():
        if entry["fillable_trades"] == 0:
            out[offset_label] = {"cost_usd": None, "gross_notional_usd": None, "fillable_trades": 0}
    return out


def _per_day_opportunity_from_costs(
    *,
    day_costs: dict[str, dict[str, Optional[float]]],
    baseline_offset: str,
) -> dict[str, dict[str, Any]]:
    """Compute opportunity_usd / opportunity_bps per offset vs baseline."""
    baseline = day_costs.get(baseline_offset) or {}
    baseline_cost = baseline.get("cost_usd")
    baseline_fillable = int(baseline.get("fillable_trades") or 0)
    out: dict[str, dict[str, Any]] = {}
    for offset_label, entry in day_costs.items():
        cost = entry.get("cost_usd")
        gross = entry.get("gross_notional_usd")
        fillable = int(entry.get("fillable_trades") or 0)
        if (
            baseline_cost is None
            or cost is None
            or baseline_fillable == 0
            or fillable == 0
            or fillable != baseline_fillable
        ):
            out[offset_label] = {
                "cost_usd": cost,
                "gross_notional_usd": gross,
                "fillable_trades": fillable,
                "opportunity_usd": None,
                "opportunity_bps": None,
            }
            continue
        opportunity_usd = float(baseline_cost) - float(cost)
        opportunity_bps = (opportunity_usd / float(gross)) * 10_000.0 if gross else None
        out[offset_label] = {
            "cost_usd": float(cost),
            "gross_notional_usd": float(gross),
            "fillable_trades": fillable,
            "opportunity_usd": opportunity_usd,
            "opportunity_bps": opportunity_bps,
        }
    return out


class RegimeHistoryFormatError(ValueError):
    """The regime CSV exists with rows but is missing required columns.

    Distinct from "missing or empty" because the operator's remediation is
    different: a missing file means run the classifier; a bad-schema file
    means upstream changed columns and the loader needs an update (this
    is the path we landed on when the VM CSV started using ``date`` instead
    of ``as_of``).
    """

    def __init__(self, *, missing_columns: list[str], regime_csv: Path) -> None:
        super().__init__(
            f"vix_regime_history bad schema: {regime_csv} is missing required "
            f"column(s) {missing_columns}; one of {{date, as_of, execution_date}} "
            "must be present together with `regime` and `vix`."
        )
        self.missing_columns = missing_columns
        self.regime_csv = regime_csv


_REGIME_DATE_COLUMN_CANDIDATES: tuple[str, ...] = ("date", "as_of", "execution_date")
"""Column names the loader will accept as the row's date. The VM ships
``date``; older local fixtures use ``as_of``; ``execution_date`` is here for
future-proofing if upstream renames it. The first non-empty value found in
the row wins. Adding a new alias is the gate for accepting new schemas."""


def _extract_regime_date(row: dict[str, Any]) -> str:
    for column in _REGIME_DATE_COLUMN_CANDIDATES:
        value = row.get(column)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text[:10]
    return ""


def _detect_missing_regime_columns(fieldnames: list[str] | None) -> list[str]:
    """Identify which required columns are absent from the CSV header.

    The header must include at least one of the date aliases AND ``regime``
    AND ``vix``. Order is preserved so the error message reads naturally.
    """
    columns = {(name or "").strip().lower() for name in (fieldnames or [])}
    missing: list[str] = []
    if not (columns & {col.lower() for col in _REGIME_DATE_COLUMN_CANDIDATES}):
        missing.append("date|as_of|execution_date")
    if "regime" not in columns:
        missing.append("regime")
    if "vix" not in columns:
        missing.append("vix")
    return missing


def load_vix_regime_history(regime_csv: Path = DEFAULT_REGIME_HISTORY) -> dict[str, dict[str, Any]]:
    """Load the regime CSV as ``execution_date → record``.

    Accepted schemas
    ----------------
    The CSV may use any of ``date``, ``as_of``, or ``execution_date`` as the
    per-row date column. ``regime`` and ``vix`` are always required. Any
    other columns (``position_scale``, ``max_positions``, ``source``,
    ``fallback_used``, …) are tolerated and ignored — the loader is forward
    compatible with new bookkeeping fields.

    Deterministic dedup
    -------------------
    Rows are stable-sorted by date before insertion, so when the same
    execution_date appears more than once (the classifier writes intra-day
    snapshots), the **last row in file order** wins for that date. This is
    consistent with the morning-brief convention.

    Errors
    ------
    * ``regime_csv`` missing → returns ``{}`` (caller treats as ``NO_REGIME_DATA``).
    * File exists but has no rows → returns ``{}``.
    * File exists with rows but header is missing required columns → raises
      :class:`RegimeHistoryFormatError`.
    """
    if not regime_csv.exists():
        return {}
    with regime_csv.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if not rows:
        return {}

    missing_cols = _detect_missing_regime_columns(fieldnames)
    if missing_cols:
        raise RegimeHistoryFormatError(missing_columns=missing_cols, regime_csv=regime_csv)

    # Stable sort by extracted date; ties preserve original file order so the
    # last row in the CSV for a given date is the one that wins below.
    indexed_rows = list(enumerate(rows))
    indexed_rows.sort(key=lambda item: (_extract_regime_date(item[1]), item[0]))

    out: dict[str, dict[str, Any]] = {}
    for _, row in indexed_rows:
        execution_date = _extract_regime_date(row)
        if not execution_date:
            continue
        try:
            vix = float(row.get("vix") or "nan")
        except (TypeError, ValueError):
            vix = float("nan")
        out[execution_date] = {
            "regime": (row.get("regime") or "").strip() or "UNKNOWN",
            "vix": None if math.isnan(vix) else vix,
        }
    return out


# ---------------------------------------------------------------------------
# Join + aggregate
# ---------------------------------------------------------------------------


def join_timing_to_regime(
    timing_days: Iterable[TimingDay],
    regime_map: dict[str, dict[str, Any]],
) -> list[JoinedDay]:
    """Attach the regime label and VIX level for each day's execution date."""
    joined: list[JoinedDay] = []
    for day in timing_days:
        record = regime_map.get(day.execution_date)
        regime = record["regime"] if record else "UNKNOWN"
        vix = record["vix"] if record else None
        joined.append(
            JoinedDay(
                plan_date=day.plan_date,
                execution_date=day.execution_date,
                regime=regime,
                vix=vix,
                by_offset=day.by_offset,
            )
        )
    return joined


def aggregate_by_regime(
    joined_days: Iterable[JoinedDay],
    *,
    threshold: int = INSUFFICIENT_SAMPLE_THRESHOLD,
) -> list[RegimeAggregate]:
    """Compute mean/median opportunity per (regime, offset)."""
    by_regime: dict[str, list[JoinedDay]] = {}
    for day in joined_days:
        by_regime.setdefault(day.regime, []).append(day)

    results: list[RegimeAggregate] = []
    for regime in sorted(by_regime.keys()):
        days = by_regime[regime]
        n_days = len(days)
        all_offsets = sorted({label for day in days for label in day.by_offset.keys()})
        by_offset_stats: dict[str, dict[str, Optional[float]]] = {}
        for label in all_offsets:
            usd_samples: list[float] = []
            bps_samples: list[float] = []
            n_with_opportunity = 0
            for day in days:
                entry = day.by_offset.get(label) or {}
                usd = entry.get("opportunity_usd")
                bps = entry.get("opportunity_bps")
                if usd is not None:
                    usd_samples.append(float(usd))
                    n_with_opportunity += 1
                if bps is not None:
                    bps_samples.append(float(bps))
            by_offset_stats[label] = {
                "n_days_with_opportunity": n_with_opportunity,
                "mean_opportunity_usd": _safe_mean(usd_samples),
                "median_opportunity_usd": _safe_median(usd_samples),
                "mean_opportunity_bps": _safe_mean(bps_samples),
                "median_opportunity_bps": _safe_median(bps_samples),
            }
        results.append(
            RegimeAggregate(
                regime=regime,
                n_days=n_days,
                insufficient_sample=n_days < threshold,
                by_offset=by_offset_stats,
            )
        )
    return results


def _safe_mean(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return round(statistics.fmean(values), 6)


def _safe_median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return round(statistics.median(values), 6)


# ---------------------------------------------------------------------------
# High-level orchestrator used by the MCP tool
# ---------------------------------------------------------------------------


def answer_timing_by_regime_question(
    *,
    timing_root: Path = DEFAULT_TIMING_ROOT,
    regime_history: Path = DEFAULT_REGIME_HISTORY,
    threshold: int = INSUFFICIENT_SAMPLE_THRESHOLD,
) -> dict[str, Any]:
    """End-to-end: discover the latest timing run, join, aggregate, summarise.

    Fail-closed contract:
    * ``status="NO_TIMING_DATA"`` if no timing run is on disk yet.
    * ``status="NO_REGIME_DATA"`` if timing exists but the regime CSV is
      missing or empty.
    * ``status="OK"`` otherwise; a per-regime ``insufficient_sample`` flag
      tags any bucket with fewer than ``threshold`` days.

    The function never writes to disk, never calls a network, and never
    raises on missing data — all "missing" branches collapse to a structured
    response that the MCP tool layer can serialise verbatim.
    """
    timing_run_dir = select_timing_run(timing_root)
    if timing_run_dir is None:
        return {
            "status": "NO_TIMING_DATA",
            "reason": f"no timing-replay run found under {timing_root}",
            "timing_root": str(timing_root),
            "regime_history": str(regime_history),
            "regime_aggregates": [],
            "baseline_offset": None,
            "run_date": None,
            "warnings": [
                "execution_timing replay has not been run yet; "
                "use `python -m scripts.research.execution_timing_replay --run-date <YYYY-MM-DD>`"
            ],
        }

    summary, per_day = load_timing_summaries(timing_run_dir)
    try:
        regime_map = load_vix_regime_history(regime_history)
    except RegimeHistoryFormatError as exc:
        return {
            "status": "BAD_REGIME_SCHEMA",
            "reason": str(exc),
            "missing_columns": exc.missing_columns,
            "timing_root": str(timing_root),
            "regime_history": str(regime_history),
            "regime_aggregates": [],
            "baseline_offset": summary.get("baseline_offset"),
            "run_date": summary.get("run_date") or timing_run_dir.name,
            "warnings": [
                "regime CSV is present but has an unrecognised schema; "
                f"missing columns: {exc.missing_columns}. The loader accepts "
                "`date`, `as_of`, or `execution_date` for the date column; "
                "`regime` and `vix` are always required."
            ],
        }
    if not regime_map:
        # File missing OR file present-but-empty (after header). Distinguish
        # so the operator knows whether to run the classifier or look at why
        # it wrote a zero-row file.
        if Path(regime_history).exists():
            reason = f"regime_history file is empty (no rows after header): {regime_history}"
            warning = "regime CSV exists but contains no rows; classifier may have failed to write data"
        else:
            reason = f"regime_history file does not exist: {regime_history}"
            warning = "regime CSV missing; cannot stratify timing by regime"
        return {
            "status": "NO_REGIME_DATA",
            "reason": reason,
            "timing_root": str(timing_root),
            "regime_history": str(regime_history),
            "regime_aggregates": [],
            "baseline_offset": summary.get("baseline_offset"),
            "run_date": summary.get("run_date") or timing_run_dir.name,
            "warnings": [warning],
        }

    joined = join_timing_to_regime(per_day, regime_map)
    aggregates = aggregate_by_regime(joined, threshold=threshold)

    days_total = len(joined)
    days_with_regime = sum(1 for d in joined if d.regime != "UNKNOWN")
    regimes_with_sufficient = sorted(
        a.regime for a in aggregates if not a.insufficient_sample and a.regime != "UNKNOWN"
    )

    warnings: list[str] = []
    if days_with_regime < days_total:
        warnings.append(
            f"{days_total - days_with_regime} of {days_total} replayed days had no regime "
            "label in the VIX history; their statistics are bucketed under regime=UNKNOWN."
        )
    insufficient = [
        a.regime for a in aggregates
        if a.insufficient_sample and a.regime != "UNKNOWN"
    ]
    if insufficient:
        warnings.append(
            f"regimes with <{threshold} days (insufficient_sample): {sorted(insufficient)}"
        )

    return {
        "status": "OK",
        "timing_root": str(timing_root),
        "regime_history": str(regime_history),
        "run_date": summary.get("run_date") or timing_run_dir.name,
        "cache_key_version": summary.get("cache_key_version"),
        "baseline_offset": summary.get("baseline_offset"),
        "offsets": summary.get("offsets") or [],
        "coverage": {
            "days_total": days_total,
            "days_with_regime_label": days_with_regime,
            "regimes_observed": sorted({d.regime for d in joined}),
            "regimes_with_sufficient_sample": regimes_with_sufficient,
            "insufficient_sample_threshold": threshold,
        },
        "regime_aggregates": [
            {
                "regime": a.regime,
                "n_days": a.n_days,
                "insufficient_sample": a.insufficient_sample,
                "by_offset": a.by_offset,
            }
            for a in aggregates
        ],
        "per_day_join": [
            {
                "plan_date": d.plan_date,
                "execution_date": d.execution_date,
                "regime": d.regime,
                "vix": d.vix,
            }
            for d in joined
        ],
        "warnings": warnings,
        "notes": (
            "Phase-1 timing-replay output stratified by VIX regime. Opportunities "
            "are signed in the convention of execution_timing_sensitivity_study.md "
            "(positive = offset is cheaper than the baseline). 'insufficient_sample' "
            "tags regimes with fewer than the configured threshold of days; do not "
            "make significance claims from those buckets."
        ),
    }
