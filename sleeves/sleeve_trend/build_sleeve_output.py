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
from typing import Optional

import pandas as pd

from core.portfolio_alloc import SleeveOutput, create_sleeve_output, WEIGHT_TOLERANCE
from core.sleeve_numeric_diagnostics import (
    REASON_SLEEVE_STRENGTH_NON_FINITE,
    diagnostic_event,
    is_finite_number,
)
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
    regime: Optional[dict] = None,
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
        How many positions to hold (overridden downward by regime if in HIGH/CRISIS).
    base_strength : float
        Base strength for dynamic allocation.
    regime : dict, optional
        Output of research.vix_regime.get_current_regime().
        Keys: regime (str), vix (float), position_scale (float), max_positions (int).
        When provided:
          - top_n is capped to regime['max_positions']
          - all target_weights are multiplied by regime['position_scale']
          - undeployed capital (1 - scale) flows to CASH via the allocator

    Returns
    -------
    SleeveOutput ready for the PortfolioAllocator.
    """
    if signals is None or signals.empty:
        logger.warning("[BUILD_SLEEVE] No signals data — returning inactive output")
        return create_sleeve_output([], "sleeve_trend", 0.0, "No signals data")

    # ── Apply regime constraints ────────────────────────────────────────────
    position_scale = 1.0
    regime_label   = "NONE"

    if regime is not None:
        position_scale = float(regime.get("position_scale", 1.0))
        regime_label   = str(regime.get("regime", "UNKNOWN"))
        regime_max_n   = int(regime.get("max_positions", top_n))
        if regime_max_n < top_n:
            logger.info(
                "[BUILD_SLEEVE] Regime=%s: capping top_n %d → %d",
                regime_label, top_n, regime_max_n,
            )
            top_n = regime_max_n

    # ── Run selection ───────────────────────────────────────────────────────
    targets = select_and_weight(signals, top_n=top_n)

    if targets.empty:
        logger.warning("[BUILD_SLEEVE] Selection returned no positions")
        return create_sleeve_output([], "sleeve_trend", 0.0, "No stocks pass gates")

    # ── Build positions list for SleeveOutput ───────────────────────────────
    # Note: position_scale (gross exposure cap) is applied at the PortfolioAllocator
    # level via min_gross_exposure, not here. create_sleeve_output normalizes weights
    # to sum to 1.0, so scaling here would be undone. The allocator's boost-to-min-
    # gross-exposure step correctly caps total deployment to position_scale.
    positions = []
    for _, row in targets.iterrows():
        positions.append({
            "ticker":         str(row["ticker"]).upper(),
            "target_weight":  float(row["target_weight"]),
            "reason":         str(row.get("reason", "trend_selection")),
            "signal_strength": float(row.get("score", 50.0)) / 100.0,
        })

    # ── Compute sleeve strength from backtest performance ───────────────────
    strength = base_strength
    if equity_df is not None and not equity_df.empty and "equity" in equity_df.columns:
        start_eq = float(equity_df["equity"].iloc[0])
        end_eq   = float(equity_df["equity"].iloc[-1])
        if start_eq > 0:
            sleeve_return = (end_eq / start_eq) - 1.0
            candidate_strength = min(1.0, base_strength * max(0.5, min(1.5, 1.0 + sleeve_return)))
            if is_finite_number(candidate_strength):
                strength = candidate_strength
            else:
                diagnostics = list(equity_df.attrs.get("numeric_diagnostics") or [])
                diagnostics.append(
                    diagnostic_event(
                        sleeve_id="sleeve_trend",
                        calculation_stage="build_sleeve_output",
                        reason_code=REASON_SLEEVE_STRENGTH_NON_FINITE,
                        field="strength",
                        value=candidate_strength,
                        source_artifact="equity_df",
                        downstream_effect="sleeve output strength left at base strength; validation still checks terminal equity",
                    )
                )
                equity_df.attrs["numeric_diagnostics"] = diagnostics

    n_pos      = len(positions)
    top_ticker = positions[0]["ticker"] if positions else "—"
    regime_tag = f"  [VIX regime: {regime_label} scale={position_scale:.0%}]" if regime else ""
    notes = (
        f"Active: {n_pos} positions (top: {top_ticker}), "
        f"scored & inverse-vol weighted{regime_tag}"
    )

    return create_sleeve_output(positions, "sleeve_trend", strength, notes)
