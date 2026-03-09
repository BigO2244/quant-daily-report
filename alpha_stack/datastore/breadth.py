"""
Alpha Stack — Breadth DataStore
=================================
Market breadth metrics computed from universe price history.

Breadth metrics provided:
    pct_above_200dma  — % of universe members with Close > EMA(200)
    pct_above_50dma   — % of universe members with Close > EMA(50)
    advance_count     — # with Close > prev Close
    decline_count     — # with Close < prev Close
    advance_decline_ratio — advance_count / (decline_count + 1)

Data source: PricesDataStore for the configured universe.

PIT safety: PIT-safe — uses only price history on or before as_of_date.

NOTE: A proper A/D line (NYSE advance-decline) requires tick or intraday
data. The close-price proxy used here is a reasonable approximation for
daily regime signals but should not be treated as equivalent.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from alpha_stack.datastore.base import DataStoreBase
from alpha_stack.datastore.prices import PricesDataStore, _to_date

logger = logging.getLogger(__name__)

# Lookback needed for EMA(200) to stabilise
_EMA200_WARMUP = 252
_EMA50_WARMUP = 63


class BreadthDataStore(DataStoreBase):
    """
    Market breadth store computed from universe price history.

    Parameters
    ----------
    universe_tickers : list of str
        Universe to compute breadth over. Defaults to SPY constituents proxy
        (the universe.csv list).
    prices_store : PricesDataStore, optional
        Shared prices store.
    """

    def __init__(
        self,
        universe_tickers: Optional[List[str]] = None,
        prices_store: Optional[PricesDataStore] = None,
    ) -> None:
        self._tickers = universe_tickers or self._load_universe_tickers()
        self._prices = prices_store or PricesDataStore()
        self._cache: Dict[str, dict] = {}

    # ------------------------------------------------------------------ #
    # DataStoreBase implementation                                         #
    # ------------------------------------------------------------------ #

    def get_price_history(self, symbol, start_date, end_date) -> pd.DataFrame:
        return self._prices.get_price_history(symbol, start_date, end_date)

    def get_fundamental(self, symbol, field, as_of_date) -> Optional[float]:
        return None

    def get_macro_series(self, series_name, as_of_date) -> Optional[float]:
        return None

    def get_breadth_snapshot(self, as_of_date: date | str) -> dict:
        """
        Return breadth metrics for the universe as of as_of_date.

        Returns
        -------
        dict with keys:
            pct_above_200dma : float
            pct_above_50dma  : float
            advance_count    : int
            decline_count    : int
            advance_decline_ratio : float
            universe_size    : int
            as_of_date       : str
        """
        end = _to_date(as_of_date)
        cache_key = str(end)

        if cache_key in self._cache:
            return self._cache[cache_key]

        result = self._compute_breadth(end)
        self._cache[cache_key] = result
        return result

    def get_breadth_history(
        self,
        start_date: date | str,
        end_date: date | str,
    ) -> pd.DataFrame:
        """
        Return daily breadth metrics for a date range.

        Returns
        -------
        DataFrame with columns:
            date, pct_above_200dma, pct_above_50dma,
            advance_count, decline_count, advance_decline_ratio
        """
        start = _to_date(start_date)
        end = _to_date(end_date)

        # Fetch all universe prices at once
        lookback_start = start - timedelta(days=_EMA200_WARMUP + 10)
        all_prices = self._prices.get_prices_multi(
            self._tickers, lookback_start, end
        )

        if all_prices.empty:
            logger.warning("[BREADTH] No price data available for breadth history.")
            return pd.DataFrame(columns=[
                "date", "pct_above_200dma", "pct_above_50dma",
                "advance_count", "decline_count", "advance_decline_ratio"
            ])

        # Compute breadth for each date in range
        all_prices["date"] = pd.to_datetime(all_prices["date"])
        all_prices = all_prices.sort_values(["ticker", "date"])

        # Compute EMAs per ticker
        all_prices["ema200"] = all_prices.groupby("ticker")["close"].transform(
            lambda s: s.ewm(span=200, adjust=False).mean()
        )
        all_prices["ema50"] = all_prices.groupby("ticker")["close"].transform(
            lambda s: s.ewm(span=50, adjust=False).mean()
        )
        all_prices["above_200"] = all_prices["close"] > all_prices["ema200"]
        all_prices["above_50"] = all_prices["close"] > all_prices["ema50"]
        all_prices["prev_close"] = all_prices.groupby("ticker")["close"].shift(1)
        all_prices["advancing"] = all_prices["close"] > all_prices["prev_close"]
        all_prices["declining"] = all_prices["close"] < all_prices["prev_close"]

        # Aggregate per date
        date_mask = (all_prices["date"] >= pd.Timestamp(start)) & \
                    (all_prices["date"] <= pd.Timestamp(end))
        daily = all_prices[date_mask].groupby("date").agg(
            n_total=("ticker", "count"),
            n_above_200=("above_200", "sum"),
            n_above_50=("above_50", "sum"),
            advance_count=("advancing", "sum"),
            decline_count=("declining", "sum"),
        ).reset_index()

        daily["pct_above_200dma"] = (daily["n_above_200"] / daily["n_total"] * 100).round(2)
        daily["pct_above_50dma"] = (daily["n_above_50"] / daily["n_total"] * 100).round(2)
        daily["advance_decline_ratio"] = (
            daily["advance_count"] / (daily["decline_count"] + 1)
        ).round(3)

        return daily[["date", "pct_above_200dma", "pct_above_50dma",
                      "advance_count", "decline_count", "advance_decline_ratio"]].copy()

    def metadata(self) -> dict:
        return {
            "name": "BreadthDataStore",
            "pit_safe": True,
            "source": "yfinance (derived from universe prices)",
            "notes": (
                f"Breadth computed over {len(self._tickers)} universe members. "
                "A/D ratio is a close-price proxy, not NYSE tick data. "
                "PIT-safe: uses only price history on or before as_of_date."
            ),
        }

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _compute_breadth(self, as_of_date: date) -> dict:
        """Compute breadth snapshot for a single date."""
        start = as_of_date - timedelta(days=_EMA200_WARMUP + 10)
        all_prices = self._prices.get_prices_multi(self._tickers, start, as_of_date)

        if all_prices.empty:
            return _empty_breadth(as_of_date)

        all_prices["date"] = pd.to_datetime(all_prices["date"])
        all_prices = all_prices.sort_values(["ticker", "date"])

        all_prices["ema200"] = all_prices.groupby("ticker")["close"].transform(
            lambda s: s.ewm(span=200, adjust=False).mean()
        )
        all_prices["ema50"] = all_prices.groupby("ticker")["close"].transform(
            lambda s: s.ewm(span=50, adjust=False).mean()
        )
        all_prices["prev_close"] = all_prices.groupby("ticker")["close"].shift(1)

        # Snapshot on as_of_date
        snap = all_prices[all_prices["date"] == pd.Timestamp(as_of_date)].copy()
        if snap.empty:
            # Try most recent available date
            snap = all_prices[all_prices["date"] <= pd.Timestamp(as_of_date)].copy()
            if snap.empty:
                return _empty_breadth(as_of_date)
            snap = snap.groupby("ticker").last().reset_index()

        n = len(snap)
        above_200 = int((snap["close"] > snap["ema200"]).sum())
        above_50 = int((snap["close"] > snap["ema50"]).sum())
        advancing = int((snap["close"] > snap["prev_close"]).sum())
        declining = int((snap["close"] < snap["prev_close"]).sum())

        return {
            "pct_above_200dma": round(above_200 / max(n, 1) * 100, 2),
            "pct_above_50dma": round(above_50 / max(n, 1) * 100, 2),
            "advance_count": advancing,
            "decline_count": declining,
            "advance_decline_ratio": round(advancing / max(declining + 1, 1), 3),
            "universe_size": n,
            "as_of_date": str(as_of_date),
        }

    @staticmethod
    def _load_universe_tickers() -> List[str]:
        """Load tickers from production universe.csv if available."""
        try:
            from pathlib import Path
            csv_path = Path("data/universe.csv")
            if csv_path.exists():
                df = pd.read_csv(csv_path)
                col = [c for c in df.columns if c.lower() in ("ticker", "symbol")][0]
                tickers = df[col].dropna().str.strip().tolist()
                logger.info("[BREADTH] Loaded %d tickers from %s", len(tickers), csv_path)
                return tickers
        except Exception as exc:
            logger.warning("[BREADTH] Could not load universe.csv: %s", exc)

        # Minimal fallback — just SPY proxy
        logger.warning("[BREADTH] Using SPY-only fallback universe.")
        return ["SPY"]


def _empty_breadth(as_of_date: date) -> dict:
    return {
        "pct_above_200dma": float("nan"),
        "pct_above_50dma": float("nan"),
        "advance_count": 0,
        "decline_count": 0,
        "advance_decline_ratio": float("nan"),
        "universe_size": 0,
        "as_of_date": str(as_of_date),
    }
