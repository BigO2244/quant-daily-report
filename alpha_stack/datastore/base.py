"""
Alpha Stack — DataStore Base
=============================
Abstract interface that all DataStore implementations must satisfy.

PIT (Point-in-Time) contract
-----------------------------
Every method that accepts an `as_of_date` parameter must only return
data that was knowable on that date. Specifically:

- Prices: use close prices on or before as_of_date (yfinance download
  respects this naturally when end=as_of_date).
- Fundamentals: use the *filed_date* (or equivalent availability date),
  NOT the period_end_date. A Q3 earnings filing released in November
  is NOT available in October even though the period ended September.
- Macro series: use observations available as of as_of_date.

If a DataStore cannot guarantee PIT semantics for a given field,
it MUST warn the caller via logging.warning() and include a
`pit_safe: false` indicator in its metadata.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Optional

import pandas as pd


class DataStoreBase(ABC):
    """Abstract base class for all Alpha Stack data stores."""

    # ------------------------------------------------------------------ #
    # Required interface                                                   #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def get_price_history(
        self,
        symbol: str,
        start_date: date | str,
        end_date: date | str,
    ) -> pd.DataFrame:
        """
        Return OHLCV history for *symbol* between start_date and end_date (inclusive).

        Parameters
        ----------
        symbol : str
            Ticker symbol (e.g. "AAPL").
        start_date : date or str
            Inclusive start (YYYY-MM-DD).
        end_date : date or str
            Inclusive end (YYYY-MM-DD).

        Returns
        -------
        DataFrame with columns: [date, open, high, low, close, volume]
            Sorted ascending by date.
            Returns empty DataFrame if no data available.
        """

    @abstractmethod
    def get_fundamental(
        self,
        symbol: str,
        field: str,
        as_of_date: date | str,
    ) -> Optional[float]:
        """
        Return a single fundamental value for *symbol* as of *as_of_date*.

        PIT contract: must use the *filing availability date*, not the
        period end date. Implementations that cannot guarantee this MUST
        raise DataStorePITWarning and return None.

        Parameters
        ----------
        symbol : str
        field : str
            E.g. "earnings_yield", "book_to_price", "roe", "roic".
        as_of_date : date or str
            Look up value that was known on this date.

        Returns
        -------
        float or None
            None if not available or PIT-safe data is unavailable.
        """

    @abstractmethod
    def get_macro_series(
        self,
        series_name: str,
        as_of_date: date | str,
    ) -> Optional[float]:
        """
        Return the latest available value of a macro series as of *as_of_date*.

        Parameters
        ----------
        series_name : str
            E.g. "vix", "spy_trend", "tlt_yield", "hyg_spread".
        as_of_date : date or str

        Returns
        -------
        float or None
        """

    @abstractmethod
    def get_breadth_snapshot(
        self,
        as_of_date: date | str,
    ) -> dict:
        """
        Return a breadth snapshot dict for *as_of_date*.

        Returns
        -------
        dict with keys:
            pct_above_200dma : float  — % of universe above 200-DMA
            pct_above_50dma  : float  — % of universe above 50-DMA
            advance_count    : int    — # advancing (close > prev close)
            decline_count    : int    — # declining
            as_of_date       : str    — ISO date string
        """

    # ------------------------------------------------------------------ #
    # Optional metadata                                                    #
    # ------------------------------------------------------------------ #

    def metadata(self) -> dict:
        """
        Return implementation metadata including PIT-safety flags.

        Returns
        -------
        dict with keys: name, pit_safe, source, notes
        """
        return {
            "name": self.__class__.__name__,
            "pit_safe": False,
            "source": "unknown",
            "notes": "Override metadata() in subclass.",
        }


# ------------------------------------------------------------------ #
# Sentinel exception for PIT violations                                #
# ------------------------------------------------------------------ #

class DataStorePITWarning(UserWarning):
    """
    Raised (as a warning) when a DataStore cannot guarantee PIT semantics.

    Code that calls fundamental or macro data should catch or filter this
    warning in backtest contexts to avoid look-ahead bias.
    """
