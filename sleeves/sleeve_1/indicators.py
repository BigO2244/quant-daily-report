"""
sleeves/sleeve_1/indicators.py
-------------------------------
Pure momentum indicator functions for Sleeve 1.
No I/O, no side effects — only transforms on DataFrames.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def momentum_12_1(df: pd.DataFrame) -> pd.DataFrame:
    """
    12-1 month price momentum: 12-month total return skipping the most recent
    month, to avoid the well-documented short-term reversal effect.

    Requires: date, ticker, close columns.
    Adds:     ret_252d, ret_21d, mom_12_1
    """
    result = df.copy().sort_values(["ticker", "date"])
    result["ret_252d"] = result.groupby("ticker")["close"].pct_change(252)
    result["ret_21d"] = result.groupby("ticker")["close"].pct_change(21)
    # 12-1 = compound 12m return divided out by last 1m return
    result["mom_12_1"] = (1.0 + result["ret_252d"]) / (
        1.0 + result["ret_21d"].clip(lower=-0.99)
    ) - 1.0
    return result


def dual_ma_filter(df: pd.DataFrame, fast: int = 50, slow: int = 200) -> pd.DataFrame:
    """
    Add 50-day and 200-day simple moving averages and an uptrend flag.

    uptrend flag = price > 200d SMA AND price > 50d SMA AND 50d SMA > 200d SMA.
    Adds: sma_50, sma_200, above_200d, above_50d, ma_uptrend
    """
    result = df.copy().sort_values(["ticker", "date"])
    result[f"sma_{fast}"] = result.groupby("ticker")["close"].transform(
        lambda x: x.rolling(fast, min_periods=fast // 2).mean()
    )
    result[f"sma_{slow}"] = result.groupby("ticker")["close"].transform(
        lambda x: x.rolling(slow, min_periods=slow // 2).mean()
    )
    result["above_200d"] = result["close"] > result[f"sma_{slow}"]
    result["above_50d"] = result["close"] > result[f"sma_{fast}"]
    result["ma_uptrend"] = (
        result["above_200d"]
        & result["above_50d"]
        & (result[f"sma_{fast}"] > result[f"sma_{slow}"])
    )
    return result


def atr_normalized_momentum(df: pd.DataFrame, atr_window: int = 14) -> pd.DataFrame:
    """
    ATR-normalized momentum: mom_12_1 / (ATR / price).

    A high mom_12_1 divided by a high ATR% is a tighter risk-adjusted signal.
    Adds: atr, atr_pct, mom_atr_norm
    """
    result = df.copy().sort_values(["ticker", "date"])

    high = result["high"] if "high" in result.columns else result["close"]
    low = result["low"] if "low" in result.columns else result["close"]
    prev_close = result.groupby("ticker")["close"].shift(1)

    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    result["atr"] = tr.groupby(result["ticker"]).transform(
        lambda x: x.rolling(atr_window, min_periods=atr_window // 2).mean()
    )
    result["atr_pct"] = result["atr"] / result["close"].clip(lower=0.01)

    if "mom_12_1" in result.columns:
        result["mom_atr_norm"] = result["mom_12_1"] / result["atr_pct"].clip(lower=0.001)
    else:
        result["mom_atr_norm"] = 0.0

    return result


def sector_relative_momentum(df: pd.DataFrame) -> pd.DataFrame:
    """
    Sector-relative momentum: stock's mom_12_1 minus the equal-weight
    average mom_12_1 across all stocks in its sector on the same date.

    Requires: date, ticker, sector, mom_12_1 columns.
    Adds: mom_sector_rel
    """
    result = df.copy()

    if "sector" not in result.columns or "mom_12_1" not in result.columns:
        result["mom_sector_rel"] = 0.0
        return result

    sector_avg = result.groupby(["date", "sector"])["mom_12_1"].transform("mean")
    result["mom_sector_rel"] = result["mom_12_1"] - sector_avg
    return result


def realized_volatility(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """
    Annualized realized volatility for inverse-vol position sizing.

    Adds: daily_ret, realized_vol
    """
    result = df.copy().sort_values(["ticker", "date"])
    result["daily_ret"] = result.groupby("ticker")["close"].pct_change()
    result["realized_vol"] = (
        result.groupby("ticker")["daily_ret"]
        .transform(lambda x: x.rolling(window, min_periods=window // 2).std())
        * np.sqrt(252)
    ).clip(lower=0.05, upper=3.0)
    return result


def uptrend_ratio(df: pd.DataFrame, asof_date=None) -> float:
    """
    Fraction of the universe in an uptrend (above 200d MA) on asof_date.
    Used for the breadth risk filter.
    """
    if asof_date is None:
        asof_date = df["date"].max()
    snap = df[df["date"] == pd.to_datetime(asof_date)]
    if snap.empty or "above_200d" not in snap.columns:
        return 1.0
    return float(snap["above_200d"].mean())
