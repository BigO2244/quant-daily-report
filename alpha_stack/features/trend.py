"""
Alpha Stack — Trend / Momentum Features
=========================================
Computes all trend and momentum signals needed by the Trend sleeve.

Signals computed:
    r12_1      — 12-1 momentum (252-day return, skip last 21 days)
    r6_1       — 6-1 momentum (126-day return, skip last 21 days)
    r3_1       — 3-1 momentum (63-day return, skip last 21 days)
    trend_flag — 1 if EMA50 > EMA200, else 0
    ema50      — 50-day EMA
    ema200     — 200-day EMA
    ema50_ema200_ratio — (EMA50/EMA200) - 1
    price_ema200_ratio — (Close/EMA200) - 1
    atr20_pct  — 20-day ATR as % of close (volatility adjustment denominator)

All signals are computed per-ticker from price history (no fundamentals required).
PIT-safe: only history on or before as_of_date is used.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Lookback needed for EMA(200) to stabilise
_EMA200_WARMUP = 300  # More than 200 for proper initialisation


def compute_trend_features(
    prices: pd.DataFrame,
    as_of_date: date | str,
    r12_1_lookback: int = 252,
    r6_1_lookback: int = 126,
    r3_1_lookback: int = 63,
    skip_days: int = 21,
) -> pd.DataFrame:
    """
    Compute trend/momentum features cross-sectionally for all tickers.

    Parameters
    ----------
    prices : DataFrame
        Long-format price history with columns:
        [date, ticker, open, high, low, close, volume]
        Must include at least r12_1_lookback + skip_days + EMA warmup rows per ticker.
    as_of_date : date or str
        Compute features as of this date.
    r12_1_lookback : int
        Calendar days for 12-1 momentum.
    r6_1_lookback : int
        Calendar days for 6-1 momentum.
    r3_1_lookback : int
        Calendar days for 3-1 momentum.
    skip_days : int
        Number of most recent trading days to skip (reversal bias avoidance).

    Returns
    -------
    DataFrame with one row per ticker and columns:
        ticker, as_of_date, close, ema50, ema200,
        trend_flag, ema50_ema200_ratio, price_ema200_ratio,
        r12_1, r6_1, r3_1, atr20_pct,
        z_r12_1, z_r6_1, z_r3_1,  (cross-sectional z-scores)
        raw_score                   (pre-normalized composite)
    """
    if isinstance(as_of_date, str):
        as_of_date = pd.Timestamp(as_of_date)
    else:
        as_of_date = pd.Timestamp(as_of_date)

    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"])

    # Only use data on or before as_of_date (PIT enforcement)
    prices = prices[prices["date"] <= as_of_date].copy()
    prices = prices.sort_values(["ticker", "date"])

    records = []
    for ticker, group in prices.groupby("ticker"):
        rec = _compute_single_ticker(
            ticker, group, as_of_date, r12_1_lookback, r6_1_lookback,
            r3_1_lookback, skip_days
        )
        if rec is not None:
            records.append(rec)

    if not records:
        return _empty_features()

    df = pd.DataFrame(records)

    # Cross-sectional z-scores (require at least 3 tickers)
    if len(df) >= 3:
        for col in ["r12_1", "r6_1", "r3_1"]:
            df[f"z_{col}"] = _zscore(df[col])
    else:
        for col in ["r12_1", "r6_1", "r3_1"]:
            df[f"z_{col}"] = 0.0

    return df.reset_index(drop=True)


def compute_raw_trend_score(
    df: pd.DataFrame,
    w_r12_1: float = 0.45,
    w_r6_1: float = 0.30,
    w_r3_1: float = 0.15,
    w_trend_flag: float = 0.10,
) -> pd.Series:
    """
    Compute raw trend score: weighted combination of z-scored momentum signals.

    S_trend_raw = w_r12_1*z(r12_1) + w_r6_1*z(r6_1) + w_r3_1*z(r3_1) + w_trend_flag*trend_flag

    Then volatility-adjusted:
    S_trend_adj = S_trend_raw / max(atr20_pct, 0.01)

    Returns
    -------
    Series indexed like df, with the raw (pre-normalised) composite score.
    """
    raw = (
        w_r12_1 * df.get("z_r12_1", 0)
        + w_r6_1 * df.get("z_r6_1", 0)
        + w_r3_1 * df.get("z_r3_1", 0)
        + w_trend_flag * df.get("trend_flag", 0)
    )

    # Volatility adjustment: divide by ATR%
    atr_adj = df.get("atr20_pct", pd.Series([0.01] * len(df), index=df.index))
    atr_adj = atr_adj.clip(lower=0.01)

    return (raw / atr_adj).rename("raw_score")


def normalise_to_percentile(series: pd.Series) -> pd.Series:
    """
    Rank-normalize a series to [0, 100] percentile rank.
    Used to produce the final sleeve score.
    """
    return series.rank(pct=True) * 100


# ------------------------------------------------------------------ #
# Per-ticker computation                                               #
# ------------------------------------------------------------------ #

def _compute_single_ticker(
    ticker: str,
    group: pd.DataFrame,
    as_of_date: pd.Timestamp,
    r12_1_lookback: int,
    r6_1_lookback: int,
    r3_1_lookback: int,
    skip_days: int,
) -> Optional[dict]:
    """Compute features for a single ticker. Returns None if insufficient history."""
    group = group.sort_values("date").reset_index(drop=True)

    if len(group) < 30:
        return None

    close = group["close"]
    high = group.get("high", close)
    low = group.get("low", close)

    # EMAs
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()

    # ATR (20-day)
    if "high" in group.columns and "low" in group.columns:
        prev_close = close.shift(1)
        tr = pd.concat([
            group["high"] - group["low"],
            (group["high"] - prev_close).abs(),
            (group["low"] - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr20 = tr.ewm(span=20, adjust=False).mean()
    else:
        atr20 = close.rolling(20).std()  # fallback

    # Latest values (on as_of_date)
    last_close = float(close.iloc[-1])
    last_ema50 = float(ema50.iloc[-1])
    last_ema200 = float(ema200.iloc[-1])
    last_atr20 = float(atr20.iloc[-1]) if pd.notna(atr20.iloc[-1]) else last_close * 0.02

    # Trend flag
    trend_flag = 1.0 if last_ema50 > last_ema200 else 0.0

    # Ratios
    ema50_ema200_ratio = (last_ema50 / last_ema200 - 1) if last_ema200 > 0 else 0.0
    price_ema200_ratio = (last_close / last_ema200 - 1) if last_ema200 > 0 else 0.0
    atr20_pct = last_atr20 / last_close if last_close > 0 else 0.02

    # Momentum: use skip_days to avoid most-recent-month reversal
    def _momentum(lookback_days: int) -> Optional[float]:
        """Return skip-adjusted momentum."""
        min_close = close.iloc[-skip_days - 1] if len(close) > skip_days else None
        prev_idx = -(skip_days + 1)  # T-skip_days close
        hist_idx = max(-(lookback_days + 1), -len(close))
        if len(close) < abs(hist_idx) + skip_days:
            return None
        p_start = float(close.iloc[hist_idx])
        p_end = float(close.iloc[prev_idx if abs(prev_idx) <= len(close) else 0])
        if p_start <= 0:
            return None
        return p_end / p_start - 1

    r12_1 = _momentum(r12_1_lookback) or 0.0
    r6_1 = _momentum(r6_1_lookback) or 0.0
    r3_1 = _momentum(r3_1_lookback) or 0.0

    return {
        "ticker": ticker,
        "as_of_date": as_of_date,
        "close": last_close,
        "ema50": last_ema50,
        "ema200": last_ema200,
        "trend_flag": trend_flag,
        "ema50_ema200_ratio": round(ema50_ema200_ratio, 6),
        "price_ema200_ratio": round(price_ema200_ratio, 6),
        "r12_1": round(r12_1, 6),
        "r6_1": round(r6_1, 6),
        "r3_1": round(r3_1, 6),
        "atr20_pct": round(atr20_pct, 6),
    }


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _zscore(s: pd.Series) -> pd.Series:
    """Cross-sectional z-score, robust to NaN."""
    mu = s.mean()
    sigma = s.std()
    if sigma == 0 or pd.isna(sigma):
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sigma


def _empty_features() -> pd.DataFrame:
    cols = [
        "ticker", "as_of_date", "close", "ema50", "ema200",
        "trend_flag", "ema50_ema200_ratio", "price_ema200_ratio",
        "r12_1", "r6_1", "r3_1", "atr20_pct",
        "z_r12_1", "z_r6_1", "z_r3_1",
    ]
    return pd.DataFrame(columns=cols)
