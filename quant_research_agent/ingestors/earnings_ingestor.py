"""
Earnings ingestor.

For each watchlist ticker, fetches recent earnings data via yfinance:
  - Trailing EPS actuals vs estimates
  - Revenue actuals vs estimates
  - Forward guidance if available

Returns a ResearchItem per ticker that has meaningful earnings data.
No production imports.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import yfinance as yf

from agent.models import ResearchItem

logger = logging.getLogger(__name__)


def fetch(
    context: dict[str, Any] | None = None,
    tickers: list[str] | None = None,
) -> list[ResearchItem]:
    """
    Fetch recent earnings data for watchlist tickers.

    Args:
        context: Strategy context dict. Used to pull watchlist if tickers not provided.
        tickers: Explicit ticker list override.

    Returns:
        List of ResearchItems (source='earnings'), one per ticker with data.
    """
    watch = tickers or _get_watchlist(context)
    if not watch:
        logger.warning("Earnings ingestor: no tickers configured.")
        return []

    items: list[ResearchItem] = []
    for ticker in watch:
        item = _fetch_ticker(ticker)
        if item:
            items.append(item)

    logger.info("Earnings: fetched data for %d/%d tickers", len(items), len(watch))
    return items


def _get_watchlist(context: dict[str, Any] | None) -> list[str]:
    if not context:
        return []
    return context.get("portfolio", {}).get("watchlist", [])


def _fetch_ticker(ticker: str) -> ResearchItem | None:
    try:
        info = yf.Ticker(ticker).info
    except Exception as exc:
        logger.warning("yfinance failed for %s: %s", ticker, exc)
        return None

    # Build a summary from available fields
    parts: list[str] = [f"Ticker: {ticker}"]

    eps_trailing = info.get("trailingEps")
    eps_forward = info.get("forwardEps")
    revenue = info.get("totalRevenue")
    revenue_growth = info.get("revenueGrowth")
    earnings_growth = info.get("earningsGrowth")
    pe_trailing = info.get("trailingPE")
    pe_forward = info.get("forwardPE")
    rec = info.get("recommendationKey", "").replace("_", " ")
    analyst_count = info.get("numberOfAnalystOpinions")
    target_mean = info.get("targetMeanPrice")
    current_price = info.get("currentPrice") or info.get("regularMarketPrice")
    short_name = info.get("shortName", ticker)

    if eps_trailing is not None:
        parts.append(f"Trailing EPS: ${eps_trailing:.2f}")
    if eps_forward is not None:
        parts.append(f"Forward EPS estimate: ${eps_forward:.2f}")
    if revenue:
        parts.append(f"Revenue (TTM): ${revenue:,.0f}")
    if revenue_growth is not None:
        parts.append(f"Revenue growth YoY: {revenue_growth:.1%}")
    if earnings_growth is not None:
        parts.append(f"Earnings growth YoY: {earnings_growth:.1%}")
    if pe_trailing is not None:
        parts.append(f"Trailing P/E: {pe_trailing:.1f}x")
    if pe_forward is not None:
        parts.append(f"Forward P/E: {pe_forward:.1f}x")
    if rec:
        parts.append(f"Analyst consensus: {rec.title()} ({analyst_count} analysts)")
    if target_mean and current_price:
        upside = (target_mean / current_price - 1) * 100
        parts.append(f"Price target: ${target_mean:.2f} ({upside:+.1f}% upside)")

    if len(parts) <= 1:
        # No meaningful data retrieved
        return None

    summary = "\n".join(parts)
    title = f"{short_name} ({ticker}) — Earnings & Fundamental Snapshot"

    return ResearchItem(
        source="earnings",
        item_id=f"earnings:{ticker}:{datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')}",
        title=title,
        summary=summary,
        url=f"https://finance.yahoo.com/quote/{ticker}",
        published=datetime.now(tz=timezone.utc),
        metadata={"ticker": ticker, "short_name": short_name},
    )
