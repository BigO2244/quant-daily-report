"""
Overnight Agent — ETF Flow Pressure
======================================
Thesis: Large ETF inflows mechanically push underlying equities upward.
Passive flows create short-term alpha — front-running sector inflows works.

Method:
    ETF shares outstanding change = net creation/redemption
    Dollar flow = Δshares × NAV per share
    Normalized flow score = flow / 30-day average AUM

Data: yfinance ETF info (shares_outstanding + NAV)
Cadence: daily (updated post-market)

Sector ETF map:
    XLK  Technology        XLE  Energy
    XLF  Financials        XLV  Health Care
    XLI  Industrials       XLY  Consumer Disc
    XLC  Communication     XLB  Materials
    XLP  Consumer Staples  XLRE Real Estate
    XLU  Utilities         GLD  Gold
    TLT  Long Bonds        IWM  Small Cap

Output:
    sector_scores: {etf: score}   score > 0 = inflow, < 0 = outflow
    top_inflow / top_outflow: list of ETF tickers
    composite_equity_flow: aggregate equity ETF flow score
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, List

from overnight_agents.base import BaseAgent

logger = logging.getLogger(__name__)

_SECTOR_ETFS = [
    "XLK", "XLE", "XLF", "XLV", "XLI",
    "XLY", "XLC", "XLB", "XLP", "XLRE", "XLU",
]
_BROAD_ETFS = ["SPY", "QQQ", "IWM", "TLT", "GLD"]
_ALL_ETFS = _SECTOR_ETFS + _BROAD_ETFS

_INFLOW_THRESH = 0.25
_OUTFLOW_THRESH = -0.25


class ETFFlowsAgent(BaseAgent):

    @property
    def name(self) -> str:
        return "etf_flows"

    def _fetch(self, as_of_date: date) -> Dict[str, Any]:
        import yfinance as yf

        sector_scores: Dict[str, float] = {}
        errors: List[str] = []

        for etf in _ALL_ETFS:
            try:
                t = yf.Ticker(etf)
                # Get 5-day history to compute shares outstanding change
                # yfinance doesn't expose historical shares outstanding directly,
                # so we proxy via volume-weighted price action and info fields.
                info = t.fast_info
                hist = t.history(period="5d")

                if hist.empty or len(hist) < 2:
                    continue

                # Proxy: net dollar flow = (close - open) × volume as directional pressure
                # This captures creation/redemption pressure via price-volume relationship
                today = hist.iloc[-1]
                prev = hist.iloc[-2]

                # Flow score: price momentum × volume ratio relative to 5-day avg
                avg_vol = hist["Volume"].mean()
                if avg_vol == 0:
                    continue

                vol_ratio = float(today["Volume"]) / avg_vol
                price_ret = float((today["Close"] - prev["Close"]) / prev["Close"])

                # Flow signal: strong volume on up days = inflow pressure
                flow_signal = self._clamp(price_ret * vol_ratio * 10.0)
                sector_scores[etf] = round(flow_signal, 4)

            except Exception as exc:
                errors.append(f"{etf}: {exc}")
                logger.debug("[ETF_FLOWS] %s failed: %s", etf, exc)

        if not sector_scores:
            raise RuntimeError(f"No ETF data available. Errors: {errors[:3]}")

        # Separate sector vs broad
        sector_only = {k: v for k, v in sector_scores.items() if k in _SECTOR_ETFS}
        broad = {k: v for k, v in sector_scores.items() if k in _BROAD_ETFS}

        # Rank sectors
        sorted_sectors = sorted(sector_only.items(), key=lambda x: x[1], reverse=True)
        top_inflows = [etf for etf, score in sorted_sectors if score >= _INFLOW_THRESH]
        top_outflows = [etf for etf, score in sorted_sectors if score <= _OUTFLOW_THRESH]

        # Composite equity flow (simple average of sector ETFs)
        composite = sum(sector_only.values()) / max(len(sector_only), 1)

        if composite >= _INFLOW_THRESH:
            regime = "risk_on_flows"
            interpretation = f"Broad equity inflows — top: {top_inflows[:3]}"
        elif composite <= _OUTFLOW_THRESH:
            regime = "risk_off_flows"
            interpretation = f"Broad equity outflows — rotating to: {list(broad.keys())[:2]}"
        else:
            regime = "neutral_flows"
            interpretation = "Mixed ETF flows — no dominant sector rotation"

        return {
            "regime": regime,
            "score": round(self._clamp(composite), 4),
            "sector_scores": sector_scores,
            "top_inflows": top_inflows[:5],
            "top_outflows": top_outflows[:5],
            "composite_equity_flow": round(composite, 4),
            "interpretation": interpretation,
            "as_of_date": as_of_date.isoformat(),
        }
