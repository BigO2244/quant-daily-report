"""
Alpha Stack — Breadth Features
================================
Derives regime-relevant breadth signals from the BreadthDataStore output.

Primarily used by the regime engine for breadth state classification.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)


def get_breadth_regime_inputs(
    breadth_store,
    as_of_date: date | str,
) -> dict:
    """
    Return breadth inputs needed by the regime engine.

    Returns
    -------
    dict with keys:
        pct_above_200dma : float — % of universe above 200-DMA
        pct_above_50dma  : float — % of universe above 50-DMA
        ad_ratio         : float — advance/decline ratio proxy
        as_of_date       : str
    """
    snap = breadth_store.get_breadth_snapshot(as_of_date)
    return {
        "pct_above_200dma": snap.get("pct_above_200dma", float("nan")),
        "pct_above_50dma": snap.get("pct_above_50dma", float("nan")),
        "ad_ratio": snap.get("advance_decline_ratio", float("nan")),
        "as_of_date": snap.get("as_of_date", str(as_of_date)),
    }
