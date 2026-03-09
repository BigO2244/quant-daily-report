"""
Alpha Stack — Fundamentals DataStore
=====================================
Point-in-time fundamental data interface using SEC EDGAR XBRL.

PIT SAFETY STATUS: SAFE
=======================
This module fetches fundamentals from the SEC EDGAR API with strict
filing-date (not period-end-date) filtering. All returned values honor
as_of_date semantics: only data filed with the SEC on or before as_of_date
is returned.

Key safeguards:
  1. Every lookup filters on `filed` (SEC acceptance date), NOT `end` (period-end)
  2. Raw XBRL facts come from official SEC filings (10-K, 10-Q)
  3. Desktop cache mitigates network dependency; TTL = 7 days
  4. Computed fields (earnings_yield, fcf_yield, etc.) use PIT-safe components

Data sources & availability:
  - EPS, equity, shares outstanding: from both 10-K and 10-Q
  - Operating CF, CapEx, net income: from 10-Q (TTM sum) or 10-K (annual) fallback
  - Price data: must be sourced from PricesDataStore (market-sensitive)
  - Note: SEC filings lag fiscal period-end by 40-90 days; backtests must account
           for this filing-lag realism

Limitations in v1:
  - No dividend/buyback data (SHY field unavailable; marked as None)
  - No balance sheet flow-through for quarterly metrics (use trailing-twelve-months fallback)
  - No real-time share count adjustments (may use diluted shares from XBRL, which lags)

Quality sleeve remains disabled (ENABLE_QUALITY_SLEEVE=false) until fundamental
field coverage expands.
"""

from __future__ import annotations

import logging
import warnings
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from alpha_stack.datastore.base import DataStoreBase, DataStorePITWarning
from alpha_stack.datastore.sec_edgar import EdgarClient
from alpha_stack._config_loader import get_section

logger = logging.getLogger(__name__)

# Fields supported by PIT-safe EDGAR (excludes derivatives like book-to-price)
_EDGAR_FIELDS = frozenset([
    "eps_diluted",
    "equity",
    "shares_outstanding",
    "operating_cf",
    "capex",
    "net_income",
    "assets",
    "liabilities",
])

# Computed fields (require multiple EDGAR inputs + external prices)
_COMPUTED_FIELDS = frozenset([
    "earnings_yield",           # EBIT / Enterprise Value
    "fcf_yield",                # Free Cash Flow / Market Cap
    "book_to_price",            # Book Value / Market Cap
    "shareholder_yield",        # ⚠️  UNAVAILABLE (no dividend/buyback data in SEC EDGAR)
])

# Full set of supported fields
SUPPORTED_FIELDS = frozenset(list(_EDGAR_FIELDS) + list(_COMPUTED_FIELDS))


