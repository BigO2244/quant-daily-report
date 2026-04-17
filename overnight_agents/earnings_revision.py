"""
Overnight Agent — Earnings Revision Momentum
===============================================
Thesis: Analyst EPS estimate revisions are a leading signal for institutional
capital rotation. Stocks with upward revisions consistently outperform (Chan,
Jegadeesh & Lakonishok 1996; Hawkins, Chamberlin & Daniel 1984).

Data: yfinance analyst estimates + recommendations (free)
Cadence: daily (estimates update post-earnings and on analyst days)

Score construction per ticker:
    1. Recommendation trend (strongBuy, buy, hold, sell, strongSell ratios)
       Revision score = (strongBuy×2 + buy×1 - sell×1 - strongSell×2) / total
    2. EPS estimate trend: current quarter vs prior quarter consensus
       Revision = (current_mean - prior_mean) / |prior_mean|

Composite ticker score:
    0.60 × rec_score + 0.40 × eps_revision (normalized to [-1, 1])

Output:
    ticker_scores: {ticker: score}   > 0 = positive revision momentum
    top_revised_up / top_revised_down: lists for portfolio overlay
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from overnight_agents.base import BaseAgent

logger = logging.getLogger(__name__)

# Tickers to evaluate — sourced from strategy watchlist
_WATCHLIST = [
    "AVGO", "NVDA", "MSFT", "VST", "TLN", "MU", "META",
    "GOOGL", "AMZN", "LMT", "NOC", "GD", "AAPL", "JPM",
    "GS", "QCOM", "FTNT", "ABNB", "VZ", "GM",
]

_REC_WEIGHT = 0.60
_EPS_WEIGHT = 0.40
_UP_THRESH = 0.30
_DOWN_THRESH = -0.30


class EarningsRevisionAgent(BaseAgent):

    @property
    def name(self) -> str:
        return "earnings_revision"

    def _fetch(self, as_of_date: date) -> Dict[str, Any]:
        import yfinance as yf

        ticker_scores: Dict[str, float] = {}

        for symbol in _WATCHLIST:
            score = _score_ticker(symbol)
            if score is not None:
                ticker_scores[symbol] = round(score, 4)

        if not ticker_scores:
            raise RuntimeError("No earnings revision data available")

        top_up = [t for t, s in sorted(ticker_scores.items(), key=lambda x: x[1], reverse=True)
                  if s >= _UP_THRESH]
        top_down = [t for t, s in sorted(ticker_scores.items(), key=lambda x: x[1])
                    if s <= _DOWN_THRESH]

        avg_score = sum(ticker_scores.values()) / len(ticker_scores)

        return {
            "regime": "positive_revision" if avg_score > 0.1 else ("negative_revision" if avg_score < -0.1 else "neutral_revision"),
            "score": round(self._clamp(avg_score), 4),
            "ticker_scores": ticker_scores,
            "top_revised_up": top_up[:8],
            "top_revised_down": top_down[:5],
            "interpretation": f"EPS revision leaders: {top_up[:3]}",
            "as_of_date": as_of_date.isoformat(),
        }


def _score_ticker(symbol: str) -> Optional[float]:
    """Score a single ticker on recommendation + EPS revision signals."""
    import yfinance as yf

    try:
        t = yf.Ticker(symbol)

        rec_score = _recommendation_score(t)
        eps_score = _eps_revision_score(t)

        if rec_score is None and eps_score is None:
            return None

        rec = rec_score or 0.0
        eps = eps_score or 0.0
        weight_r = _REC_WEIGHT if rec_score is not None else 0.0
        weight_e = _EPS_WEIGHT if eps_score is not None else 0.0
        total_w = weight_r + weight_e

        if total_w == 0:
            return None

        return (rec * weight_r + eps * weight_e) / total_w

    except Exception as exc:
        logger.debug("[EARNINGS_REV] %s failed: %s", symbol, exc)
        return None


def _recommendation_score(t) -> Optional[float]:
    """Compute a [-1,1] score from analyst recommendation distribution."""
    try:
        recs = t.recommendations_summary
        if recs is None or recs.empty:
            return None

        latest = recs.iloc[0]
        strong_buy = int(latest.get("strongBuy", 0))
        buy = int(latest.get("buy", 0))
        hold = int(latest.get("hold", 0))
        sell = int(latest.get("sell", 0))
        strong_sell = int(latest.get("strongSell", 0))

        total = strong_buy + buy + hold + sell + strong_sell
        if total == 0:
            return None

        raw = (strong_buy * 2 + buy * 1 - sell * 1 - strong_sell * 2) / total
        return max(-1.0, min(1.0, raw))

    except Exception:
        return None


def _eps_revision_score(t) -> Optional[float]:
    """Compare current vs prior quarter EPS consensus estimate."""
    try:
        est = t.earnings_estimate
        if est is None or est.empty:
            return None

        # yfinance returns estimates for 0q (current quarter), +1q, 0y, +1y
        rows = est.reset_index()
        if len(rows) < 2:
            return None

        current_mean = rows.iloc[0].get("avg", None)
        prior_mean = rows.iloc[1].get("avg", None)

        if current_mean is None or prior_mean is None:
            return None
        if abs(float(prior_mean)) < 0.01:
            return None

        revision = (float(current_mean) - float(prior_mean)) / abs(float(prior_mean))
        return max(-1.0, min(1.0, revision * 3.0))   # scale: 33% revision = max signal

    except Exception:
        return None
