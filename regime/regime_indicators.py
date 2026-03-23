"""
regime_indicators.py
--------------------
Computes the raw and EWM-smoothed indicator values that feed the
regime state machine.  This module is pure data transformation —
no state logic lives here.

Inputs:
    spy_prices  : DataFrame with columns [date, close] for SPY
    vix_prices  : DataFrame with columns [date, close] for ^VIX
    universe_prices: DataFrame with columns [date, ticker, close] —
                     your existing 200-ticker universe
    fred_data   : Wide DataFrame indexed by date with columns:
                  [yield_curve_2s10s, hy_oas, fed_funds]
                  (produced by FredClient.fetch_many)

Output (from build_indicators):
    DataFrame indexed by date with one column per raw indicator
    and one EWM-smoothed companion (suffixed _ewm).
"""

import logging
import numpy as np
import pandas as pd

from regime.regime_config import EWM_SPANS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_indicators(
    spy_prices: pd.DataFrame,
    vix_prices: pd.DataFrame,
    universe_prices: pd.DataFrame,
    fred_data: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build the full indicator table used by the regime classifier.

    Returns a DataFrame indexed by date (trading days) with columns:

    TREND dimension
        spy_vs_50d          : SPY close / 50d SMA - 1  (+ means above)
        spy_vs_200d         : SPY close / 200d SMA - 1
        ma50_vs_ma200       : 50d SMA / 200d SMA - 1
        spy_ret_63d         : SPY 63-day total return

    VOLATILITY dimension
        vix                 : raw VIX close
        vix_vs_ma63         : VIX / 63d VIX SMA - 1
        vix_spike_10d       : VIX % change over 10 days

    BREADTH dimension
        pct_above_200d      : % of universe stocks above their 200d SMA
        pct_above_50d       : % of universe stocks above their 50d SMA
        adv_decline_cum     : cumulative advance-decline line (normalized)

    MACRO dimension
        yield_curve_2s10s   : 10Y-2Y spread in pct pts (from FRED)
        hy_oas              : HY OAS in bps (from FRED)
        fed_funds           : Fed funds rate (from FRED)

    Each column also has an _ewm companion smoothed per EWM_SPANS config.
    """
    spy  = _prep_prices(spy_prices,  "spy")
    vix  = _prep_prices(vix_prices,  "vix")
    uni  = _prep_universe(universe_prices)
    fred = _prep_fred(fred_data)

    trend_df      = _compute_trend(spy)
    vol_df        = _compute_volatility(vix)
    breadth_df    = _compute_breadth(uni)
    macro_df      = _compute_macro(fred)

    # Align all dimensions to a common trading-day index
    combined = (
        trend_df
        .join(vol_df,     how="outer")
        .join(breadth_df, how="outer")
        .join(macro_df,   how="outer")
    )
    combined = combined.sort_index()

    # Forward-fill macro/FRED data (published on business days, not every day)
    macro_cols = [c for c in combined.columns if c in macro_df.columns]
    combined[macro_cols] = combined[macro_cols].ffill(limit=5)

    # Add EWM-smoothed companions
    combined = _add_ewm_columns(combined)

    logger.info(
        "Regime indicators built: %d trading days, %d columns",
        len(combined), len(combined.columns)
    )
    return combined


# ---------------------------------------------------------------------------
# Trend dimension
# ---------------------------------------------------------------------------

def _compute_trend(spy: pd.DataFrame) -> pd.DataFrame:
    df = spy[["close"]].copy()

    df["ma50"]  = df["close"].rolling(50,  min_periods=40).mean()
    df["ma200"] = df["close"].rolling(200, min_periods=150).mean()

    out = pd.DataFrame(index=df.index)
    out["spy_vs_50d"]    = df["close"] / df["ma50"]  - 1
    out["spy_vs_200d"]   = df["close"] / df["ma200"] - 1
    out["ma50_vs_ma200"] = df["ma50"]  / df["ma200"] - 1
    out["spy_ret_63d"]   = df["close"].pct_change(63)

    return out


# ---------------------------------------------------------------------------
# Volatility dimension
# ---------------------------------------------------------------------------

def _compute_volatility(vix: pd.DataFrame) -> pd.DataFrame:
    df = vix[["close"]].copy()
    df.columns = ["vix"]

    out = pd.DataFrame(index=df.index)
    out["vix"]           = df["vix"]
    out["vix_vs_ma63"]   = df["vix"] / df["vix"].rolling(63, min_periods=40).mean() - 1
    out["vix_spike_10d"] = df["vix"].pct_change(10)

    return out


# ---------------------------------------------------------------------------
# Breadth dimension
# ---------------------------------------------------------------------------

def _compute_breadth(uni: pd.DataFrame) -> pd.DataFrame:
    """
    uni: long-form DataFrame with [date, ticker, close]
    """
    # Per-ticker rolling MAs
    uni = uni.sort_values(["ticker", "date"])
    uni["ma200"] = uni.groupby("ticker")["close"].transform(
        lambda s: s.rolling(200, min_periods=150).mean()
    )
    uni["ma50"] = uni.groupby("ticker")["close"].transform(
        lambda s: s.rolling(50, min_periods=40).mean()
    )

    uni["above_200d"] = (uni["close"] > uni["ma200"]).astype(float)
    uni["above_50d"]  = (uni["close"] > uni["ma50"]).astype(float)

    # Daily cross-sectional aggregation
    daily = uni.groupby("date").agg(
        pct_above_200d=("above_200d", "mean"),
        pct_above_50d=("above_50d",  "mean"),
        advancers=("above_200d", "sum"),
        decliners=("above_200d", lambda x: (x == 0).sum()),
    ).sort_index()

    # Advance-decline line: cumulative net advancers, normalized
    daily["net_adv"] = daily["advancers"] - daily["decliners"]
    adl_cum = daily["net_adv"].cumsum()
    # Normalize to 0-1 range over a 252-day rolling window to make it stationary
    roll_min = adl_cum.rolling(252, min_periods=126).min()
    roll_max = adl_cum.rolling(252, min_periods=126).max()
    denom = (roll_max - roll_min).replace(0, np.nan)
    daily["adv_decline_cum"] = (adl_cum - roll_min) / denom

    out = daily[["pct_above_200d", "pct_above_50d", "adv_decline_cum"]].copy()
    out.index = pd.to_datetime(out.index)
    return out


# ---------------------------------------------------------------------------
# Macro dimension
# ---------------------------------------------------------------------------

def _compute_macro(fred: pd.DataFrame) -> pd.DataFrame:
    """
    fred: wide DataFrame indexed by date with columns matching FRED_SERIES keys.
    Passes through as-is; smoothing happens in the EWM layer.
    """
    expected = ["yield_curve_2s10s", "hy_oas", "fed_funds"]
    missing = [c for c in expected if c not in fred.columns]
    if missing:
        logger.warning("FRED data missing columns: %s — will be NaN", missing)

    out = fred.reindex(columns=expected)
    out.index = pd.to_datetime(out.index)
    return out


# ---------------------------------------------------------------------------
# EWM smoothing
# ---------------------------------------------------------------------------

def _add_ewm_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each raw indicator column, add an EWM-smoothed companion (_ewm suffix)
    using the span defined in EWM_SPANS for the relevant dimension.
    """
    dimension_map = {
        # trend indicators → trend span
        "spy_vs_50d":       "trend",
        "spy_vs_200d":      "trend",
        "ma50_vs_ma200":    "trend",
        "spy_ret_63d":      "trend",
        # volatility indicators → volatility span
        "vix":              "volatility",
        "vix_vs_ma63":      "volatility",
        "vix_spike_10d":    "volatility",
        # breadth indicators → breadth span
        "pct_above_200d":   "breadth",
        "pct_above_50d":    "breadth",
        "adv_decline_cum":  "breadth",
        # macro indicators → macro span
        "yield_curve_2s10s": "macro",
        "hy_oas":            "macro",
        "fed_funds":         "macro",
    }

    new_cols = {}
    for col, dimension in dimension_map.items():
        if col not in df.columns:
            continue
        span = EWM_SPANS[dimension]
        new_cols[f"{col}_ewm"] = df[col].ewm(span=span, adjust=False).mean()

    return pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1)


# ---------------------------------------------------------------------------
# Data prep helpers — normalize inputs to consistent formats
# ---------------------------------------------------------------------------

def _prep_prices(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """Normalize a price DataFrame to [date (index), close]."""
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    if "date" in df.columns:
        df = df.set_index("date")
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    if "close" not in df.columns and "adj_close" in df.columns:
        df["close"] = df["adj_close"]

    if "close" not in df.columns:
        raise ValueError(f"{label}: expected 'close' column, got {list(df.columns)}")

    return df[["close"]]


def _prep_universe(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize the universe price DataFrame to [date, ticker, close]."""
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    required = {"date", "ticker", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Universe prices missing columns: {missing}")

    if "adj_close" in df.columns and "close" not in df.columns:
        df["close"] = df["adj_close"]

    df["date"] = pd.to_datetime(df["date"])
    return df[["date", "ticker", "close"]]


def _prep_fred(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize FRED wide DataFrame: ensure DatetimeIndex, forward-fill gaps."""
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    # FRED series have weekend/holiday gaps — forward-fill up to 5 days
    df = df.ffill(limit=5)
    return df
