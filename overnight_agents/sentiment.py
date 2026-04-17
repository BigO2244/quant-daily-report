"""
Overnight Agent — Sentiment Divergence Signal
===============================================
Thesis: Retail sentiment extremes often mark turning points.
Retail euphoria + institutional selling = contrarian short signal.
Retail panic + institutional buying = contrarian long signal.

Data sources:
    AAII Investor Sentiment Survey (weekly, free CSV)
    Existing research agent news sentiment scores (daily digest JSON)
    CNN Fear & Greed proxy via VIX + momentum (derived)

Score construction:
    aaii_spread = bullish% - bearish%
    Contrarian score: -1 × normalize(aaii_spread)
    (high retail bullishness = negative contrarian score = caution)

Extreme thresholds:
    aaii_spread > 30%  → extreme bullish (contrarian bearish signal)
    aaii_spread < -20% → extreme bearish (contrarian bullish signal)

Regime:
    extreme_bullish → reduce risk exposure (crowded trade warning)
    extreme_bearish → increase risk exposure (capitulation signal)
    neutral         → no regime override
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from overnight_agents.base import BaseAgent

logger = logging.getLogger(__name__)

# AAII releases weekly (Thursday); we use the most recent available
_AAII_URL = "https://www.aaii.com/files/surveys/sentiment.xls"
_EXTREME_BULL = 30.0    # aaii bull-bear spread %
_EXTREME_BEAR = -20.0

_DIGEST_SEARCH_PATHS = [
    Path(__file__).parents[1] / "quant_research_agent" / "outputs",
    Path(__file__).parents[1] / "outputs" / "research_digest",
]


class SentimentAgent(BaseAgent):

    @property
    def name(self) -> str:
        return "sentiment"

    def _fetch(self, as_of_date: date) -> Dict[str, Any]:
        aaii = _fetch_aaii_sentiment()
        news = _load_news_sentiment(as_of_date)

        components: Dict[str, Optional[float]] = {
            "aaii_spread": aaii,
            "news_sentiment": news,
        }

        scores = []

        if aaii is not None:
            # Contrarian: normalize spread to [-1, +1] and invert
            # spread of +30 = max retail bullishness = -1 contrarian score
            normalized = self._clamp(aaii / 40.0)
            contrarian_aaii = -normalized
            scores.append(("aaii", contrarian_aaii, 0.60))

        if news is not None:
            # News sentiment already in [-1, +1]; invert for contrarian
            scores.append(("news", -news * 0.5, 0.40))   # partial contrarian weight

        if not scores:
            raise RuntimeError("No sentiment data available")

        total_w = sum(w for _, _, w in scores)
        composite = sum(s * w for _, s, w in scores) / total_w
        composite = self._clamp(composite)

        spread = aaii or 0.0
        if spread >= _EXTREME_BULL:
            regime = "extreme_bullish_retail"
            interpretation = f"Retail euphoria (AAII spread {spread:.1f}%) — contrarian caution"
        elif spread <= _EXTREME_BEAR:
            regime = "extreme_bearish_retail"
            interpretation = f"Retail panic (AAII spread {spread:.1f}%) — contrarian opportunity"
        else:
            regime = "neutral_sentiment"
            interpretation = f"Sentiment neutral (AAII spread {spread:.1f}%)"

        return {
            "regime": regime,
            "score": round(composite, 4),
            "aaii_bull_bear_spread": round(spread, 2) if aaii is not None else None,
            "news_sentiment_score": round(news, 4) if news is not None else None,
            "contrarian_signal": regime if regime != "neutral_sentiment" else None,
            "interpretation": interpretation,
            "as_of_date": as_of_date.isoformat(),
        }


def _fetch_aaii_sentiment() -> Optional[float]:
    """Fetch AAII bull-bear spread. Returns None on failure."""
    try:
        import pandas as pd
        # AAII publishes weekly XLS — read last row for most recent reading
        df = pd.read_excel(_AAII_URL, engine="xlrd", skiprows=3)
        df = df.dropna(how="all")
        if df.empty or df.shape[1] < 3:
            return None
        # Columns: Date, Bullish, Neutral, Bearish (approximate)
        last = df.iloc[-1]
        bullish = float(last.iloc[1]) * 100 if last.iloc[1] < 2 else float(last.iloc[1])
        bearish = float(last.iloc[3]) * 100 if last.iloc[3] < 2 else float(last.iloc[3])
        spread = bullish - bearish
        logger.info("[SENTIMENT] AAII bull=%.1f%% bear=%.1f%% spread=%.1f%%",
                    bullish, bearish, spread)
        return round(spread, 2)
    except Exception as exc:
        logger.debug("[SENTIMENT] AAII fetch failed: %s", exc)
        return None


def _load_news_sentiment(as_of: date) -> Optional[float]:
    """Load average sentiment score from most recent research agent digest."""
    cutoff = as_of - timedelta(days=3)
    for search_dir in _DIGEST_SEARCH_PATHS:
        if not search_dir.exists():
            continue
        candidates = sorted(search_dir.glob("digest_*.json"), reverse=True)
        for path in candidates:
            try:
                file_date = date.fromisoformat(path.stem.removeprefix("digest_"))
            except ValueError:
                continue
            if file_date < cutoff:
                break
            try:
                data = json.loads(path.read_text())
                items = data.get("items", [])
                if not items:
                    return None
                sentiments = []
                for item in items:
                    s = item.get("sentiment", "neutral")
                    score = 1.0 if s == "bullish" else (-1.0 if s == "bearish" else 0.0)
                    sentiments.append(score * item.get("score", 0.5))
                if sentiments:
                    return round(sum(sentiments) / len(sentiments), 4)
            except Exception:
                continue
    return None
