"""
Overnight Agent — Liquidity Regime
=====================================
Thesis: Liquidity conditions drive broad market performance more than earnings.
Liquidity expansion → growth/risk-on. Contraction → defensives/quality.

Data sources (all FRED, free):
    WALCL       — Fed balance sheet (weekly, billions USD)
    WTREGEN     — Treasury General Account balance (weekly, billions USD)
    RRPONTSYD   — Overnight reverse repo facility (daily, billions USD)
    WRESBAL     — Bank reserve balances (weekly, billions USD)

Score construction:
    Each series: z-score of 4-week change, clipped to [-2, 2]
    Composite: weighted average, normalized to [-1, +1]
    Positive = expanding (risk-on), Negative = contracting (risk-off)

Regime classification:
    score >= +0.30  → expanding (risk-on, favor momentum/growth)
    score <= -0.30  → contracting (risk-off, favor quality/defensive)
    else            → neutral

Feeds: regime engine liquidity dimension, allocator macro modifier.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any, Dict, Optional

import pandas as pd

from overnight_agents.base import BaseAgent

logger = logging.getLogger(__name__)

# FRED series → weight in composite (must sum to 1.0)
_SERIES_WEIGHTS = {
    "WALCL":     0.35,   # Fed balance sheet — primary liquidity driver
    "RRPONTSYD": 0.30,   # Reverse repo drains liquidity from system
    "WTREGEN":   0.20,   # TGA drawdown = liquidity injection
    "WRESBAL":   0.15,   # Bank reserves = credit creation capacity
}

# For TGA and RRPONTSYD: higher value = less net liquidity (inverted)
_INVERTED = {"WTREGEN", "RRPONTSYD"}

_LOOKBACK_DAYS = 35   # ~5 trading weeks for 4-week change
_EXPAND_THRESH = 0.30
_CONTRACT_THRESH = -0.30


class LiquidityAgent(BaseAgent):

    @property
    def name(self) -> str:
        return "liquidity"

    def _fetch(self, as_of_date: date) -> Dict[str, Any]:
        fred_key = os.environ.get("FRED_API_KEY")
        if not fred_key:
            raise EnvironmentError("FRED_API_KEY not set")

        from fredapi import Fred
        fred = Fred(api_key=fred_key)

        start = as_of_date - timedelta(days=_LOOKBACK_DAYS)
        raw_signals: Dict[str, Optional[float]] = {}

        for series_id in _SERIES_WEIGHTS:
            try:
                s = fred.get_series(
                    series_id,
                    observation_start=start.isoformat(),
                    observation_end=as_of_date.isoformat(),
                )
                if s is None or s.empty:
                    raw_signals[series_id] = None
                    continue

                s = s.dropna()
                if len(s) < 2:
                    raw_signals[series_id] = None
                    continue

                # 4-week change as fraction of the series mean (scale-independent)
                current = float(s.iloc[-1])
                prior = float(s.iloc[0])
                series_mean = float(s.mean())
                if series_mean == 0:
                    raw_signals[series_id] = None
                    continue

                chg = (current - prior) / abs(series_mean)
                raw_signals[series_id] = chg

            except Exception as exc:
                logger.warning("[LIQUIDITY] FRED series %s failed: %s", series_id, exc)
                raw_signals[series_id] = None

        # Build composite score
        total_weight = 0.0
        weighted_sum = 0.0
        series_readings = {}

        for series_id, weight in _SERIES_WEIGHTS.items():
            chg = raw_signals.get(series_id)
            if chg is None:
                continue
            # Invert series where higher = less liquidity
            if series_id in _INVERTED:
                chg = -chg
            clipped = self._clamp(chg * 5.0, -1.0, 1.0)   # scale factor empirical
            series_readings[series_id] = round(clipped, 4)
            weighted_sum += clipped * weight
            total_weight += weight

        if total_weight < 0.20:
            raise RuntimeError("Insufficient FRED series available for liquidity score")

        score = self._clamp(weighted_sum / total_weight)

        if score >= _EXPAND_THRESH:
            regime = "expanding"
            interpretation = "Liquidity expanding — favor momentum and growth"
        elif score <= _CONTRACT_THRESH:
            regime = "contracting"
            interpretation = "Liquidity contracting — favor quality and defensives"
        else:
            regime = "neutral"
            interpretation = "Liquidity conditions neutral"

        return {
            "regime": regime,
            "score": round(score, 4),
            "series": series_readings,
            "interpretation": interpretation,
            "as_of_date": as_of_date.isoformat(),
        }
