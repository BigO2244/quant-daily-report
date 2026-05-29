"""Deterministic fill-price model for the execution-timing sensitivity study.

This module is intentionally pure: every function takes data in, returns
data out, and never reads or writes a file. It is the heart of the replay
engine and the place that must enforce the no-look-ahead contract.

No-look-ahead contract
----------------------
For a simulated execution at time ``sim_ts`` we may only use bars whose
``bar_start_ts >= sim_ts``. The reference price is the **open** of the
chosen bar — never the high, low, or close, since those are not yet
observable at the bar's start. :func:`pick_reference_bar` enforces this
and refuses to return any bar that fails the cutoff.

Signed-cost math
----------------
Per the spec (``specs/execution_timing_sensitivity_study.md §1.3``):

* ``q_i = +shares`` for BUY, ``-shares`` for SELL
* ``fill_i(Δ)`` is the per-share modeled fill price at offset Δ
* ``cost(d, Δ) = Σ_i q_i × fill_i(Δ)`` — signed cash outlay (BUYs
  add, SELLs subtract)
* ``opportunity(d, Δ) = cost(d, baseline) − cost(d, Δ)`` — positive
  means Δ is cheaper than the baseline (less cash out for the same
  order set).
* gross notional = ``Σ_i |q_i × ref_price_i|``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

# Phase 1 study offsets — minutes past the regular-session open (09:30 ET).
DEFAULT_OFFSETS_MINUTES: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 10)
DEFAULT_BASELINE_OFFSET_MINUTES: int = 5


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OffsetSpec:
    minutes: int

    @property
    def label(self) -> str:
        return f"T+{self.minutes}m"


@dataclass(frozen=True)
class FillRecord:
    """Modeled fill at a single (trade, offset). All timestamps are ISO UTC.

    ``bar_start_ts`` is guaranteed to be ``>= asof_cutoff_ts``; that is the
    no-look-ahead invariant.
    """
    offset_label: str
    offset_minutes: int
    simulated_execution_ts: str
    asof_cutoff_ts: str
    bar_start_ts: Optional[str]
    ref_price: Optional[float]
    modeled_fill: Optional[float]
    bar_source: Optional[str]
    bar_feed: Optional[str]
    status: str  # "ok" | "no_bar_in_window"


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def market_open_et(trade_date: str) -> dt.datetime:
    """Return 09:30:00 ET on ``trade_date`` as a tz-aware datetime."""
    day = dt.date.fromisoformat(trade_date)
    return dt.datetime.combine(day, dt.time(9, 30), tzinfo=ET)


def simulated_execution_ts(trade_date: str, offset_minutes: int) -> dt.datetime:
    """Return the simulated execution timestamp at 09:30 + offset (ET)."""
    return market_open_et(trade_date) + dt.timedelta(minutes=offset_minutes)


def _to_iso_utc(value: dt.datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Reference-bar selection (no look-ahead)
# ---------------------------------------------------------------------------


def pick_reference_bar(
    bars: pd.DataFrame,
    sim_ts: dt.datetime,
) -> Optional[Mapping[str, Any]]:
    """Return the first bar with ``bar_start_ts >= sim_ts``.

    A ``ValueError`` is raised if the returned bar would violate the no-look-
    ahead contract — this is defense in depth; the filter logic alone
    already enforces it, but the assertion guards against future refactors
    silently breaking the invariant.
    """
    if bars is None or bars.empty or "bar_start_ts" not in bars.columns:
        return None

    sim_utc = sim_ts.astimezone(UTC)
    timestamps = pd.to_datetime(bars["bar_start_ts"], utc=True, errors="coerce")
    valid = bars.assign(_ts=timestamps).dropna(subset=["_ts"])
    eligible = valid[valid["_ts"] >= sim_utc].sort_values("_ts", kind="mergesort")
    if eligible.empty:
        return None
    row = eligible.iloc[0]

    chosen_ts = row["_ts"].to_pydatetime()
    if chosen_ts < sim_utc:
        # Should be unreachable thanks to the filter above.
        raise ValueError(
            f"no_look_ahead_violation: chosen bar_start_ts={chosen_ts.isoformat()} "
            f"< simulated_execution_ts={sim_utc.isoformat()}"
        )
    return {
        "bar_start_ts": _to_iso_utc(chosen_ts),
        "open": _coerce_float(row.get("open")),
        "high": _coerce_float(row.get("high")),
        "low": _coerce_float(row.get("low")),
        "close": _coerce_float(row.get("close")),
        "volume": _coerce_float(row.get("volume")),
        "source": _coerce_str(row.get("source")),
        "feed": _coerce_str(row.get("feed")),
    }


# ---------------------------------------------------------------------------
# Fill model
# ---------------------------------------------------------------------------


def model_fill(side: str, ref_price: float) -> float:
    """Phase-1 fill model: identity on the bar's open.

    Future revisions will add a half-spread term and an open-volatility
    decay factor (spec §1.2). Keeping this a one-liner now means every
    spread/impact assumption added later is locally visible and testable.
    """
    if side not in {"BUY", "SELL"}:
        raise ValueError(f"unknown_side: {side!r}")
    return float(ref_price)


def signed_shares(side: str, shares: int | float) -> float:
    if side == "BUY":
        return float(shares)
    if side == "SELL":
        return -float(shares)
    raise ValueError(f"unknown_side: {side!r}")


# ---------------------------------------------------------------------------
# Trade-level + day-level rollups
# ---------------------------------------------------------------------------


def replay_trade(
    *,
    side: str,
    shares: int | float,
    bars: pd.DataFrame,
    trade_date: str,
    offsets_minutes: Sequence[int] = DEFAULT_OFFSETS_MINUTES,
) -> dict[str, FillRecord]:
    """Compute :class:`FillRecord` per offset for a single trade."""
    records: dict[str, FillRecord] = {}
    for offset_minutes in offsets_minutes:
        sim_dt = simulated_execution_ts(trade_date, offset_minutes)
        sim_iso = _to_iso_utc(sim_dt)
        offset_label = f"T+{offset_minutes}m"
        bar = pick_reference_bar(bars, sim_dt)
        if bar is None or bar.get("open") is None:
            records[offset_label] = FillRecord(
                offset_label=offset_label,
                offset_minutes=offset_minutes,
                simulated_execution_ts=sim_iso,
                asof_cutoff_ts=sim_iso,
                bar_start_ts=None,
                ref_price=None,
                modeled_fill=None,
                bar_source=None,
                bar_feed=None,
                status="no_bar_in_window",
            )
            continue
        ref_price = float(bar["open"])
        records[offset_label] = FillRecord(
            offset_label=offset_label,
            offset_minutes=offset_minutes,
            simulated_execution_ts=sim_iso,
            asof_cutoff_ts=sim_iso,
            bar_start_ts=bar["bar_start_ts"],
            ref_price=ref_price,
            modeled_fill=model_fill(side, ref_price),
            bar_source=bar.get("source"),
            bar_feed=bar.get("feed"),
            status="ok",
        )
    return records


def compute_day_costs(
    *,
    trades: Sequence[Mapping[str, Any]],
    fills_by_trade: Sequence[dict[str, FillRecord]],
    offsets_minutes: Sequence[int] = DEFAULT_OFFSETS_MINUTES,
) -> dict[str, dict[str, Any]]:
    """Compute signed cost + gross notional per offset for a single day.

    A trade is *fully fillable* at an offset only if every requested offset
    yields ``status == "ok"``. If any offset has a missing bar, that
    offset's cost/notional are recorded as ``None`` and the trade is
    excluded from that offset's totals so the headline numbers are not
    silently biased.
    """
    out: dict[str, dict[str, Any]] = {}
    for offset_minutes in offsets_minutes:
        label = f"T+{offset_minutes}m"
        total_cost = 0.0
        gross_notional = 0.0
        fillable_trades = 0
        unavailable_trades = 0
        for trade, fills in zip(trades, fills_by_trade):
            fill = fills.get(label)
            if fill is None or fill.status != "ok" or fill.modeled_fill is None:
                unavailable_trades += 1
                continue
            q = signed_shares(str(trade.get("side", "")).strip().upper(), trade.get("shares", 0))
            total_cost += q * fill.modeled_fill
            gross_notional += abs(q * (fill.ref_price or 0.0))
            fillable_trades += 1
        out[label] = {
            "cost_usd": round(total_cost, 6) if fillable_trades else None,
            "gross_notional_usd": round(gross_notional, 6) if fillable_trades else None,
            "fillable_trades": fillable_trades,
            "unavailable_trades": unavailable_trades,
        }
    return out


def compute_opportunity_vs_baseline(
    *,
    day_costs: Mapping[str, Mapping[str, Any]],
    baseline_offset_minutes: int = DEFAULT_BASELINE_OFFSET_MINUTES,
) -> dict[str, dict[str, Any]]:
    """Compute opportunity vs. baseline per offset.

    Only offsets whose ``fillable_trades`` *and* the baseline's
    ``fillable_trades`` are equal AND non-zero get a numeric opportunity;
    otherwise the day's offset entry is ``{usd: None, bps: None,
    reason: ...}``. This prevents apples-to-oranges comparisons between
    different trade subsets.
    """
    baseline_label = f"T+{baseline_offset_minutes}m"
    baseline = day_costs.get(baseline_label) or {}
    baseline_cost = baseline.get("cost_usd")
    baseline_fillable = baseline.get("fillable_trades", 0)
    out: dict[str, dict[str, Any]] = {}
    for offset_label, entry in day_costs.items():
        cost = entry.get("cost_usd")
        fillable = entry.get("fillable_trades", 0)
        gross = entry.get("gross_notional_usd")
        if (
            baseline_cost is None
            or cost is None
            or baseline_fillable == 0
            or fillable == 0
            or fillable != baseline_fillable
        ):
            out[offset_label] = {
                "opportunity_usd": None,
                "opportunity_bps": None,
                "reason": "trade_subset_mismatch_or_unavailable",
            }
            continue
        opportunity_usd = baseline_cost - cost
        opportunity_bps = (
            (opportunity_usd / gross) * 10_000.0 if gross else None
        )
        out[offset_label] = {
            "opportunity_usd": round(opportunity_usd, 6),
            "opportunity_bps": round(opportunity_bps, 6) if opportunity_bps is not None else None,
        }
    return out


# ---------------------------------------------------------------------------
# Cross-day aggregation helpers
# ---------------------------------------------------------------------------


def aggregate_offsets(
    per_day: Sequence[Mapping[str, Mapping[str, Any]]],
    metric_key: str,
) -> dict[str, dict[str, Optional[float]]]:
    """Compute simple cross-day mean/median/p10/p90/sum/n per offset.

    ``per_day`` is a list of mappings from offset_label → metric-dict;
    ``metric_key`` is the field within each metric-dict to aggregate.
    None values are skipped (excluded from the sample, not zeroed).
    """
    aggregates: dict[str, dict[str, Optional[float]]] = {}
    by_offset: dict[str, list[float]] = {}
    for day in per_day:
        for offset_label, entry in day.items():
            v = entry.get(metric_key) if isinstance(entry, Mapping) else None
            if v is None:
                continue
            by_offset.setdefault(offset_label, []).append(float(v))
    for offset_label, values in by_offset.items():
        if not values:
            aggregates[offset_label] = {
                "n": 0, "mean": None, "median": None, "p10": None, "p90": None, "sum": None,
            }
            continue
        s = pd.Series(values)
        aggregates[offset_label] = {
            "n": int(s.size),
            "mean": round(float(s.mean()), 6),
            "median": round(float(s.median()), 6),
            "p10": round(float(s.quantile(0.10)), 6),
            "p90": round(float(s.quantile(0.90)), 6),
            "sum": round(float(s.sum()), 6),
        }
    return aggregates


# ---------------------------------------------------------------------------
# Internal coercers
# ---------------------------------------------------------------------------


def _coerce_float(value: Any) -> Optional[float]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


def _coerce_str(value: Any) -> Optional[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value)
    return s or None


def fill_record_to_dict(record: FillRecord) -> dict[str, Any]:
    return {
        "offset_label": record.offset_label,
        "offset_minutes": record.offset_minutes,
        "simulated_execution_ts": record.simulated_execution_ts,
        "asof_cutoff_ts": record.asof_cutoff_ts,
        "bar_start_ts": record.bar_start_ts,
        "ref_price": record.ref_price,
        "modeled_fill": record.modeled_fill,
        "bar_source": record.bar_source,
        "bar_feed": record.bar_feed,
        "status": record.status,
    }
