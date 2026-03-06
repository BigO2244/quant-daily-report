"""
Macro ingestor.

Fetches key macroeconomic series from FRED (Federal Reserve Economic Data):
  - Fed Funds Rate
  - CPI YoY
  - 10Y Treasury yield
  - Unemployment rate
  - 2Y-10Y yield spread (recession indicator)

Returns one ResearchItem summarising the latest macro readings.
No production imports.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from fredapi import Fred

from agent.models import ResearchItem

logger = logging.getLogger(__name__)

# FRED series IDs → human-readable labels
_SERIES = {
    "FEDFUNDS": "Fed Funds Rate (%)",
    "CPIAUCSL": "CPI (All Urban Consumers, SA)",
    "GS10": "10-Year Treasury Yield (%)",
    "GS2": "2-Year Treasury Yield (%)",
    "UNRATE": "Unemployment Rate (%)",
    "T10Y2Y": "10Y-2Y Treasury Spread (bps)",
    "VIXCLS": "VIX (CBOE Volatility Index)",
}


def fetch(
    context: dict[str, Any] | None = None,
    api_key: str | None = None,
) -> list[ResearchItem]:
    """
    Fetch latest macro readings from FRED and return as a single ResearchItem.

    Args:
        context: Strategy context (not used currently, reserved for future filtering).
        api_key: FRED API key. Falls back to FRED_API_KEY env var.

    Returns:
        List with one ResearchItem (source='macro'), or empty list on failure.
    """
    key = api_key or os.environ.get("FRED_API_KEY")
    if not key:
        logger.warning("FRED_API_KEY not set — skipping macro ingestor.")
        return []

    try:
        fred = Fred(api_key=key)
    except Exception as exc:
        logger.error("Failed to initialise FRED client: %s", exc)
        return []

    readings: list[str] = []
    latest_date: datetime | None = None

    for series_id, label in _SERIES.items():
        try:
            series = fred.get_series(series_id, observation_start="2024-01-01")
            if series is None or series.empty:
                continue
            value = series.dropna().iloc[-1]
            obs_date = series.dropna().index[-1]

            readings.append(f"{label}: {value:.2f} (as of {obs_date.strftime('%Y-%m-%d')})")

            obs_dt = obs_date.to_pydatetime().replace(tzinfo=timezone.utc)
            if latest_date is None or obs_dt > latest_date:
                latest_date = obs_dt

        except Exception as exc:
            logger.warning("FRED series %s failed: %s", series_id, exc)

    if not readings:
        logger.warning("Macro ingestor: no data retrieved from FRED.")
        return []

    # Compute spread annotation if both yields available
    spread_line = _compute_spread_annotation(readings)
    if spread_line:
        readings.append(spread_line)

    summary = "Latest macroeconomic readings from FRED:\n\n" + "\n".join(readings)
    published = latest_date or datetime.now(tz=timezone.utc)

    item = ResearchItem(
        source="macro",
        item_id=f"macro:fred:{published.strftime('%Y-%m-%d')}",
        title=f"Macro Dashboard — {published.strftime('%Y-%m-%d')}",
        summary=summary,
        url="https://fred.stlouisfed.org/",
        published=published,
        metadata={"series": list(_SERIES.keys())},
    )

    logger.info("Macro: fetched %d FRED series", len(readings))
    return [item]


def _compute_spread_annotation(readings: list[str]) -> str | None:
    """Attempt to extract and annotate the 2Y-10Y spread direction."""
    spread_line = next((r for r in readings if "10Y-2Y" in r), None)
    if not spread_line:
        return None
    try:
        value_str = spread_line.split(":")[1].strip().split(" ")[0]
        value = float(value_str)
        if value < 0:
            return f"NOTE: Yield curve is inverted ({value:.2f} bps) — historically a recession signal."
        return f"NOTE: Yield curve is positive ({value:.2f} bps) — no inversion."
    except (IndexError, ValueError):
        return None
