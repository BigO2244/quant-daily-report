"""
Alpha Stack — PIT Validation Research
======================================
Validate PIT-safe fundamentals semantics and coverage.

Coverage
--------
1. Verify data unavailable before filing_date
2. Verify market cap reconstruction PIT-safety
3. Cache behavior and deterministic replay
4. Universe coverage statistics by date
5. Field-level missingness analysis
"""

from __future__ import annotations

import logging
from datetime import date, timedelta, datetime
from typing import Optional, Dict, List, Tuple, Any
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class PITValidator:
    """Validate PIT-safe fundamentals behavior."""

    def __init__(self, fundamentals_store, prices_store, universe_tickers: List[str]):
        self.fundamentals = fundamentals_store
        self.prices = prices_store
        self.tickers = universe_tickers

    def test_data_unavailable_before_filing(
        self,
        ticker: str,
        field: str,
        filing_date: date,
        days_before: int = 10
    ) -> Dict[str, Any]:
        """
        Test that data is unavailable before filing_date.
        
        Returns dict with:
            before_date: value before filing (should be None)
            on_date: value on filing (should be available)
            after_date: value after filing (should be available or updated)
        """
        before = filing_date - timedelta(days=days_before)
        on_date = filing_date
        after = filing_date + timedelta(days=1)

        result = {
            "ticker": ticker,
            "field": field,
            "filing_date": str(filing_date),
            "days_before": days_before,
        }

        # Test before filing
        try:
            val_before = self.fundamentals.get_fundamental(ticker, field, str(before))
            result["before_filing"] = {
                "value": val_before,
                "available": val_before is not None,
            }
        except Exception as e:
            result["before_filing"] = {"error": str(e)}

        # Test on filing
        try:
            val_on = self.fundamentals.get_fundamental(ticker, field, str(on_date))
            result["on_filing"] = {
                "value": val_on,
                "available": val_on is not None,
            }
        except Exception as e:
            result["on_filing"] = {"error": str(e)}

        # Test after filing
        try:
            val_after = self.fundamentals.get_fundamental(ticker, field, str(after))
            result["after_filing"] = {
                "value": val_after,
                "available": val_after is not None,
            }
        except Exception as e:
            result["after_filing"] = {"error": str(e)}

        return result

    def test_market_cap_reconstruction(
        self,
        ticker: str,
        as_of_date: str,
    ) -> Dict[str, Any]:
        """
        Test that market cap is reconstructed PIT-safely
        (shares from EDGAR, price from prices_store).
        """
        result = {
            "ticker": ticker,
            "as_of_date": as_of_date,
        }

        try:
            # Get shares from EDGAR (filing-date safe)
            shares = self.fundamentals._edgar.get_pit_value(
                ticker, "shares_outstanding", as_of_date
            ) if hasattr(self.fundamentals, '_edgar') else None
            result["shares_outstanding"] = shares

            # Get price from prices store (as-of-date safe)
            if hasattr(self.fundamentals, '_prices'):
                price_df = self.fundamentals._prices.get_prices_multi(
                    [ticker], as_of_date, as_of_date
                )
                if not price_df.empty:
                    price = price_df.iloc[0].get("close")
                else:
                    price = None
            else:
                price = None
            result["price"] = price

            # Reconstruct market cap
            if shares and shares > 0 and price and price > 0:
                mkt_cap = shares * price
                result["market_cap"] = mkt_cap
                result["mkt_cap_valid"] = True
            else:
                result["market_cap"] = None
                result["mkt_cap_valid"] = False

            # Test computed fields (EY, FCFY, B/P)
            try:
                ey = self.fundamentals.get_fundamental(ticker, "earnings_yield", as_of_date)
                fcfy = self.fundamentals.get_fundamental(ticker, "fcf_yield", as_of_date)
                bp = self.fundamentals.get_fundamental(ticker, "book_to_price", as_of_date)
                result["computed_fields"] = {
                    "earnings_yield": ey,
                    "fcf_yield": fcfy,
                    "book_to_price": bp,
                }
            except Exception as e:
                result["computed_fields_error"] = str(e)

        except Exception as e:
            result["error"] = str(e)

        return result

    def get_universe_coverage(
        self,
        as_of_date: str,
        fields: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        For a given as_of_date, report coverage statistics.
        
        Returns dict with:
            total_tickers: number of universe symbols tested
            coverage_by_field: % of tickers with non-null values per field
            complete_coverage: % of tickers with all fields available
            by_sector: coverage grouped by sector (if sector_map available)
        """
        if fields is None:
            fields = ["earnings_yield", "fcf_yield", "book_to_price"]

        result = {
            "as_of_date": as_of_date,
            "total_tickers": len(self.tickers),
            "coverage_by_field": {},
            "tickers_with_any_factor": 0,
            "tickers_with_all_factors": 0,
        }

        factor_avail = {ticker: [] for ticker in self.tickers}
        all_null_count = 0

        for ticker in self.tickers:
            has_any = False
            has_all = True
            for field in fields:
                try:
                    val = self.fundamentals.get_fundamental(ticker, field, as_of_date)
                    is_avail = val is not None
                    factor_avail[ticker].append(is_avail)
                    has_any = has_any or is_avail
                    has_all = has_all and is_avail
                except Exception:
                    factor_avail[ticker].append(False)
                    has_all = False

            if has_any:
                result["tickers_with_any_factor"] += 1
            if has_all:
                result["tickers_with_all_factors"] += 1
            if not has_any:
                all_null_count += 1

        # Compute per-field coverage
        for i, field in enumerate(fields):
            count_available = sum(
                1 for ticker in self.tickers
                if i < len(factor_avail[ticker]) and factor_avail[ticker][i]
            )
            pct = (count_available / len(self.tickers) * 100) if self.tickers else 0
            result["coverage_by_field"][field] = {
                "count": count_available,
                "pct": round(pct, 2),
            }

        result["all_null_pct"] = round(all_null_count / len(self.tickers) * 100, 2) if self.tickers else 0
        result["any_factor_pct"] = round(result["tickers_with_any_factor"] / len(self.tickers) * 100, 2) if self.tickers else 0
        result["all_factors_pct"] = round(result["tickers_with_all_factors"] / len(self.tickers) * 100, 2) if self.tickers else 0

        return result

    def get_coverage_time_series(
        self,
        start_date: date,
        end_date: date,
        step_days: int = 7,
        fields: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """
        Get coverage statistics over a date range.
        
        Returns DataFrame with rows per date, columns:
            date, any_factor_pct, all_factors_pct, ey_pct, fcfy_pct, bp_pct
        """
        if fields is None:
            fields = ["earnings_yield", "fcf_yield", "book_to_price"]

        results = []
        current = start_date
        while current <= end_date:
            cov = self.get_universe_coverage(str(current), fields)
            row = {
                "date": current,
                "any_factor_pct": cov["any_factor_pct"],
                "all_factors_pct": cov["all_factors_pct"],
            }
            for field in fields:
                col_name = f"{field}_pct"
                row[col_name] = cov["coverage_by_field"][field]["pct"]
            results.append(row)
            current += timedelta(days=step_days)

        return pd.DataFrame(results)


class CacheValidator:
    """Validate EDGAR cache behavior and deterministic replay."""

    def __init__(self, fundamentals_store):
        self.fundamentals = fundamentals_store

    def test_deterministic_replay(
        self,
        tickers: List[str],
        as_of_date: str,
    ) -> Dict[str, Any]:
        """
        Test that repeated calls return identical values (deterministic).
        """
        result = {
            "as_of_date": as_of_date,
            "deterministic": True,
            "mismatches": [],
        }

        for ticker in tickers[:5]:  # Limit to avoid rate limiting
            try:
                # First call
                val1 = self.fundamentals.get_fundamental(ticker, "earnings_yield", as_of_date)
                # Second call (should hit cache)
                val2 = self.fundamentals.get_fundamental(ticker, "earnings_yield", as_of_date)

                if val1 != val2:
                    result["deterministic"] = False
                    result["mismatches"].append({
                        "ticker": ticker,
                        "call1": val1,
                        "call2": val2,
                    })
            except Exception as e:
                result[f"{ticker}_error"] = str(e)

        return result
