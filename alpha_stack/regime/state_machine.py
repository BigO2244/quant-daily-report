"""
Alpha Stack — Regime State Machine
=====================================
Defines all regime state enumerations and raw (pre-hysteresis) classification
functions. Hysteresis is applied on top of these raw signals.

State dimensions:
    TrendState     — based on SPY T1=(Close/EMA200)-1 and T2=(EMA50/EMA200)-1
    VolatilityState — based on VIX level
    BreadthState   — based on % of universe above 200-DMA
    MacroState     — based on macro proxy signals (TLT, HYG); defaults to neutral

All threshold values are sourced from alpha_stack.yaml (with hard-coded
defaults for resilience if config is unavailable).
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# ================================================================== #
# State enumerations                                                   #
# ================================================================== #

class TrendState(str, Enum):
    STRONG_UP   = "strong_up"
    WEAK_UP     = "weak_up"
    NEUTRAL     = "neutral"
    WEAK_DOWN   = "weak_down"
    STRONG_DOWN = "strong_down"

    def numeric(self) -> int:
        """Ordinal value for transition distance checks (higher = more bullish)."""
        return {
            TrendState.STRONG_UP:   4,
            TrendState.WEAK_UP:     3,
            TrendState.NEUTRAL:     2,
            TrendState.WEAK_DOWN:   1,
            TrendState.STRONG_DOWN: 0,
        }[self]


class VolatilityState(str, Enum):
    CALM     = "calm"
    NORMAL   = "normal"
    ELEVATED = "elevated"
    CRISIS   = "crisis"

    def numeric(self) -> int:
        """Higher = more stressed."""
        return {
            VolatilityState.CALM:     0,
            VolatilityState.NORMAL:   1,
            VolatilityState.ELEVATED: 2,
            VolatilityState.CRISIS:   3,
        }[self]


class BreadthState(str, Enum):
    HEALTHY       = "healthy"
    MIXED         = "mixed"
    DETERIORATING = "deteriorating"
    WASHED_OUT    = "washed_out"

    def numeric(self) -> int:
        return {
            BreadthState.HEALTHY:       3,
            BreadthState.MIXED:         2,
            BreadthState.DETERIORATING: 1,
            BreadthState.WASHED_OUT:    0,
        }[self]


class MacroState(str, Enum):
    SUPPORTIVE  = "supportive"
    NEUTRAL     = "neutral"
    RESTRICTIVE = "restrictive"


# ================================================================== #
# Default thresholds (override via alpha_stack.yaml)                   #
# ================================================================== #

_TREND_DEFAULTS = {
    "strong_up":   {"t1_min": 0.03,  "t2_min": 0.01},
    "weak_up":     {"t1_min": 0.00,  "t2_min": 0.00},
    "neutral":     {"t1_min": -0.02, "t1_max": 0.00},
    "weak_down":   {"t1_max": -0.02, "t2_max": 0.00},
    "strong_down": {"t1_max": -0.05, "t2_max": -0.01},
}

_VOL_DEFAULTS = {
    "calm":     {"vix_max": 16.0},
    "normal":   {"vix_min": 16.0, "vix_max": 22.0},
    "elevated": {"vix_min": 22.0, "vix_max": 30.0},
    "crisis":   {"vix_min": 30.0},
}

_BREADTH_DEFAULTS = {
    "healthy":       {"pct_min": 65.0},
    "mixed":         {"pct_min": 45.0, "pct_max": 65.0},
    "deteriorating": {"pct_min": 30.0, "pct_max": 45.0},
    "washed_out":    {"pct_max": 30.0},
}


# ================================================================== #
# Raw classifiers (no hysteresis)                                      #
# ================================================================== #

def classify_trend(
    t1: Optional[float],
    t2: Optional[float],
    thresholds: Optional[dict] = None,
) -> TrendState:
    """
    Classify trend state from T1=(Close/EMA200)-1 and T2=(EMA50/EMA200)-1.

    Parameters
    ----------
    t1 : float or None
        Price relative to EMA200.
    t2 : float or None
        EMA50 relative to EMA200.
    thresholds : dict, optional
        Override default thresholds from alpha_stack.yaml.

    Returns
    -------
    TrendState
    """
    if t1 is None or t2 is None:
        logger.warning("[REGIME] Trend inputs unavailable; defaulting to NEUTRAL")
        return TrendState.NEUTRAL

    th = thresholds or _TREND_DEFAULTS

    # Priority: strong states checked first (most specific)
    if t1 >= th["strong_up"]["t1_min"] and t2 >= th["strong_up"]["t2_min"]:
        return TrendState.STRONG_UP

    if t1 <= th["strong_down"]["t1_max"] and t2 <= th["strong_down"]["t2_max"]:
        return TrendState.STRONG_DOWN

    if t1 >= th["weak_up"]["t1_min"] and t2 >= th["weak_up"]["t2_min"]:
        return TrendState.WEAK_UP

    if t1 <= th["weak_down"]["t1_max"] and t2 <= th["weak_down"]["t2_max"]:
        return TrendState.WEAK_DOWN

    return TrendState.NEUTRAL


def classify_volatility(
    vix: Optional[float],
    thresholds: Optional[dict] = None,
) -> VolatilityState:
    """
    Classify volatility state from VIX level.

    Parameters
    ----------
    vix : float or None
    thresholds : dict, optional

    Returns
    -------
    VolatilityState
    """
    if vix is None:
        logger.warning("[REGIME] VIX unavailable; defaulting to NORMAL")
        return VolatilityState.NORMAL

    th = thresholds or _VOL_DEFAULTS

    if vix >= th["crisis"]["vix_min"]:
        return VolatilityState.CRISIS
    if vix >= th["elevated"]["vix_min"]:
        return VolatilityState.ELEVATED
    if vix >= th["normal"]["vix_min"]:
        return VolatilityState.NORMAL
    return VolatilityState.CALM


def classify_breadth(
    pct_above_200dma: Optional[float],
    thresholds: Optional[dict] = None,
) -> BreadthState:
    """
    Classify breadth state from % of universe above 200-DMA.

    Parameters
    ----------
    pct_above_200dma : float or None
        Percentage (0-100) of universe members above their 200-day MA.
    thresholds : dict, optional

    Returns
    -------
    BreadthState
    """
    if pct_above_200dma is None or pct_above_200dma != pct_above_200dma:  # NaN check
        logger.warning("[REGIME] Breadth data unavailable; defaulting to MIXED")
        return BreadthState.MIXED

    th = thresholds or _BREADTH_DEFAULTS

    if pct_above_200dma >= th["healthy"]["pct_min"]:
        return BreadthState.HEALTHY
    if pct_above_200dma >= th["mixed"]["pct_min"]:
        return BreadthState.MIXED
    if pct_above_200dma >= th["deteriorating"]["pct_min"]:
        return BreadthState.DETERIORATING
    return BreadthState.WASHED_OUT


def classify_macro(
    tlt_ret_20d: Optional[float] = None,
    hyg_ret_20d: Optional[float] = None,
) -> MacroState:
    """
    Classify macro state from proxy signals.

    NOTE: This is a simplified placeholder using TLT and HYG price returns
    as proxies. A proper implementation should use yield curve slope and
    credit OAS spreads. Defaults to NEUTRAL when data is unavailable.

    Parameters
    ----------
    tlt_ret_20d : float or None
        20-day return of TLT (20-year treasury ETF).
        Positive = rates falling / easing → supportive.
    hyg_ret_20d : float or None
        20-day return of HYG (HY bond ETF).
        Positive = credit spreads tightening → supportive.

    Returns
    -------
    MacroState
    """
    if tlt_ret_20d is None and hyg_ret_20d is None:
        return MacroState.NEUTRAL

    # Simple scoring: positive returns on both = supportive, negative = restrictive
    score = 0
    if tlt_ret_20d is not None:
        score += 1 if tlt_ret_20d > 0.01 else (-1 if tlt_ret_20d < -0.01 else 0)
    if hyg_ret_20d is not None:
        score += 1 if hyg_ret_20d > 0.005 else (-1 if hyg_ret_20d < -0.005 else 0)

    if score >= 1:
        return MacroState.SUPPORTIVE
    if score <= -1:
        return MacroState.RESTRICTIVE
    return MacroState.NEUTRAL


def transition_distance(
    state_a: TrendState | VolatilityState | BreadthState,
    state_b: TrendState | VolatilityState | BreadthState,
) -> int:
    """
    Return the ordinal distance between two states of the same type.
    Used to enforce max_state_jump constraint.
    """
    return abs(state_a.numeric() - state_b.numeric())  # type: ignore[attr-defined]
