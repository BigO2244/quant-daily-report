"""
Alpha Stack — Prices DataStore
================================
PIT-safe OHLCV price data via yfinance with local caching.

PIT safety for prices: yfinance close prices are PIT-safe when using
end_date = as_of_date. We never look forward past as_of_date.

Caching: prices are cached as Parquet files keyed by ticker.
Cache is considered stale after `cache_ttl_hours` and is refreshed.
Backtest callers typically pass explicit date ranges, so caching is
a performance optimization only.
"""

from __future__ import annotations

import hashlib
import logging
import os
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd

from alpha_stack.datastore.base import DataStoreBase, DataStorePITWarning

logger = logging.getLogger(__name__)


class PricesDataStore(DataStoreBase):
    """
    OHLCV price data store backed by yfinance.

    Parameters
    ----------
    cache_dir : str or Path, optional
        Directory for price cache (Parquet files).
        Defaults to 'alpha_stack_cache/prices' relative to cwd.
    cache_ttl_hours : int
        Stale threshold for cache entries (default 8 hours).
    """

    def __init__(
        self,
        cache_dir: str | Path = "alpha_stack_cache/prices",
        cache_ttl_hours: int = 8,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._ttl = timedelta(hours=cache_ttl_hours)
        self._memory: dict[str, pd.DataFrame] = {}  # in-process memory cache

    # ------------------------------------------------------------------ #
    # DataStoreBase implementation                                         #
    # ------------------------------------------------------------------ #

    def get_price_history(
        self,
        symbol: str,
        start_date: date | str,
        end_date: date | str,
    ) -> pd.DataFrame:
        """
        Return OHLCV history PIT-safe: data available on or before end_date.

        Returns
        -------
        DataFrame with columns: [date, open, high, low, close, volume]
        """
        start = _to_date(start_date)
        end = _to_date(end_date)

        df = self._fetch_with_cache(symbol, start, end)
        if df is None or df.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

        # PIT filter: only return rows on or before end_date
        df = df[df["date"] <= pd.Timestamp(end)].copy()
        df = df[df["date"] >= pd.Timestamp(start)].copy()
        return df.sort_values("date").reset_index(drop=True)

    def get_prices_multi(
        self,
        symbols: List[str],
        start_date: date | str,
        end_date: date | str,
    ) -> pd.DataFrame:
        """
        Return OHLCV for multiple symbols as a long DataFrame.

        Returns
        -------
        DataFrame with columns: [date, ticker, open, high, low, close, volume]
        """
        frames = []
        for sym in symbols:
            df = self.get_price_history(sym, start_date, end_date)
            if not df.empty:
                df["ticker"] = sym
                frames.append(df)
        if not frames:
            return pd.DataFrame(columns=["date", "ticker", "open", "high", "low", "close", "volume"])
        result = pd.concat(frames, ignore_index=True)
        return result.sort_values(["ticker", "date"]).reset_index(drop=True)

    def get_fundamental(
        self,
        symbol: str,
        field: str,
        as_of_date: date | str,
    ) -> Optional[float]:
        """
        Prices DataStore does not provide fundamentals.
        Delegates to FundamentalsDataStore.
        """
        warnings.warn(
            f"PricesDataStore.get_fundamental() called for {symbol}/{field}. "
            "Use FundamentalsDataStore for fundamental data.",
            DataStorePITWarning,
            stacklevel=2,
        )
        return None

    def get_macro_series(
        self,
        series_name: str,
        as_of_date: date | str,
    ) -> Optional[float]:
        """Delegates to MacroDataStore."""
        warnings.warn(
            "PricesDataStore.get_macro_series() called. Use MacroDataStore.",
            DataStorePITWarning,
            stacklevel=2,
        )
        return None

    def get_breadth_snapshot(self, as_of_date: date | str) -> dict:
        """Delegates to BreadthDataStore."""
        warnings.warn(
            "PricesDataStore.get_breadth_snapshot() called. Use BreadthDataStore.",
            DataStorePITWarning,
            stacklevel=2,
        )
        return {}

    def metadata(self) -> dict:
        return {
            "name": "PricesDataStore",
            "pit_safe": True,
            "source": "yfinance",
            "notes": (
                "OHLCV prices are PIT-safe: end_date parameter filters all future data. "
                "Corporate actions (splits/divs) are adjusted by yfinance auto_adjust=True."
            ),
        }

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _fetch_with_cache(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> Optional[pd.DataFrame]:
        """Fetch from memory cache, disk cache, or yfinance."""
        cache_key = f"{symbol}_{start}_{end}"
        if cache_key in self._memory:
            return self._memory[cache_key]

        # Check disk cache
        cache_file = self._cache_dir / f"{symbol.upper()}.parquet"
        if cache_file.exists():
            mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
            if datetime.now() - mtime < self._ttl:
                try:
                    df = pd.read_parquet(cache_file)
                    df["date"] = pd.to_datetime(df["date"])
                    # Check if cache covers the requested range
                    if not df.empty and df["date"].max() >= pd.Timestamp(end) - timedelta(days=5):
                        self._memory[cache_key] = df
                        return df
                except Exception as exc:
                    logger.debug("[PRICES] Cache read failed for %s: %s", symbol, exc)

        # Fetch from yfinance
        df = self._fetch_yfinance(symbol, start, end)
        if df is not None and not df.empty:
            try:
                df.to_parquet(cache_file, index=False)
            except Exception as exc:
                logger.debug("[PRICES] Cache write failed for %s: %s", symbol, exc)
            self._memory[cache_key] = df

        return df

    def _fetch_yfinance(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> Optional[pd.DataFrame]:
        """Download OHLCV from yfinance and normalise columns."""
        try:
            import yfinance as yf
            # Fetch a day after end to get the end_date close included by yfinance
            fetch_end = end + timedelta(days=1)
            raw = yf.download(
                symbol,
                start=start.isoformat(),
                end=fetch_end.isoformat(),
                progress=False,
                auto_adjust=True,
            )
            if raw is None or raw.empty:
                logger.warning("[PRICES] Empty response from yfinance for %s", symbol)
                return None

            # Flatten MultiIndex columns if present (yfinance >= 0.2.x)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = [c[0].lower() for c in raw.columns]
            else:
                raw.columns = [str(c).lower() for c in raw.columns]

            raw = raw.reset_index()
            raw = raw.rename(columns={"date": "date", "index": "date"})

            # Normalise
            raw["date"] = pd.to_datetime(raw["date"]).dt.normalize()
            for col in ["open", "high", "low", "close", "volume"]:
                if col not in raw.columns:
                    raw[col] = float("nan")

            result = raw[["date", "open", "high", "low", "close", "volume"]].copy()
            result = result.dropna(subset=["close"])
            logger.debug(
                "[PRICES] Fetched %d rows for %s (%s → %s)",
                len(result), symbol, start, end,
            )
            return result.sort_values("date").reset_index(drop=True)

        except Exception as exc:
            logger.error("[PRICES] yfinance fetch failed for %s: %s", symbol, exc)
            return None


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _to_date(d: date | str) -> date:
    if isinstance(d, date):
        return d
    return datetime.strptime(str(d), "%Y-%m-%d").date()
