"""
Alpha Stack — Volatility Features
====================================
Realised volatility and ATR-based features used across all sleeves.

Computed per-ticker:
    realized_vol_20d  — 20-day annualised realised volatility
    realized_vol_60d  — 60-day annualised realised volatility
    atr_14            — 14-day Average True Range
    atr_pct_14        — ATR as % of close price
    vol_regime_label  — crude vol regime based on realised vol level

Used by:
    - Trend sleeve: vol-adjusted scoring + inverse-vol sizing
    - Allocator: vol-aware gross exposure scaling
    - Attribution: rolling vol monitoring
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_ANNUALISE = np.sqrt(252)


def compute_volatility_features(
    prices: pd.DataFrame,
    as_of_date: date | str,
    vol_lookback_short: int = 20,
    vol_lookback_long: int = 60,
    atr_period: int = 14,
) -> pd.DataFrame:
    """
    Compute volatility features cross-sectionally.

    Parameters
    ----------
    prices : DataFrame
        Long-format OHLCV with columns [date, ticker, open, high, low, close, volume].
    as_of_date : date or str
        Compute as of this date (PIT-safe filter applied).
    vol_lookback_short : int
    vol_lookback_long : int
    atr_period : int

    Returns
    -------
    DataFrame with one row per ticker and columns:
        ticker, realized_vol_20d, realized_vol_60d,
        atr_14, atr_pct_14, vol_regime_label
    """
    if isinstance(as_of_date, str):
        as_of_date = pd.Timestamp(as_of_date)
    else:
        as_of_date = pd.Timestamp(as_of_date)

    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"])
    prices = prices[prices["date"] <= as_of_date].sort_values(["ticker", "date"])

    records = []
    for ticker, group in prices.groupby("ticker"):
        rec = _compute_vol_single(
            ticker, group, vol_lookback_short, vol_lookback_long, atr_period
        )
        if rec is not None:
            records.append(rec)

    if not records:
        return pd.DataFrame(columns=[
            "ticker", "realized_vol_20d", "realized_vol_60d",
            "atr_14", "atr_pct_14", "vol_regime_label"
        ])

    return pd.DataFrame(records).reset_index(drop=True)


def _compute_vol_single(
    ticker: str,
    group: pd.DataFrame,
    lookback_short: int,
    lookback_long: int,
    atr_period: int,
) -> Optional[dict]:
    if len(group) < lookback_short + 5:
        return None

    group = group.sort_values("date").reset_index(drop=True)
    close = group["close"]
    daily_ret = close.pct_change()

    # Realized vol
    rv_short = float(daily_ret.tail(lookback_short).std() * _ANNUALISE)
    rv_long = float(daily_ret.tail(lookback_long).std() * _ANNUALISE) if len(group) >= lookback_long else rv_short

    # ATR
    if "high" in group.columns and "low" in group.columns:
        prev_close = close.shift(1)
        tr = pd.concat([
            group["high"] - group["low"],
            (group["high"] - prev_close).abs(),
            (group["low"] - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr_val = float(tr.tail(atr_period).mean())
    else:
        atr_val = float(close.tail(atr_period).std())

    last_close = float(close.iloc[-1])
    atr_pct = atr_val / last_close if last_close > 0 else 0.02

    # Simple vol regime label
    if rv_short < 0.12:
        vol_regime = "low"
    elif rv_short < 0.20:
        vol_regime = "normal"
    elif rv_short < 0.35:
        vol_regime = "elevated"
    else:
        vol_regime = "high"

    return {
        "ticker": ticker,
        "realized_vol_20d": round(rv_short, 6),
        "realized_vol_60d": round(rv_long, 6),
        "atr_14": round(atr_val, 4),
        "atr_pct_14": round(atr_pct, 6),
        "vol_regime_label": vol_regime,
    }
