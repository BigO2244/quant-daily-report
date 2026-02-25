# sleeves/sleeve_trend/build_sleeve_output.py
"""
Integration bridge: replaces the naive extract_sleeve_output() path
for sleeve_trend with real cross-sectional selection.

Usage in daily_quant_report.py:
    # BEFORE (naive — equal weight from trade history):
    trend_output = extract_sleeve_output(st_equity, st_trades, "sleeve_trend", 1.0)

    # AFTER (real selection — scored, ranked, inverse-vol weighted):
    from sleeves.sleeve_trend.build_sleeve_output import build_trend_sleeve_output
    trend_output = build_trend_sleeve_output(st_signals, st_equity)
"""

from __future__ import annotations

import logging

import pandas as pd

from core.portfolio_alloc import SleeveOutput, create_sleeve_output, WEIGHT_TOLERANCE
from sleeves.sleeve_trend.selection import select_and_weight, TOP_N

logger = logging.getLogger(__name__)

# Increase to 10 for paper trading (backtest uses 3 for its swing strategy,
# but the portfolio allocation sleeve should hold a broader basket)
PAPER_TOP_N = 10


def build_trend_sleeve_output(
    signals: pd.DataFrame,
    equity_df: pd.DataFrame | None = None,
    top_n: int = PAPER_TOP_N,
    base_strength: float = 1.0,
) -> SleeveOutput:
    """
    Build a SleeveOutput for sleeve_trend using real cross-sectional selection.

    Parameters
    ----------
    signals : DataFrame
        Output of sleeves.sleeve_trend.backtest.prepare_data().
        Contains indicators for every ticker on every date.
    equity_df : DataFrame, optional
        Equity curve from the sleeve_trend backtest (used for strength calc).
    top_n : int
        How many positions to hold.
    base_strength : float
        Base strength for dynamic allocation.

    Returns
    -------
    SleeveOutput ready for the PortfolioAllocator.
    """
    if signals is None or signals.empty:
        logger.warning("[BUILD_SLEEVE] No signals data — returning inactive output")
        return create_sleeve_output([], "sleeve_trend", 0.0, "No signals data")

    # Run selection
    targets = select_and_weight(signals, top_n=top_n)

    if targets.empty:
        logger.warning("[BUILD_SLEEVE] Selection returned no positions")
        return create_sleeve_output([], "sleeve_trend", 0.0, "No stocks pass gates")

    # Build positions list for SleeveOutput
    positions = []
    for _, row in targets.iterrows():
        positions.append({
            "ticker": str(row["ticker"]).upper(),
            "target_weight": float(row["target_weight"]),
            "reason": str(row.get("reason", "trend_selection")),
            "signal_strength": float(row.get("score", 50.0)) / 100.0,
        })

    # Compute sleeve strength from backtest performance
    strength = base_strength
    if equity_df is not None and not equity_df.empty and "equity" in equity_df.columns:
        start_eq = float(equity_df["equity"].iloc[0])
        end_eq = float(equity_df["equity"].iloc[-1])
        if start_eq > 0:
            sleeve_return = (end_eq / start_eq) - 1.0
            # Scale strength: better performance → higher allocation priority
            strength = min(1.0, base_strength * max(0.5, min(1.5, 1.0 + sleeve_return)))

    n_pos = len(positions)
    top_ticker = positions[0]["ticker"] if positions else "—"
    notes = f"Active: {n_pos} positions (top: {top_ticker}), scored & inverse-vol weighted"

    return create_sleeve_output(positions, "sleeve_trend", strength, notes)