class FundamentalsDataStore(DataStoreBase):
    """
    PIT-safe fundamentals via SEC EDGAR XBRL.

    Parameters
    ----------
    edgar_client : EdgarClient, optional
        Reuse existing client; creates new one if not provided.
    prices_datastore : PricesDataStore, optional
        For computing sec_yield, fcf_yield, book_to_price from prices.
        If not provided, price-based computations return None.
    """

    def __init__(
        self,
        edgar_client: Optional[EdgarClient] = None,
        prices_datastore = None,
    ) -> None:
        self._edgar = edgar_client or EdgarClient()
        self._prices = prices_datastore
        self._warned_once: set = set()

    # ------------------------------------------------------------------ #
    # DataStoreBase implementation                                         #
    # ------------------------------------------------------------------ #

    def get_price_history(
        self,
        symbol: str,
        start_date: date | str,
        end_date: date | str,
    ) -> pd.DataFrame:
        """Not provided by this store — use PricesDataStore."""
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    def get_fundamental(
        self,
        symbol: str,
        field: str,
        as_of_date: date | str,
    ) -> Optional[float]:
        """
        Return a PIT-safe fundamental value.

        Parameters
        ----------
        symbol : str
            Ticker symbol (uppercase recommended)
        field : str
            One of SUPPORTED_FIELDS
        as_of_date : date or str (YYYY-MM-DD)
            As-of date; only filings with `filed` <= as_of_date are considered

        Returns
        -------
        float or None
            The most recent value, or None if unavailable
        """
        if field not in SUPPORTED_FIELDS:
            logger.warning("[FUNDAMENTALS] Unsupported field: %s", field)
            return None

        as_of_ts = pd.Timestamp(as_of_date)

        # Handle EDGAR raw fields
        if field in _EDGAR_FIELDS:
            return self._edgar.get_pit_value(symbol, field, as_of_date)

        # Handle computed fields (require external price data)
        if field in _COMPUTED_FIELDS:
            return self._compute_field(symbol, field, as_of_ts)

        return None

    def get_fundamental_snapshot(
        self,
        symbol: str,
        as_of_date: date | str,
    ) -> Dict[str, Optional[float]]:
        """Return all supported fields for a symbol as a dict."""
        return {field: self.get_fundamental(symbol, field, as_of_date)
                for field in SUPPORTED_FIELDS}

    def get_macro_series(
        self,
        series_name: str,
        as_of_date: date | str,
    ) -> Optional[float]:
        """Not provided by this store — use MacroDataStore."""
        return None

    def get_breadth_snapshot(self, as_of_date: date | str) -> dict:
        """Not provided by this store — use BreadthDataStore."""
        return {}

    def metadata(self) -> dict:
        return {
            "name": "FundamentalsDataStore",
            "pit_safe": True,
            "source": "SEC EDGAR XBRL",
            "notes": (
                "PIT-safe fundamentals from official SEC filings (10-K, 10-Q). "
                "All lookups filter on `filed` date (not period-end). "
                "Filing lag realism (40-90 days) is built-in."
            ),
        }

    # ------------------------------------------------------------------ #
    # Computed fields (derived from EDGAR raw fields + external prices)   #
    # ------------------------------------------------------------------ #

    def _compute_field(
        self,
        symbol: str,
        field: str,
        as_of_ts: pd.Timestamp,
    ) -> Optional[float]:
        """
        Compute a derived field using raw EDGAR values + prices.

        Returns None if any component is unavailable.
        """
        if field == "earnings_yield":
            return self._compute_earnings_yield(symbol, as_of_ts)
        elif field == "fcf_yield":
            return self._compute_fcf_yield(symbol, as_of_ts)
        elif field == "book_to_price":
            return self._compute_book_to_price(symbol, as_of_ts)
        elif field == "shareholder_yield":
            # Requires dividend and buyback data not available in SEC EDGAR
            logger.warning("[FUNDAMENTALS] shareholder_yield unavailable (no dividend/buyback in EDGAR)")
            return None
        return None

    def _compute_earnings_yield(self, symbol: str, as_of_ts: pd.Timestamp) -> Optional[float]:
        """
        Earnings yield ≈ net_income / market_cap
        Uses TTM net income and current price.
        """
        as_of_date_str = str(as_of_ts.date())

        # Get TTM net income (sum last 4 quarters)
        ni = self._edgar.get_ttm_value(symbol, "net_income", as_of_date_str, n_quarters=4)
        if ni is None or ni <= 0:
            return None

        # Get market cap from price + shares outstanding
        mkt_cap = self._get_market_cap_pit(symbol, as_of_ts)
        if mkt_cap is None or mkt_cap <= 0:
            return None

        ey = float(ni / mkt_cap)
        # Sanity check: yield typically in [-0.5, 0.5]
        if ey < -0.5 or ey > 0.5:
            logger.debug("[FUNDAMENTALS] Earnings yield out of range: %s %.3f", symbol, ey)
            return None
        return ey

    def _compute_fcf_yield(self, symbol: str, as_of_ts: pd.Timestamp) -> Optional[float]:
        """
        FCF yield ≈ (operating_cf - capex) / market_cap
        Uses TTM FCF and current price.
        """
        as_of_date_str = str(as_of_ts.date())

        # Get TTM operating CF and CapEx
        op_cf = self._edgar.get_ttm_value(symbol, "operating_cf", as_of_date_str, n_quarters=4)
        capex = self._edgar.get_ttm_value(symbol, "capex", as_of_date_str, n_quarters=4)
        if op_cf is None or capex is None:
            return None

        fcf = op_cf - capex
        if fcf <= 0:  # No positive FCF
            return None

        # Get market cap
        mkt_cap = self._get_market_cap_pit(symbol, as_of_ts)
        if mkt_cap is None or mkt_cap <= 0:
            return None

        fcfy = float(fcf / mkt_cap)
        # Sanity check
        if fcfy < -0.5 or fcfy > 0.5:
            return None
        return fcfy

    def _compute_book_to_price(self, symbol: str, as_of_ts: pd.Timestamp) -> Optional[float]:
        """
        Book-to-price = stockholders_equity / market_cap
        Uses most recent equity and current price.
        """
        as_of_date_str = str(as_of_ts.date())

        # Get most recent stockholders equity
        equity = self._edgar.get_pit_value(symbol, "equity", as_of_date_str)
        if equity is None or equity <= 0:
            return None

        # Get market cap
        mkt_cap = self._get_market_cap_pit(symbol, as_of_ts)
        if mkt_cap is None or mkt_cap <= 0:
            return None

        b2p = float(equity / mkt_cap)
        # Sanity check: typically in [0.2, 3]
        if b2p < 0.1 or b2p > 5:
            return None
        return b2p

    def _get_market_cap_pit(self, symbol: str, as_of_ts: pd.Timestamp) -> Optional[float]:
        """
        Compute market cap = price * shares_outstanding (both PIT-safe).
        Returns None if either component is unavailable.
        """
        as_of_date_str = str(as_of_ts.date())

        # Get share count from EDGAR
        shares = self._edgar.get_pit_value(symbol, "shares_outstanding", as_of_date_str)
        if shares is None or shares <= 0:
            return None

        # Get price from PricesDataStore
        if self._prices is None:
            return None
        price_df = self._prices.get_prices_multi([symbol], as_of_ts, as_of_ts)
        if price_df.empty:
            return None
        price = price_df.iloc[-1].get("close")
        if price is None or price <= 0:
            return None

        return float(shares * price)

    # ------------------------------------------------------------------ #
    # Filed-date utilities                                                #
    # ------------------------------------------------------------------ #

    def get_filing_metadata(
        self,
        symbol: str,
        field: str,
        as_of_date: date | str,
    ) -> Optional[Dict]:
        """
        Get filing metadata for a field (filed_date, period_end, form_type).

        Parameters
        ----------
        symbol : str
        field : str (must be raw EDGAR field)
        as_of_date : date or str

        Returns
        -------
        dict with keys: {ticker, field, filed_date, period_end_date, form_type}
        or None if unavailable
        """
        if field not in _EDGAR_FIELDS:
            return None

        try:
            as_of_ts = pd.Timestamp(as_of_date)

            # Get facts dataframe from EdgarClient (will load from cache or fetch)
            facts_df = self._edgar.get_company_facts(symbol)
            if facts_df is None or facts_df.empty:
                return None

            # Filter to field and as-of-date
            subset = facts_df[facts_df["field_name"] == field].copy()
            subset = subset[subset["filed"] <= as_of_ts]

            if subset.empty:
                return None

            # Get most recent filing
            latest = subset.sort_values("filed").iloc[-1]

            return {
                "ticker": symbol,
                "field": field,
                "filed_date": latest["filed"],
                "period_end_date": latest["end"],
                "form_type": latest.get("form", "unknown"),
            }

        except Exception as e:
            logger.debug("[FILING_METADATA] Error for %s/%s: %s", symbol, field, e)
            return None

    @staticmethod
    def standard_filing_lags() -> Dict[str, int]:
        """
        Conservative filing lag assumptions (calendar days after period end).
        Use these as maximum lag estimates when building a PIT-safe database.

        Returns
        -------
        dict: filing_type → max_lag_days
        """
        return {
            "10-Q": 45,     # SEC deadline: 40 days (large), 45 days (smaller)
            "10-K": 75,     # SEC deadline: 60-90 days depending on filer class
            "earnings_release": 30,  # Press release typically 20-35 days after quarter end
            "default": 60,  # Conservative default
        }

    def print_coverage_report(self, universe_tickers: List[str]) -> dict:
        """
        Print EDGAR ticker→CIK mapping coverage report for a universe.
        
        This helps identify which tickers cannot be mapped to SEC CIK numbers.
        Called during backtest initialization to diagnose data availability.
        
        Parameters
        ----------
        universe_tickers : list of str
            Tickers in the backtest universe
        
        Returns
        -------
        dict with coverage statistics
        """
        coverage = self._edgar.get_mapping_coverage(universe_tickers)
        
        logger.info("=" * 60)
        logger.info("EDGAR TICKER → CIK MAPPING COVERAGE REPORT")
        logger.info("=" * 60)
        logger.info("Total CIK map size: %d entries", coverage["map_size"])
        logger.info("Universe tickers: %d", coverage["total"])
        logger.info("Mapped tickers: %d (%.1f%%)", 
                   coverage["mapped"], coverage["coverage_pct"])
        logger.info("Unmapped tickers: %d", len(coverage["unmapped"]))
        
        if coverage["unmapped"]:
            # Show first 20 unmapped tickers
            unmapped_sample = coverage["unmapped"][:20]
            logger.info("Sample unmapped: %s", ", ".join(unmapped_sample))
            if len(coverage["unmapped"]) > 20:
                logger.info("  ... and %d more", len(coverage["unmapped"]) - 20)
        
        logger.info("=" * 60)
        
        return coverage
