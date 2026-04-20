"""
Alpha Stack — Thematic Overlay
================================
Merges two signal sources into per-ticker score boosts for sleeve scoring:

Source 1 — Claude research digest (quant_research_agent/outputs/digest_*.json)
    Score per ticker = max Claude score across all items mentioning that ticker.
    Bearish items receive a 50% haircut (flag, not boost).

Source 2 — Overnight agent signals (outputs/overnight_signals/YYYY-MM-DD.json)
    Earnings revision scores → direct per-ticker boost
    Insider cluster buys → +0.70 boost per ticker
    Commodity sector bias → mapped to sector ETF tickers
    Liquidity / gamma regime → applied as a uniform market-level modifier

Combined boost per ticker = max(digest_score, overnight_score), clipped [0, 1].

The combined boost is applied in the trend sleeve's score_universe() as:
    adj_score += thematic_boost_weight × boost
before percentile ranking, so Claude-flagged + overnight-confirmed names
rise naturally in the distribution.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_DIGEST_SEARCH_PATHS = [
    Path(__file__).parents[2] / "quant_research_agent" / "outputs",
    Path(__file__).parents[2] / "outputs" / "research_digest",
]
_OVERNIGHT_DIR = Path(__file__).parents[2] / "outputs" / "overnight_signals"
_MAX_AGE_DAYS = 3


def load_thematic_scores(
    output_dir: Optional[str | Path] = None,
    as_of_date: Optional[date] = None,
    max_age_days: int = _MAX_AGE_DAYS,
) -> pd.Series:
    """
    Load merged per-ticker thematic boost scores from all signal sources.

    Returns
    -------
    pd.Series  {ticker: float}  boosts in [0.0, 1.0].
    """
    as_of = as_of_date or date.today()

    digest_scores = _load_digest_scores(output_dir, as_of, max_age_days)
    overnight_scores = _load_overnight_scores(as_of, max_age_days)

    if digest_scores.empty and overnight_scores.empty:
        return pd.Series(dtype=float)

    # Merge: take max across both sources per ticker
    combined = pd.concat([digest_scores, overnight_scores], axis=0)
    merged = combined.groupby(level=0).max().clip(0.0, 1.0)

    logger.info(
        "[THEMATIC] Combined boosts: %d tickers (digest=%d overnight=%d) top=%s",
        len(merged),
        len(digest_scores),
        len(overnight_scores),
        merged.nlargest(5).round(3).to_dict(),
    )
    return merged


# ------------------------------------------------------------------ #
# Source 1: Research digest                                            #
# ------------------------------------------------------------------ #

def _load_digest_scores(
    output_dir: Optional[str | Path],
    as_of: date,
    max_age_days: int,
) -> pd.Series:
    search_dirs = [Path(output_dir)] if output_dir else _DIGEST_SEARCH_PATHS
    digest_path = _find_latest_file(search_dirs, "digest_*.json", as_of, max_age_days)
    if digest_path is None:
        logger.debug("[THEMATIC] No recent digest found.")
        return pd.Series(dtype=float)

    try:
        data = json.loads(digest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("[THEMATIC] Failed to read digest %s: %s", digest_path, exc)
        return pd.Series(dtype=float)

    # Accumulate bullish and bearish scores separately so that net-bearish
    # tickers receive no positive boost (a bearish-only ticker must not rank
    # higher than a neutral ticker).
    bullish_scores: Dict[str, float] = {}
    bearish_scores: Dict[str, float] = {}
    for item in data.get("items", []):
        item_score = float(item.get("score", 0.0))
        tickers = item.get("tickers_mentioned", [])
        if not tickers or item_score <= 0:
            continue
        sentiment = item.get("sentiment", "neutral")
        if sentiment == "bearish":
            for ticker in tickers:
                t = ticker.strip().upper()
                if t:
                    bearish_scores[t] = max(bearish_scores.get(t, 0.0), item_score * 0.50)
        else:
            for ticker in tickers:
                t = ticker.strip().upper()
                if t:
                    bullish_scores[t] = max(bullish_scores.get(t, 0.0), item_score)

    # Net score: only positive net values contribute a boost.
    ticker_scores: Dict[str, float] = {}
    for t in set(bullish_scores) | set(bearish_scores):
        net = bullish_scores.get(t, 0.0) - bearish_scores.get(t, 0.0)
        if net > 0:
            ticker_scores[t] = net

    if not ticker_scores:
        return pd.Series(dtype=float)

    return pd.Series(ticker_scores, dtype=float).clip(0.0, 1.0)


# ------------------------------------------------------------------ #
# Source 2: Overnight agent signals                                    #
# ------------------------------------------------------------------ #

def _load_overnight_scores(as_of: date, max_age_days: int) -> pd.Series:
    """Extract per-ticker boosts from the overnight signals JSON."""
    signals_path = _find_latest_file([_OVERNIGHT_DIR], "*.json", as_of, max_age_days)
    if signals_path is None:
        logger.debug("[THEMATIC] No recent overnight signals found.")
        return pd.Series(dtype=float)

    try:
        payload = json.loads(signals_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("[THEMATIC] Failed to read overnight signals %s: %s", signals_path, exc)
        return pd.Series(dtype=float)

    ticker_scores: Dict[str, float] = {}
    signals_raw = payload.get("signals", {})
    # Normalize list-format output (new orchestrator) to dict keyed by agent name.
    if isinstance(signals_raw, list):
        signals = {item.get("agent", ""): item for item in signals_raw if isinstance(item, dict)}
    else:
        signals = signals_raw if isinstance(signals_raw, dict) else {}

    # Earnings revision: direct per-ticker scores
    earnings = signals.get("earnings_revision", {})
    for ticker, score in earnings.get("ticker_scores", {}).items():
        _update_score(ticker_scores, ticker, max(0.0, float(score)))

    # Insider activity: cluster buys → strong boost
    insider = signals.get("insider_activity", {})
    for ticker in insider.get("cluster_buys", []):
        _update_score(ticker_scores, ticker, 0.75)
    for ticker in insider.get("cluster_sells", []):
        _update_score(ticker_scores, ticker, -0.30)   # penalize (will be clipped to 0)

    # Gamma regime: negative gamma boosts momentum names already in top ranks
    # We express this as a small uniform boost to all names when in neg gamma regime
    gamma = signals.get("gamma", {})
    if gamma.get("regime") == "negative_gamma":
        for ticker in ticker_scores:
            ticker_scores[ticker] = min(1.0, ticker_scores[ticker] + 0.10)

    # Liquidity: contracting liquidity depresses all scores slightly
    liquidity = signals.get("liquidity", {})
    if liquidity.get("regime") == "contracting":
        for ticker in ticker_scores:
            ticker_scores[ticker] = max(0.0, ticker_scores[ticker] - 0.10)

    if not ticker_scores:
        return pd.Series(dtype=float)

    return pd.Series(ticker_scores, dtype=float).clip(0.0, 1.0)


def _update_score(store: Dict[str, float], ticker: str, score: float) -> None:
    t = ticker.strip().upper()
    if t:
        store[t] = max(store.get(t, 0.0), score)


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _find_latest_file(
    search_dirs: list[Path],
    pattern: str,
    as_of: date,
    max_age_days: int,
) -> Optional[Path]:
    # Add 2 calendar-day buffer to cover weekends: a 3-trading-day window spans
    # up to 5 calendar days when the boundary falls across a weekend.
    cutoff = as_of - timedelta(days=max_age_days + 2)
    candidates: list[tuple[date, Path]] = []

    for d in search_dirs:
        if not d.exists():
            continue
        for f in d.glob(pattern):
            stem = f.stem.removeprefix("digest_")
            try:
                file_date = date.fromisoformat(stem)
            except ValueError:
                # Try the raw stem (overnight signals use YYYY-MM-DD.json directly)
                try:
                    file_date = date.fromisoformat(f.stem)
                except ValueError:
                    continue
            if file_date >= cutoff:
                candidates.append((file_date, f))
            else:
                logger.debug("[THEMATIC] Rejecting stale file %s (date=%s < cutoff=%s)", f.name, file_date, cutoff)

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]
