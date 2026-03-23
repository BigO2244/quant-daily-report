"""
sleeves/sleeve_2/selection.py
------------------------------
Value sleeve — EDGAR XBRL-based fundamental screen.

Strategy:
  - Screen for low trailing P/E (below 75th percentile of universe P/E)
  - Require positive TTM EPS (no money-losers)
  - Require price > 200d SMA (avoid value traps)
  - Weight by inverse P/E (lower P/E → more capital)

Output format matches sleeve_trend / sleeve_1 for PortfolioAllocator compatibility.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from core.portfolio_alloc import SleeveOutput, create_sleeve_output
from sleeves.sleeve_2.valuation import compute_pe_ratios

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TOP_N_DEFAULT = 10
PE_MAX_PERCENTILE = 0.75   # exclude top-25% most expensive P/Es
PE_MIN_RATIO = 1.0         # exclude negative/zero P/E (losses)
PE_CAP_RATIO = 80.0        # exclude extreme P/Es (data artifacts)

# MA filter: avoid value traps
REQUIRE_ABOVE_200D = True

GATE_MIN_PRICE = 5.0
GATE_MIN_AVG_VOLUME = 100_000


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def select_and_weight(
    prices: pd.DataFrame,
    as_of_date: Optional[str] = None,
    top_n: int = TOP_N_DEFAULT,
    force_edgar_refresh: bool = False,
) -> pd.DataFrame:
    """
    Select low-P/E value stocks using EDGAR-sourced fundamentals.

    Parameters
    ----------
    prices : DataFrame
        Long-form OHLCV with date, ticker, open, high, low, close, volume.
    as_of_date : str or None
        Backtest date. Defaults to most recent price date.
    top_n : int
        Maximum positions.
    force_edgar_refresh : bool
        Bypass EDGAR disk cache.

    Returns
    -------
    DataFrame with columns:
        ticker, target_weight, sleeve, score, rank, pe_ratio, ttm_eps, reason
    """
    if prices is None or prices.empty:
        return pd.DataFrame()

    if as_of_date is None:
        as_of_date = str(prices["date"].max())[:10]

    tickers = prices["ticker"].unique().tolist()

    # ── Fetch P/E ratios from EDGAR ──────────────────────────────────────
    try:
        pe_df = compute_pe_ratios(
            tickers=tickers,
            prices=prices,
            as_of_date=as_of_date,
            force_refresh=force_edgar_refresh,
        )
    except Exception as exc:
        logger.warning("[S2_SELECT] EDGAR P/E fetch failed: %s", exc)
        return pd.DataFrame()

    if pe_df.empty:
        logger.warning("[S2_SELECT] No P/E data available for %s", as_of_date)
        return pd.DataFrame()

    # ── 200-day SMA filter (avoid value traps) ──────────────────────────
    if REQUIRE_ABOVE_200D and not prices.empty:
        as_of_ts = pd.to_datetime(as_of_date)
        sma_200 = (
            prices.sort_values(["ticker", "date"])
            .groupby("ticker")["close"]
            .apply(lambda x: x.rolling(200, min_periods=100).mean().iloc[-1]
                   if len(x) >= 100 else None)
            .reset_index()
        )
        sma_200.columns = ["ticker", "sma_200"]
        last_close = (
            prices[prices["date"] <= as_of_ts]
            .sort_values("date")
            .groupby("ticker")["close"]
            .last()
            .reset_index()
        )
        last_close.columns = ["ticker", "last_close"]

        sma_df = sma_200.merge(last_close, on="ticker", how="inner")
        sma_df["above_200d"] = sma_df["last_close"] > sma_df["sma_200"]
        pe_df = pe_df.merge(
            sma_df[["ticker", "above_200d"]], on="ticker", how="left"
        )
        pe_df = pe_df[pe_df["above_200d"].fillna(False)]

    # ── Fundamental gates ────────────────────────────────────────────────
    eligible = pe_df.dropna(subset=["pe_ratio"]).copy()
    eligible = eligible[
        (eligible["pe_ratio"] >= PE_MIN_RATIO)
        & (eligible["pe_ratio"] <= PE_CAP_RATIO)
        & (eligible["ttm_eps"].fillna(0) > 0)
    ]

    # Price gate
    eligible = eligible[eligible["close"].fillna(0) >= GATE_MIN_PRICE]

    # P/E percentile screen: only buy the cheapest 75%
    if len(eligible) > 2:
        pe_cutoff = eligible["pe_ratio"].quantile(PE_MAX_PERCENTILE)
        eligible = eligible[eligible["pe_ratio"] <= pe_cutoff]

    if eligible.empty:
        logger.warning("[S2_SELECT] No stocks pass value gates on %s", as_of_date)
        return pd.DataFrame()

    # ── Score: inverse P/E (lower P/E → higher score) ──────────────────
    eligible["score"] = (1.0 / eligible["pe_ratio"].clip(lower=1.0)) * 100
    # Cross-sectional rank for output consistency
    eligible["score"] = eligible["score"].rank(pct=True) * 100

    # ── Select top N ─────────────────────────────────────────────────────
    selected = eligible.nlargest(top_n, "score").copy().reset_index(drop=True)
    selected["rank"] = range(1, len(selected) + 1)

    # ── Inverse-P/E weighting ─────────────────────────────────────────────
    inv_pe = 1.0 / selected["pe_ratio"].clip(lower=1.0)
    raw_weights = inv_pe / inv_pe.sum()
    raw_weights = raw_weights.clip(upper=0.40)
    raw_weights = raw_weights / raw_weights.sum()   # renormalize after cap

    selected["target_weight"] = raw_weights.values
    selected["sleeve"] = "sleeve_2"
    selected["reason"] = "value_selection_edgar"

    out_cols = ["ticker", "target_weight", "sleeve", "score", "rank",
                "pe_ratio", "ttm_eps", "reason"]
    return selected[out_cols].reset_index(drop=True)


# ---------------------------------------------------------------------------
# SleeveOutput builder
# ---------------------------------------------------------------------------

def build_value_sleeve_output(
    prices: pd.DataFrame,
    top_n: int = TOP_N_DEFAULT,
    base_strength: float = 1.0,
    as_of_date: Optional[str] = None,
) -> SleeveOutput:
    """
    Build a SleeveOutput for Sleeve 2 (value) using EDGAR XBRL P/E.

    Parameters
    ----------
    prices : Raw OHLCV DataFrame from download_prices().
    top_n : Maximum positions.
    base_strength : Base sleeve strength for dynamic allocation.
    as_of_date : Snapshot date (defaults to latest in prices).

    Returns
    -------
    SleeveOutput compatible with PortfolioAllocator.
    """
    if prices is None or prices.empty:
        return create_sleeve_output([], "sleeve_2", 0.0, "No price data")

    try:
        targets = select_and_weight(prices, as_of_date=as_of_date, top_n=top_n)
    except Exception as exc:
        logger.warning("[S2] Selection failed: %s", exc)
        return create_sleeve_output([], "sleeve_2", 0.0, f"Selection error: {exc}")

    if targets.empty:
        return create_sleeve_output([], "sleeve_2", 0.0, "No stocks pass value gates")

    positions = [
        {
            "ticker": str(row["ticker"]).upper(),
            "target_weight": float(row["target_weight"]),
            "reason": "value_selection_edgar",
            "signal_strength": float(row.get("score", 50.0)) / 100.0,
        }
        for _, row in targets.iterrows()
    ]

    n_pos = len(positions)
    top_ticker = positions[0]["ticker"] if positions else "—"
    avg_pe = float(targets["pe_ratio"].mean()) if "pe_ratio" in targets.columns else 0.0
    notes = (
        f"Active: {n_pos} positions (top: {top_ticker}), "
        f"EDGAR trailing P/E, avg_pe={avg_pe:.1f}, inverse-PE weighted"
    )
    return create_sleeve_output(positions, "sleeve_2", base_strength, notes)
