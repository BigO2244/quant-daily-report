"""
Alpha Stack — Regime Engine
=============================
Explicit state machine with hysteresis for multi-dimensional market regime
classification. Used by the allocator to adjust sleeve budgets.

Dimensions:
    Trend     — strong_up / weak_up / neutral / weak_down / strong_down
    Volatility — calm / normal / elevated / crisis
    Breadth    — healthy / mixed / deteriorating / washed_out
    Macro      — supportive / neutral / restrictive

Usage:
    from alpha_stack.regime.context import RegimeEngine
    engine = RegimeEngine()
    ctx = engine.classify(as_of_date="2024-01-15")
    print(ctx.trend_state, ctx.vol_state, ctx.breadth_state)
"""

from alpha_stack.regime.state_machine import (
    TrendState,
    VolatilityState,
    BreadthState,
    MacroState,
    classify_trend,
    classify_volatility,
    classify_breadth,
    classify_macro,
)
from alpha_stack.regime.hysteresis import HysteresisController
from alpha_stack.regime.context import RegimeContext, RegimeEngine

__all__ = [
    "TrendState",
    "VolatilityState",
    "BreadthState",
    "MacroState",
    "classify_trend",
    "classify_volatility",
    "classify_breadth",
    "classify_macro",
    "HysteresisController",
    "RegimeContext",
    "RegimeEngine",
]
