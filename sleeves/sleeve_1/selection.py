"""
sleeves/sleeve_1/selection.py
------------------------------
Cross-sectional momentum selection for Sleeve 1.

Signals:
  - 12-1 month price momentum (skip last month to avoid short-term reversal)
  - Dual MA filter (50d / 200d SMA)
  - ATR-normalized momentum (risk-adjusted signal strength)
  - Sector-relative momentum (alpha vs sector peers)
  - Breadth risk filter: if < 40% of universe in uptrend, scale back exposure

Position sizing: ATR-based inverse-vol weighting (lower vol → more weight).

Output format mirrors sleeve_trend so portfolio_alloc.py works unchanged.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np
import pandas as pd

from core.portfolio_alloc import SleeveOutput, create_sleeve_output
from sleeves.sleeve_1.indicators import (
    atr_normalized_momentum,
    dual_ma_filter,
    momentum_12_1,
    realized_volatility,
    sector_relative_momentum,
    uptrend_ratio,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_RISK_PCT_PER_TRADE = float(os.environ.get("MAX_RISK_PCT_PER_TRADE", "0.01"))

# Risk filter: below this uptrend fraction, scale position sizes back
UPTREND_RISK_FILTER_MIN = 0.40

# Default number of positions
TOP_N_DEFAULT = 10

# Minimum history required before a stock can be scored (trading days)
MIN_HISTORY_DAYS = 260   # needs ~252 days for mom_12_1

# Liquidity gates (mirrors sleeve_trend)
GATE_MIN_PRICE = 5.0
GATE_MIN_AVG_VOLUME_DAYS = 20
GATE_MIN_AVG_VOLUME = 100_000

# Composite score weights (must sum to 1.0)
W_MOM_12_1    = 0.45   # 12-1 month momentum cross-sectional rank
W_SECTOR_REL  = 0.25   # Sector-relative momentum rank
W_ATR_NORM    = 0.25   # ATR-normalized momentum rank (risk-adjusted)
W_MA_ALIGN    = 0.05   # MA alignment is a soft bonus, not a hard gate

# Inverse-vol position sizing caps
IVOL_FLOOR = 0.05   # 5% annualized vol floor
IVOL_CAP   = 1.50   # 150% annualized vol cap
MAX_SINGLE_WEIGHT = 0.40   # hard cap per position
MIN_SINGLE_WEIGHT = 0.02   # drop positions below this after normalization


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------

def build_momentum_signals(prices: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all Sleeve 1 momentum signals from raw OHLCV prices.

    Parameters
    ----------
    prices : DataFrame
        Long-form with columns: date, ticker, open, high, low, close, volume.
        Optional: sector column (added from universe if missing).

    Returns
    -------
    DataFrame with all indicator columns appended.
    """
    df = prices.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["ticker", "date"])

    df = momentum_12_1(df)
    df = dual_ma_filter(df)
    df = atr_normalized_momentum(df)
    df = sector_relative_momentum(df)
    df = realized_volatility(df)

    # Rolling 20-day average volume for liquidity gate
    df["avg_volume_20d"] = df.groupby("ticker")["volume"].transform(
        lambda x: x.rolling(GATE_MIN_AVG_VOLUME_DAYS, min_periods=5).mean()
    )

    return df


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def select_and_weight(
    signals: pd.DataFrame,
    asof_date: Optional[str] = None,
    top_n: int = TOP_N_DEFAULT,
) -> pd.DataFrame:
    """
    Select top momentum names and compute ATR-based inverse-vol target weights.

    Parameters
    ----------
    signals : DataFrame
        Output of build_momentum_signals().
    asof_date : str or None
        Snapshot date. Defaults to the most recent date in signals.
    top_n : int
        Maximum number of positions to hold.

    Returns
    -------
    DataFrame with columns:
        ticker, target_weight, sleeve, score, rank, sector (if available),
        realized_vol, reason
    """
    if signals is None or signals.empty:
        return pd.DataFrame()

    if asof_date is None:
        asof_date = signals["date"].max()
    asof_date = pd.to_datetime(asof_date)

    snap = signals[signals["date"] == asof_date].copy()
    if snap.empty:
        logger.warning("[S1_SELECT] No signals for date %s", asof_date)
        return pd.DataFrame()

    # ── Breadth risk filter ─────────────────────────────────────────────────
    uptrend_frac = uptrend_ratio(
        signals[["date", "ticker", "above_200d"]].drop_duplicates()
        if "above_200d" in signals.columns
        else pd.DataFrame(),
        asof_date,
    )
    position_scale = 1.0
    if uptrend_frac < UPTREND_RISK_FILTER_MIN:
        position_scale = max(0.25, uptrend_frac / UPTREND_RISK_FILTER_MIN)
        logger.info(
            "[S1_SELECT] Breadth risk filter: %.0f%% in uptrend → position_scale=%.0f%%",
            uptrend_frac * 100,
            position_scale * 100,
        )

    # ── Hard gates ──────────────────────────────────────────────────────────
    eligible = snap.dropna(subset=["mom_12_1"]).copy()

    # Price gate
    if "close" in eligible.columns:
        eligible = eligible[eligible["close"] >= GATE_MIN_PRICE]

    # Volume gate
    if "avg_volume_20d" in eligible.columns:
        eligible = eligible[eligible["avg_volume_20d"].fillna(0) >= GATE_MIN_AVG_VOLUME]

    if eligible.empty:
        logger.warning("[S1_SELECT] No stocks pass momentum liquidity filters on %s", asof_date)
        return pd.DataFrame()

    # ── Cross-sectional scoring ─────────────────────────────────────────────
    def pct_rank(col: str) -> pd.Series:
        if col in eligible.columns:
            return eligible[col].rank(pct=True, na_option="bottom") * 100
        return pd.Series(50.0, index=eligible.index)

    mom_rank       = pct_rank("mom_12_1")
    sector_rel_rank = pct_rank("mom_sector_rel")
    atr_norm_rank  = pct_rank("mom_atr_norm")

    if "ma_uptrend" in eligible.columns:
        ma_score = eligible["ma_uptrend"].astype(float) * 100
    else:
        ma_score = pd.Series(50.0, index=eligible.index)

    eligible["score"] = (
        W_MOM_12_1   * mom_rank
        + W_SECTOR_REL * sector_rel_rank
        + W_ATR_NORM   * atr_norm_rank
        + W_MA_ALIGN   * ma_score
    )

    # ── Select top N ───────────────────────────────────────────────────────
    selected = eligible.nlargest(top_n, "score").copy()
    selected = selected.reset_index(drop=True)
    selected["rank"] = range(1, len(selected) + 1)

    if selected.empty:
        return pd.DataFrame()

    # ── ATR-based inverse-vol weighting ─────────────────────────────────────
    if "realized_vol" in selected.columns:
        inv_vol = 1.0 / selected["realized_vol"].clip(lower=IVOL_FLOOR, upper=IVOL_CAP)
    else:
        inv_vol = pd.Series(1.0, index=selected.index)

    raw_weights = inv_vol / inv_vol.sum()

    # Apply cap and floor
    raw_weights = raw_weights.clip(upper=MAX_SINGLE_WEIGHT)
    raw_weights = raw_weights / raw_weights.sum() * position_scale

    # Drop below-floor positions and renormalize
    raw_weights = raw_weights.where(raw_weights >= MIN_SINGLE_WEIGHT, other=0.0)
    if raw_weights.sum() > 0:
        raw_weights = raw_weights / raw_weights.sum() * position_scale

    selected["target_weight"] = raw_weights.values
    selected = selected[selected["target_weight"] > 0]

    # ── Output columns ──────────────────────────────────────────────────────
    selected["sleeve"] = "sleeve_1"
    selected["reason"] = "momentum_selection"

    out_cols = ["ticker", "target_weight", "sleeve", "score", "rank", "reason"]
    for opt in ["sector", "realized_vol"]:
        if opt in selected.columns:
            out_cols.append(opt)

    logger.info(
        "[S1_SELECT] Selected %d positions on %s (uptrend_frac=%.0f%% scale=%.0f%%)",
        len(selected),
        asof_date.date(),
        uptrend_frac * 100,
        position_scale * 100,
    )
    return selected[out_cols].reset_index(drop=True)


