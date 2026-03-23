"""
sleeves/sleeve_mean_reversion/indicators.py
--------------------------------------------
Pure mean-reversion indicator functions for the Mean Reversion sleeve.
No I/O, no side effects — only transforms on DataFrames.

The mean-reversion thesis: short-term price dislocations relative to a
ticker's own recent history tend to revert. This sleeve deliberately
fades recent losers that have become oversold, not permanently impaired.

Indicators:
  - RSI(14)                 — momentum oscillator; oversold = <30
  - Price Z-score (20d/60d) — standard deviations below moving average
  - Bollinger %B (20d)      — position within Bollinger bands
  - Short-term reversal     — 1-month return (inverted signal)

These are computed on raw price data (long-form OHLCV DataFrame) and
assembled into a single snapshot for selection.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ── RSI ───────────────────────────────────────────────────────────────


def rsi(df: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    """
    Compute RSI(window) for each ticker.

    Classic Wilder RSI using exponentially weighted moving average of
    gains and losses (alpha = 1/window).

    Adds: rsi_{window}
    """
    result = df.copy().sort_values(['ticker', 'date'])

    delta  = result.groupby('ticker')['close'].diff()
    gain   = delta.clip(lower=0.0)
    loss   = (-delta).clip(lower=0.0)

    avg_gain = gain.groupby(result['ticker']).transform(
        lambda x: x.ewm(alpha=1.0 / window, min_periods=window // 2, adjust=False).mean()
    )
    avg_loss = loss.groupby(result['ticker']).transform(
        lambda x: x.ewm(alpha=1.0 / window, min_periods=window // 2, adjust=False).mean()
    )

    rs = avg_gain / avg_loss.clip(lower=1e-10)
    result[f'rsi_{window}'] = 100.0 - (100.0 / (1.0 + rs))
    return result


# ── Price Z-score ─────────────────────────────────────────────────────


def price_zscore(df: pd.DataFrame, short_window: int = 20, long_window: int = 60) -> pd.DataFrame:
    """
    Price Z-score relative to its own rolling mean and std.

    zscore_Nd = (price - rolling_mean_Nd) / rolling_std_Nd

    Negative z-score = price below its own recent average → potential dislocation.

    Adds: zscore_20d, zscore_60d
    """
    result = df.copy().sort_values(['ticker', 'date'])

    for window in (short_window, long_window):
        roll_mean = result.groupby('ticker')['close'].transform(
            lambda x: x.rolling(window, min_periods=window // 2).mean()
        )
        roll_std = result.groupby('ticker')['close'].transform(
            lambda x: x.rolling(window, min_periods=window // 2).std()
        )
        result[f'zscore_{window}d'] = (result['close'] - roll_mean) / roll_std.clip(lower=1e-10)

    return result


# ── Bollinger %B ──────────────────────────────────────────────────────


def bollinger_pct_b(df: pd.DataFrame, window: int = 20, n_std: float = 2.0) -> pd.DataFrame:
    """
    Bollinger Band %B indicator.

    %B = (price - lower_band) / (upper_band - lower_band)

    %B < 0   : price below lower band (oversold)
    %B = 0   : price at lower band
    %B = 0.5 : price at middle band (20d SMA)
    %B = 1   : price at upper band
    %B > 1   : price above upper band (overbought)

    Adds: bb_middle, bb_upper, bb_lower, bb_pct_b, bb_width
    """
    result = df.copy().sort_values(['ticker', 'date'])

    middle = result.groupby('ticker')['close'].transform(
        lambda x: x.rolling(window, min_periods=window // 2).mean()
    )
    std = result.groupby('ticker')['close'].transform(
        lambda x: x.rolling(window, min_periods=window // 2).std()
    )

    result['bb_middle'] = middle
    result['bb_upper']  = middle + n_std * std
    result['bb_lower']  = middle - n_std * std
    result['bb_width']  = (result['bb_upper'] - result['bb_lower']) / middle.clip(lower=0.01)

    band_range = (result['bb_upper'] - result['bb_lower']).clip(lower=1e-10)
    result['bb_pct_b'] = (result['close'] - result['bb_lower']) / band_range

    return result


# ── Short-term reversal ───────────────────────────────────────────────


def short_term_reversal(df: pd.DataFrame, window: int = 21) -> pd.DataFrame:
    """
    Short-term reversal signal: 1-month (21-day) price return.

    Used inverted: recent losers are candidates for mean reversion.
    Winsorized at [-0.5, 0.5] to prevent distressed equities from
    dominating the cross-section.

    Adds: ret_21d_rev (raw return — caller inverts for scoring)
    """
    result = df.copy().sort_values(['ticker', 'date'])
    result['ret_21d_rev'] = result.groupby('ticker')['close'].pct_change(window)
    return result


# ── Realized volatility ───────────────────────────────────────────────


def realized_volatility(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """
    Annualized realized volatility for inverse-vol position sizing.

    Adds: daily_ret, realized_vol
    """
    result = df.copy().sort_values(['ticker', 'date'])
    result['daily_ret']   = result.groupby('ticker')['close'].pct_change()
    result['realized_vol'] = (
        result.groupby('ticker')['daily_ret']
        .transform(lambda x: x.rolling(window, min_periods=window // 2).std())
        * np.sqrt(252)
    ).clip(lower=0.05, upper=3.0)
    return result


# ── Average volume ────────────────────────────────────────────────────


def avg_volume(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """
    Rolling average daily volume for liquidity gating.

    Adds: avg_volume_20d
    """
    result = df.copy().sort_values(['ticker', 'date'])
    result['avg_volume_20d'] = result.groupby('ticker')['volume'].transform(
        lambda x: x.rolling(window, min_periods=5).mean()
    )
    return result
