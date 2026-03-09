"""
Alpha Stack — Regime Context & Engine
========================================
RegimeContext: the output object consumed by sleeves and the allocator.
RegimeEngine: orchestrates the full multi-dimensional regime classification.

Usage (single date):
    engine = RegimeEngine()
    ctx = engine.classify(as_of_date="2024-06-01")
    print(ctx)

Usage (backtest loop):
    engine = RegimeEngine()
    for date in trading_dates:
        ctx = engine.step(date)
        allocator.update(ctx)
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from alpha_stack.regime.state_machine import (
    TrendState, VolatilityState, BreadthState, MacroState,
    classify_trend, classify_volatility, classify_breadth, classify_macro,
)
from alpha_stack.regime.hysteresis import HysteresisController

logger = logging.getLogger(__name__)


# ================================================================== #
# RegimeContext — the regime output object                             #
# ================================================================== #

@dataclass
class RegimeContext:
    """
    Multi-dimensional regime context object.
    Consumed by sleeves and the allocator.
    """
    trend_state:   TrendState
    vol_state:     VolatilityState
    breadth_state: BreadthState
    macro_state:   MacroState
    as_of_date:    str
    bars_in_trend_state:   int = 0
    bars_in_vol_state:     int = 0
    bars_in_breadth_state: int = 0
    state_confidence:      float = 1.0   # [0,1]; future: use for blended transitions

    # Raw inputs (for diagnostics)
    raw_vix:             Optional[float] = None
    raw_spy_t1:          Optional[float] = None
    raw_spy_t2:          Optional[float] = None
    raw_pct_above_200:   Optional[float] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["trend_state"]   = str(self.trend_state.value)
        d["vol_state"]     = str(self.vol_state.value)
        d["breadth_state"] = str(self.breadth_state.value)
        d["macro_state"]   = str(self.macro_state.value)
        return d

    def __str__(self) -> str:
        return (
            f"RegimeContext({self.as_of_date}) "
            f"trend={self.trend_state.value} "
            f"vol={self.vol_state.value} "
            f"breadth={self.breadth_state.value} "
            f"macro={self.macro_state.value}"
        )

    @property
    def is_risk_off(self) -> bool:
        """True if a defensive regime is active."""
        return (
            self.vol_state in (VolatilityState.CRISIS,)
            or self.trend_state in (TrendState.STRONG_DOWN,)
            or self.breadth_state in (BreadthState.WASHED_OUT,)
        )

    @property
    def allows_mean_reversion(self) -> bool:
        """True if the mean-reversion regime gate is satisfied."""
        return (
            self.trend_state in (TrendState.WEAK_UP, TrendState.NEUTRAL)
            and self.vol_state in (VolatilityState.CALM, VolatilityState.NORMAL)
        )


# ================================================================== #
# RegimeEngine                                                         #
# ================================================================== #

class RegimeEngine:
    """
    Orchestrates multi-dimensional regime classification with hysteresis.

    Parameters
    ----------
    macro_store : MacroDataStore, optional
        If None, creates one internally.
    breadth_store : BreadthDataStore, optional
        If None, creates one internally.
    config_overrides : dict, optional
        Override hysteresis/threshold settings from alpha_stack.yaml.
    """

    def __init__(
        self,
        macro_store=None,
        breadth_store=None,
        config_overrides: Optional[dict] = None,
    ) -> None:
        from alpha_stack._config_loader import get_section
        cfg = config_overrides or get_section("regime") or {}

        hyst = cfg.get("hysteresis", {})
        min_dwell = int(hyst.get("min_dwell_days", 5))
        confirm = int(hyst.get("confirmation_closes", 2))
        max_jump = int(hyst.get("max_state_jump", 1))
        crisis_bypass = bool(hyst.get("crisis_bypass_dwell", True))

        self._crisis_bypass = crisis_bypass

        # Hysteresis controllers per dimension
        self._h_trend = HysteresisController(
            TrendState.NEUTRAL, min_dwell, confirm, max_jump, "trend"
        )
        self._h_vol = HysteresisController(
            VolatilityState.NORMAL, min_dwell, confirm, max_jump, "volatility"
        )
        self._h_breadth = HysteresisController(
            BreadthState.MIXED, min_dwell, confirm, max_jump, "breadth"
        )
        self._h_macro = HysteresisController(
            MacroState.NEUTRAL, min_dwell, confirm, max_jump, "macro"
        )

        # Data stores
        self._macro = macro_store or self._build_macro_store()
        self._breadth = breadth_store or self._build_breadth_store()

        # History for attribution / diagnostics
        self._history: List[RegimeContext] = []

    # ------------------------------------------------------------------ #
    # Single-date classification                                           #
    # ------------------------------------------------------------------ #

    def classify(self, as_of_date: date | str) -> RegimeContext:
        """
        Classify regime for a single date.
        Updates hysteresis controllers and returns RegimeContext.

        Parameters
        ----------
        as_of_date : date or str

        Returns
        -------
        RegimeContext
        """
        if isinstance(as_of_date, str):
            as_of_date = pd.Timestamp(as_of_date)
        date_str = str(as_of_date)[:10]

        # Fetch raw inputs
        vix = self._macro.get_macro_series("vix", date_str)
        t1 = self._macro.get_macro_series("spy_trend_t1", date_str)
        t2 = self._macro.get_macro_series("spy_trend_t2", date_str)
        pct_200 = self._breadth.get_breadth_snapshot(date_str).get("pct_above_200dma")

        # Macro proxy (TLT/HYG 20-day returns — approximate)
        tlt_ret = self._get_20d_return("tlt_close", date_str)
        hyg_ret = self._get_20d_return("hyg_close", date_str)

        # Raw classifications (no hysteresis)
        raw_trend = classify_trend(t1, t2)
        raw_vol = classify_volatility(vix)
        raw_breadth = classify_breadth(pct_200)
        raw_macro = classify_macro(tlt_ret, hyg_ret)

        # Determine if crisis bypass applies
        is_crisis = raw_vol == VolatilityState.CRISIS

        # Apply hysteresis
        self._h_trend.update(raw_trend, date_str, is_crisis=False)
        self._h_vol.update(raw_vol, date_str, is_crisis=(is_crisis and self._crisis_bypass))
        self._h_breadth.update(raw_breadth, date_str, is_crisis=False)
        self._h_macro.update(raw_macro, date_str, is_crisis=False)

        ctx = RegimeContext(
            trend_state=self._h_trend.confirmed_state,
            vol_state=self._h_vol.confirmed_state,
            breadth_state=self._h_breadth.confirmed_state,
            macro_state=self._h_macro.confirmed_state,
            as_of_date=date_str,
            bars_in_trend_state=self._h_trend.bars_in_state,
            bars_in_vol_state=self._h_vol.bars_in_state,
            bars_in_breadth_state=self._h_breadth.bars_in_state,
            raw_vix=vix,
            raw_spy_t1=t1,
            raw_spy_t2=t2,
            raw_pct_above_200=pct_200,
        )

        self._history.append(ctx)
        logger.info("[REGIME] %s", ctx)
        return ctx

    # ------------------------------------------------------------------ #
    # Backtest helpers                                                     #
    # ------------------------------------------------------------------ #

    def classify_history(
        self,
        trading_dates: List[date | str],
    ) -> pd.DataFrame:
        """
        Run the regime engine over a list of dates and return a history DataFrame.

        Parameters
        ----------
        trading_dates : list of date or str

        Returns
        -------
        DataFrame with columns:
            date, trend_state, vol_state, breadth_state, macro_state,
            bars_in_trend_state, bars_in_vol_state, raw_vix, raw_spy_t1, raw_spy_t2
        """
        # Reset hysteresis controllers for clean backtest
        self._h_trend.reset(TrendState.NEUTRAL)
        self._h_vol.reset(VolatilityState.NORMAL)
        self._h_breadth.reset(BreadthState.MIXED)
        self._h_macro.reset(MacroState.NEUTRAL)
        self._history = []

        records = []
        for d in trading_dates:
            ctx = self.classify(d)
            records.append(ctx.to_dict())

        return pd.DataFrame(records)

    def transition_summary(self) -> Dict[str, List[dict]]:
        """Return transition history for all dimensions."""
        return {
            "trend":   self._h_trend.transition_history(),
            "vol":     self._h_vol.transition_history(),
            "breadth": self._h_breadth.transition_history(),
            "macro":   self._h_macro.transition_history(),
        }

    def persist(self, output_path: str | Path) -> None:
        """Persist the latest regime context to a JSON file."""
        if not self._history:
            return
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        latest = self._history[-1].to_dict()
        with open(path, "w") as fh:
            json.dump(latest, fh, indent=2)
        logger.info("[REGIME] Persisted to %s", path)

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _get_20d_return(self, series_name: str, as_of_date: str) -> Optional[float]:
        """Approximate 20-day return for macro proxy calculation."""
        try:
            from datetime import timedelta
            end_date = pd.Timestamp(as_of_date).date()
            start_date = end_date - pd.Timedelta(days=30)
            hist = self._macro.get_macro_history(series_name, str(start_date), as_of_date)
            if hist is None or len(hist) < 5:
                return None
            vals = hist[series_name].dropna().values
            if len(vals) < 5:
                return None
            return float(vals[-1] / vals[0] - 1)
        except Exception:
            return None

    @staticmethod
    def _build_macro_store():
        from alpha_stack.datastore.macro import MacroDataStore
        return MacroDataStore()

    @staticmethod
    def _build_breadth_store():
        from alpha_stack.datastore.breadth import BreadthDataStore
        return BreadthDataStore()