# ---------------------------------------------------------------------------
# SleeveOutput builder
# ---------------------------------------------------------------------------

def build_momentum_sleeve_output(
    prices: pd.DataFrame,
    top_n: int = TOP_N_DEFAULT,
    base_strength: float = 1.0,
) -> SleeveOutput:
    """
    Build a SleeveOutput for Sleeve 1 using cross-sectional momentum selection.

    Parameters
    ----------
    prices : Raw OHLCV DataFrame from download_prices().
    top_n : Maximum positions.
    base_strength : Base sleeve strength for dynamic portfolio allocation.

    Returns
    -------
    SleeveOutput compatible with PortfolioAllocator.
    """
    if prices is None or prices.empty:
        return create_sleeve_output([], "sleeve_1", 0.0, "No price data")

    try:
        signals = build_momentum_signals(prices)
    except Exception as exc:
        logger.warning("[S1] Signal build failed: %s", exc)
        return create_sleeve_output([], "sleeve_1", 0.0, f"Signal build error: {exc}")

    targets = select_and_weight(signals, top_n=top_n)
    if targets.empty:
        return create_sleeve_output([], "sleeve_1", 0.0, "No stocks pass momentum gates")

    positions = [
        {
            "ticker": str(row["ticker"]).upper(),
            "target_weight": float(row["target_weight"]),
            "reason": "momentum_selection",
            "signal_strength": float(row.get("score", 50.0)) / 100.0,
        }
        for _, row in targets.iterrows()
    ]

    n_pos = len(positions)
    top_ticker = positions[0]["ticker"] if positions else "—"
    notes = (
        f"Active: {n_pos} positions (top: {top_ticker}), "
        "12-1m momentum + sector-relative, ATR inverse-vol weighted"
    )
    return create_sleeve_output(positions, "sleeve_1", base_strength, notes)
