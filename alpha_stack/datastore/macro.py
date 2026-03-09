"""
Alpha Stack — Macro DataStore
================================
Macro time-series data using yfinance as the data source.

PIT safety: yfinance historical data is PIT-safe for prices (VIX closes,
ETF closes). We use end_date filtering to prevent look-ahead.

Supported series:
    "vix"           — CBOE VIX close (^VIX)
    "spy_close"     — S&P 500 ETF close price (SPY)
    "spy_ema50"     — SPY 50-day EMA (derived from history)
    "spy_ema200"    — SPY 200-day EMA (derived from history)
    "spy_trend_t1"  — (Close/EMA200) - 1
    "spy_trend_t2"  — (EMA50/EMA200) - 1
    "tlt_close"     — 20-year Treasury ETF close (TLT proxy for yield)
    "hyg_close"     — HY Bond ETF close (HYG proxy for credit spreads)

Future (placeholder, returns None until wired):
    "yield_curve_slope" — 10yr - 2yr treasury spread
    "credit_spread"     — OAS on HY index (Bloomberg/FRED needed)
    "growth_nowcast"    — GDP nowcast (Fed nowcast API or FRED)
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from alpha_stack.datastore.base import DataStoreBase, DataStorePITWarning
from alpha_stack.datastore.prices import PricesDataStore, _to_date

logger = logging.getLogger(__name__)

# Tickers backing each series
_SERIES_MAP = {
    "vix": "^VIX",
    "spy_close": "SPY",
    "spy_ema50": "SPY",
    "spy_ema200": "SPY",
    "spy_trend_t1": "SPY",
    "spy_trend_t2": "SPY",
    "tlt_close": "TLT",
    "hyg_close": "HYG",
}

# Series that require longer lookback for indicator computation
_LONG_LOOKBACK_SERIES = {"spy_ema50", "spy_ema200", "spy_trend_t1", "spy_trend_t2"}
_LOOKBACK_DAYS = 252  # 1 year of history for EMA stability


class MacroDataStore(DataStoreBase):
    """
    Macro time-series store built on top of PricesDataStore.

    Parameters
    ----------
    prices_store : PricesDataStore, optional
        Shared prices store. Creates one internally if not provided.
    """

    def __init__(self, prices_store: Optional[PricesDataStore] = None) -> None:
        self._prices = prices_store or PricesDataStore()
        self._cache: Dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------ #
    # DataStoreBase implementation                                         #
    # ------------------------------------------------------------------ #

    def get_price_history(self, symbol, start_date, end_date) -> pd.DataFrame:
        return self._prices.get_price_history(symbol, start_date, end_date)

    def get_fundamental(self, symbol, field, as_of_date) -> Optional[float]:
        return None  # Not provided by this store

    def get_macro_series(
        self,
        series_name: str,
        as_of_date: date | str,
    ) -> Optional[float]:
        """
        Return the most recent value of *series_name* available as of *as_of_date*.

        Parameters
        ----------
        series_name : str
            One of: vix, spy_close, spy_ema50, spy_ema200,
                    spy_trend_t1, spy_trend_t2, tlt_close, hyg_close
        as_of_date : date or str

        Returns
        -------
        float or None
        """
        end = _to_date(as_of_date)

        if series_name not in _SERIES_MAP:
            logger.warning("[MACRO] Unknown series: %s", series_name)
            return None

        df = self._get_series_history(series_name, end)
        if df is None or df.empty:
            return None

        # PIT: latest row on or before as_of_date
        available = df[df["date"] <= pd.Timestamp(end)]
        if available.empty:
            return None

        val = available.iloc[-1][series_name]
        return float(val) if pd.notna(val) else None

    def get_macro_history(
        self,
        series_name: str,
        start_date: date | str,
        end_date: date | str,
    ) -> pd.DataFrame:
        """
        Return a full time-series for *series_name* between start and end dates.

        Returns
        -------
        DataFrame with columns: [date, {series_name}]
        """
        end = _to_date(end_date)
        df = self._get_series_history(series_name, end)
        if df is None or df.empty:
            return pd.DataFrame(columns=["date", series_name])

        start = _to_date(start_date)
        mask = (df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))
        return df[mask].copy().reset_index(drop=True)

    def get_breadth_snapshot(self, as_of_date: date | str) -> dict:
        return {}  # Delegated to BreadthDataStore

    def metadata(self) -> dict:
        return {
            "name": "MacroDataStore",
            "pit_safe": True,
            "source": "yfinance",
            "notes": (
                "Macro series are ETF/index closes — PIT-safe via end_date filtering. "
                "Yield curve slope and growth nowcast are placeholders (return None) "
                "until a proper macro data feed (FRED, Bloomberg) is wired."
            ),
        }

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _get_series_history(
        self,
        series_name: str,
        as_of_date: date,
    ) -> Optional[pd.DataFrame]:
        """Fetch, compute, and cache a derived macro series."""
        ticker = _SERIES_MAP[series_name]
        cache_key = f"{series_name}_{as_of_date}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        # For EMA-based series we need extra lookback
        lookback = _LOOKBACK_DAYS if series_name in _LONG_LOOKBACK_SERIES else 30
        start = as_of_date - timedelta(days=lookback + 10)

        raw = self._prices.get_price_history(ticker, start, as_of_date)
        if raw is None or raw.empty:
            return None

        df = raw[["date", "close"]].copy()

        if series_name == "vix":
            df = df.rename(columns={"close": "vix"})

        elif series_name == "spy_close":
            df = df.rename(columns={"close": "spy_close"})

        elif series_name in ("spy_ema50", "spy_ema200", "spy_trend_t1", "spy_trend_t2"):
            df["spy_ema50"] = df["close"].ewm(span=50, adjust=False).mean()
            df["spy_ema200"] = df["close"].ewm(span=200, adjust=False).mean()
            df["spy_trend_t1"] = (df["close"] / df["spy_ema200"]) - 1
            df["spy_trend_t2"] = (df["spy_ema50"] / df["spy_ema200"]) - 1
            df = df.drop(columns=["close"])

        elif series_name == "tlt_close":
            df = df.rename(columns={"close": "tlt_close"})

        elif series_name == "hyg_close":
            df = df.rename(columns={"close": "hyg_close"})

        # Ensure the target column exists
        if series_name not in df.columns:
            logger.warning("[MACRO] Series %s not derived for ticker %s", series_name, ticker)
            return None

        result = df[["date", series_name]].dropna(subset=[series_name])
        self._cache[cache_key] = result
        return result

    def get_combined_macro_snapshot(self, as_of_date: date | str) -> Dict[str, Optional[float]]:
        """
        Return a dict of all macro series values as of as_of_date.
        Useful for regime engine inputs.
        """
        return {series: self.get_macro_series(series, as_of_date)
                for series in _SERIES_MAP}
