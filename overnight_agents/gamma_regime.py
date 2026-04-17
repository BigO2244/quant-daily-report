"""
Overnight Agent — Dealer Gamma Regime
========================================
Thesis: Dealer hedging behavior determines market volatility and trend structure.

  Positive gamma → dealers buy dips / sell rallies → mean reversion, low vol
  Negative gamma → dealers amplify moves (buy on up, sell on down) → momentum, high vol

This directly maps to our sleeve switching:
  positive_gamma  → activate mean reversion sleeve, reduce momentum weight
  negative_gamma  → activate momentum sleeve, suppress mean reversion

Data sources (yfinance, free):
    SPY options chain (front two expiries)
    Derived: put/call OI ratio, IV skew, GEX proxy

GEX proxy (simplified):
    For each strike:  gamma × OI × 100 × spot²  × 0.01
    Net GEX = sum(call GEX) - sum(put GEX)
    Positive net GEX = dealers long gamma = positive gamma regime

IV Skew:
    25-delta put IV - 25-delta call IV
    High skew = fear premium = market expects downside, dealers short gamma

Score: normalized composite of put/call ratio + IV skew + GEX sign
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List

import numpy as np

from overnight_agents.base import BaseAgent

logger = logging.getLogger(__name__)

_POS_GAMMA_THRESH = 0.25
_NEG_GAMMA_THRESH = -0.25


class GammaRegimeAgent(BaseAgent):

    @property
    def name(self) -> str:
        return "gamma"

    def _fetch(self, as_of_date: date) -> Dict[str, Any]:
        import yfinance as yf

        ticker = yf.Ticker("SPY")
        spot = _get_spot(ticker)
        if spot is None or spot <= 0:
            raise RuntimeError("Could not fetch SPY spot price")

        # Fetch front two option expiries
        expirations = ticker.options
        if not expirations or len(expirations) < 1:
            raise RuntimeError("No SPY options data available")

        put_oi_total = 0
        call_oi_total = 0
        net_gex = 0.0
        iv_skew_samples: List[float] = []

        for expiry in expirations[:2]:
            try:
                chain = ticker.option_chain(expiry)
                calls = chain.calls.dropna(subset=["openInterest", "impliedVolatility"])
                puts = chain.puts.dropna(subset=["openInterest", "impliedVolatility"])

                call_oi_total += int(calls["openInterest"].sum())
                put_oi_total += int(puts["openInterest"].sum())

                # Compute gamma via Black-Scholes (yfinance doesn't supply greeks)
                import datetime as _dt
                exp_date = _dt.date.fromisoformat(expiry)
                dte = max((exp_date - date.today()).days, 1) / 365.0
                r = 0.05   # risk-free rate proxy

                call_gex = _compute_gex(calls, spot, dte, r, is_put=False)
                put_gex = _compute_gex(puts, spot, dte, r, is_put=True)
                net_gex += call_gex - put_gex

                # IV skew: OTM puts (5-10% below spot) vs OTM calls (5-10% above)
                atm_low = spot * 0.93
                atm_high = spot * 1.07
                otm_puts = puts[(puts["strike"] >= atm_low) & (puts["strike"] <= spot * 0.99)]
                otm_calls = calls[(calls["strike"] >= spot * 1.01) & (calls["strike"] <= atm_high)]

                if not otm_puts.empty and not otm_calls.empty:
                    put_iv = float(otm_puts["impliedVolatility"].median())
                    call_iv = float(otm_calls["impliedVolatility"].median())
                    iv_skew_samples.append(put_iv - call_iv)

            except Exception as exc:
                logger.debug("[GAMMA] Expiry %s failed: %s", expiry, exc)
                continue

        if call_oi_total == 0:
            raise RuntimeError("No options data parsed")

        # Put/Call OI ratio (>1 = more puts = fear)
        pc_ratio = put_oi_total / max(call_oi_total, 1)
        # Normalize: ratio of 1.0 is neutral, 1.5 is high fear, 0.5 is complacency
        pc_signal = self._clamp((1.0 - pc_ratio) * 2.0)   # higher ratio → negative score

        # GEX signal: positive GEX = positive gamma regime
        gex_signal = self._clamp(np.sign(net_gex) * min(abs(net_gex) / 1e9, 1.0))

        # IV skew signal: high skew = negative gamma environment
        iv_skew = float(np.mean(iv_skew_samples)) if iv_skew_samples else 0.0
        skew_signal = self._clamp(-iv_skew * 5.0)   # high skew → negative score

        # Composite
        score = self._clamp(0.35 * gex_signal + 0.40 * pc_signal + 0.25 * skew_signal)

        if score >= _POS_GAMMA_THRESH:
            regime = "positive_gamma"
            interpretation = "Positive gamma — mean reversion favored, low vol expected"
        elif score <= _NEG_GAMMA_THRESH:
            regime = "negative_gamma"
            interpretation = "Negative gamma — momentum favored, elevated vol expected"
        else:
            regime = "neutral_gamma"
            interpretation = "Gamma neutral — no structural vol bias"

        return {
            "regime": regime,
            "score": round(score, 4),
            "put_call_ratio": round(pc_ratio, 3),
            "iv_skew": round(iv_skew, 4),
            "net_gex_bn": round(net_gex / 1e9, 3),
            "spot": round(spot, 2),
            "interpretation": interpretation,
            "as_of_date": as_of_date.isoformat(),
        }


def _bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes gamma (identical for calls and puts)."""
    import math
    if sigma <= 0 or T <= 0 or S <= 0:
        return 0.0
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return math.exp(-0.5 * d1 ** 2) / (S * sigma * math.sqrt(2 * math.pi * T))
    except Exception:
        return 0.0


def _compute_gex(chain_df, spot: float, dte: float, r: float, is_put: bool) -> float:
    """Compute total GEX contribution for one side of the options chain."""
    total = 0.0
    for _, row in chain_df.iterrows():
        K = float(row.get("strike", 0))
        sigma = float(row.get("impliedVolatility", 0))
        oi = float(row.get("openInterest", 0))
        if K <= 0 or sigma <= 0 or oi <= 0:
            continue
        gamma = _bs_gamma(spot, K, dte, r, sigma)
        # GEX = gamma × OI × contract_size × spot² × 0.01
        gex = gamma * oi * 100 * spot * spot * 0.01
        total += gex
    return total


def _get_spot(ticker) -> float | None:
    try:
        info = ticker.fast_info
        return float(info.last_price or info.previous_close)
    except Exception:
        try:
            hist = ticker.history(period="1d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            return None
