"""
Overnight Agent — Commodity Shock Transmission
=================================================
Thesis: Commodity shocks propagate through economic sectors before equity
prices fully adjust. Oil spikes hit airlines before airline stocks move;
grain price surges hit food producers before earnings revisions appear.

Data sources:
    WTI crude:   FRED series DCOILWTICO (daily, free)
    Nat gas:     FRED series DHHNGSP
    Gold:        yfinance GLD
    Baltic Dry:  yfinance BDRY (BDI ETF proxy)
    Agri index:  yfinance DBA

Sector impact matrix:
    Oil spike  → Energy+ Financials+  Airlines- Retail- Transport-
    Gas spike  → Utilities-           Industrials-
    BDI spike  → Materials+           Retail+  (trade expansion signal)
    Gold spike → Defensive/Risk-off signal

Score: per-sector directional bias [-1, +1]
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any, Dict

from overnight_agents.base import BaseAgent

logger = logging.getLogger(__name__)

# FRED series for commodity prices
_FRED_COMMODITIES = {
    "wti_crude": "DCOILWTICO",
    "nat_gas":   "DHHNGSP",
}

# yfinance tickers for commodity proxies
_YF_COMMODITIES = {
    "gold":      "GLD",
    "baltic_dry": "BDRY",
    "agri":      "DBA",
}

# Sector impact weights: (commodity, direction) → {sector: impact}
# Positive impact = bullish for sector; negative = bearish
_SECTOR_IMPACTS = {
    ("wti_crude", "spike"): {
        "XLE": +1.0, "XLF": +0.3,
        "XLI": -0.4, "XLY": -0.5, "XLP": -0.3,
    },
    ("wti_crude", "crash"): {
        "XLE": -1.0, "XLF": -0.2,
        "XLI": +0.3, "XLY": +0.4, "XLP": +0.2,
    },
    ("nat_gas", "spike"): {
        "XLU": -0.6, "XLI": -0.3, "XLE": +0.4,
    },
    ("gold", "spike"): {
        "GLD": +1.0, "XLF": -0.3, "XLK": -0.2,
    },
    ("baltic_dry", "spike"): {
        "XLB": +0.5, "XLI": +0.4, "XLY": +0.2,
    },
}

_SPIKE_THRESH = 0.04    # 4% 5-day move = signal
_CRASH_THRESH = -0.04


class CommodityAgent(BaseAgent):

    @property
    def name(self) -> str:
        return "commodity"

    def _fetch(self, as_of_date: date) -> Dict[str, Any]:
        changes: Dict[str, float] = {}

        # FRED commodities
        fred_key = os.environ.get("FRED_API_KEY")
        if fred_key:
            changes.update(_fetch_fred_commodities(fred_key, as_of_date))

        # yfinance commodities
        changes.update(_fetch_yf_commodities(as_of_date))

        if not changes:
            raise RuntimeError("No commodity data available")

        # Classify each commodity's move direction
        commodity_signals: Dict[str, str] = {}
        for name, chg in changes.items():
            if chg >= _SPIKE_THRESH:
                commodity_signals[name] = "spike"
            elif chg <= _CRASH_THRESH:
                commodity_signals[name] = "crash"
            else:
                commodity_signals[name] = "neutral"

        # Aggregate sector impacts
        sector_bias: Dict[str, float] = {}
        for (commodity, direction), impacts in _SECTOR_IMPACTS.items():
            signal = commodity_signals.get(commodity)
            if signal != direction:
                continue
            mag = abs(changes.get(commodity, 0))
            for sector, base_impact in impacts.items():
                sector_bias[sector] = sector_bias.get(sector, 0) + base_impact * min(mag / _SPIKE_THRESH, 2.0)

        # Clamp sector biases
        sector_bias = {k: round(self._clamp(v), 4) for k, v in sector_bias.items()}

        # Overall commodity regime
        oil_sig = commodity_signals.get("wti_crude", "neutral")
        gold_sig = commodity_signals.get("gold", "neutral")
        if oil_sig == "spike" or gold_sig == "spike":
            regime = "inflationary_shock"
            score = 0.0   # not a simple bull/bear — sector-specific
        elif oil_sig == "crash":
            regime = "deflationary_pressure"
            score = -0.3
        else:
            regime = "neutral"
            score = 0.0

        return {
            "regime": regime,
            "score": round(score, 4),
            "commodity_changes_5d": {k: round(v, 4) for k, v in changes.items()},
            "commodity_signals": commodity_signals,
            "sector_bias": sector_bias,
            "top_bullish_sectors": [k for k, v in sorted(sector_bias.items(), key=lambda x: x[1], reverse=True) if v > 0.2],
            "top_bearish_sectors": [k for k, v in sorted(sector_bias.items(), key=lambda x: x[1]) if v < -0.2],
            "interpretation": f"Oil={oil_sig}, Gold={gold_sig}",
            "as_of_date": as_of_date.isoformat(),
        }


def _fetch_fred_commodities(fred_key: str, as_of: date) -> Dict[str, float]:
    from fredapi import Fred
    fred = Fred(api_key=fred_key)
    start = as_of - timedelta(days=10)
    result = {}
    for name, series_id in _FRED_COMMODITIES.items():
        try:
            s = fred.get_series(series_id, observation_start=start.isoformat(),
                                observation_end=as_of.isoformat()).dropna()
            if len(s) >= 2:
                result[name] = float(s.iloc[-1] / s.iloc[0] - 1)
        except Exception as exc:
            logger.debug("[COMMODITY] FRED %s: %s", series_id, exc)
    return result


def _fetch_yf_commodities(as_of: date) -> Dict[str, float]:
    import yfinance as yf
    result = {}
    for name, ticker in _YF_COMMODITIES.items():
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if len(hist) >= 2:
                result[name] = float(hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1)
        except Exception as exc:
            logger.debug("[COMMODITY] yf %s: %s", ticker, exc)
    return result
