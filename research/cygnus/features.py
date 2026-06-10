"""FR-051 Cygnus Stage 2 — feature engineering (RESEARCH_ONLY / NON_EXECUTIONAL).

All features are computed from data available at or before the decision close `t`
(the event's `availability_date`). Forward returns are computed separately and
used only as backtest targets, never as inputs. Price data is the repo's
adjusted-close matrix (split/dividend adjusted); the caller is responsible for
slicing it so the 2025+ holdout is never visible (FR-051 addendum A4).
"""
from __future__ import annotations

from typing import Any

import pandas as pd

REVENUE_TAGS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
)


def _pos_on_or_before(index: pd.DatetimeIndex, date: pd.Timestamp) -> int | None:
    """Integer position of the last index entry on or before `date`."""
    loc = index.searchsorted(date, side="right") - 1
    return int(loc) if loc >= 0 else None


def close_to_close_return(series: pd.Series, end_pos: int, lookback: int) -> float | None:
    start_pos = end_pos - lookback
    if start_pos < 0 or end_pos >= len(series):
        return None
    a, b = series.iloc[start_pos], series.iloc[end_pos]
    if pd.isna(a) or pd.isna(b) or a <= 0:
        return None
    return float(b) / float(a) - 1.0


def forward_return(series: pd.Series, t_pos: int, horizon_td: int) -> float | None:
    """Close-to-close return from position t to t+horizon (None if it would
    read beyond the available (holdout-excluded) data)."""
    end_pos = t_pos + horizon_td
    if t_pos < 0 or end_pos >= len(series):
        return None
    a, b = series.iloc[t_pos], series.iloc[end_pos]
    if pd.isna(a) or pd.isna(b) or a <= 0:
        return None
    return float(b) / float(a) - 1.0


def event_reaction_abnormal_return(
    ticker_series: pd.Series, spy_series: pd.Series, t_pos: int
) -> float | None:
    """First-eligible reaction: 1-day close-to-close at the availability close
    minus SPY's same-day return (A3: unchanged from canonical definition)."""
    r = close_to_close_return(ticker_series, t_pos, 1)
    m = close_to_close_return(spy_series, t_pos, 1)
    if r is None or m is None:
        return None
    return r - m


def drift_confirmation(ticker_series: pd.Series, t_pos: int) -> float | None:
    """Fraction of {5D, 10D} trailing trends that are positive at the decision
    close — proxy for 'price above event reaction close and positive 5D/10D trend'."""
    r5 = close_to_close_return(ticker_series, t_pos, 5)
    r10 = close_to_close_return(ticker_series, t_pos, 10)
    if r5 is None or r10 is None:
        return None
    return (float(r5 > 0) + float(r10 > 0)) / 2.0


def pre_event_runup(ticker_series: pd.Series, t_pos: int) -> float | None:
    """20-day return ending the day before the reaction window (for the run-up
    penalty); excludes the reaction day itself."""
    return close_to_close_return(ticker_series, t_pos - 1, 20)


def revenue_yoy_acceleration(fund_df: pd.DataFrame, as_of: pd.Timestamp) -> float | None:
    """YoY revenue growth of the latest PIT-available quarter minus the prior
    quarter's YoY growth, using only values filed on or before `as_of` and the
    earliest filing per period (no restatement look-ahead)."""
    if fund_df is None or fund_df.empty:
        return None
    df = fund_df[fund_df["tag"].isin(REVENUE_TAGS)].copy()
    if df.empty:
        return None
    df["period_end"] = pd.to_datetime(df["period_end"], errors="coerce")
    df["period_start"] = pd.to_datetime(df["period_start"], errors="coerce")
    df["filed_date"] = pd.to_datetime(df["filed_date"], errors="coerce")
    df = df.dropna(subset=["period_end", "filed_date", "value"])
    df = df[df["filed_date"] <= as_of]  # PIT availability
    # Quarterly only (~80-100 day periods).
    days = (df["period_end"] - df["period_start"]).dt.days
    df = df[(days >= 80) & (days <= 100)]
    if df.empty:
        return None
    # Prefer tag priority, then earliest filing per period_end (original filing).
    df["tag_rank"] = df["tag"].map({t: i for i, t in enumerate(REVENUE_TAGS)})
    df = df.sort_values(["period_end", "tag_rank", "filed_date"])
    by_period = df.groupby("period_end", as_index=False).first()
    by_period = by_period.sort_values("period_end").reset_index(drop=True)

    def _yoy(period_end: pd.Timestamp) -> float | None:
        cur = by_period[by_period["period_end"] == period_end]
        if cur.empty:
            return None
        prior_end = period_end - pd.DateOffset(years=1)
        # nearest prior-year quarter within +/- 20 days
        cand = by_period[(by_period["period_end"] - prior_end).abs() <= pd.Timedelta(days=20)]
        if cand.empty:
            return None
        v0, v1 = float(cand.iloc[0]["value"]), float(cur.iloc[0]["value"])
        if v0 == 0:
            return None
        return v1 / v0 - 1.0

    if len(by_period) < 2:
        return None
    latest_end = by_period.iloc[-1]["period_end"]
    prev_end = by_period.iloc[-2]["period_end"]
    g_latest = _yoy(latest_end)
    g_prev = _yoy(prev_end)
    if g_latest is None or g_prev is None:
        return None
    return g_latest - g_prev
