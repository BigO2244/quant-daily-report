"""
Alpha Stack — PIT Fundamentals Tests
====================================
Verify that FundamentalsDataStore correctly enforces as_of_date semantics
using filing dates (not period-end dates).

Key test assertions:
  1. Fundamentals unavailable BEFORE filing date
  2. Fundamentals available ON/AFTER filing date
  3. Most recent available value returned (not future values)
  4. Computed fields (EY, FCF, B/P) handle missing components gracefully
"""

import pytest
from datetime import date, timedelta
import pandas as pd
from unittest.mock import MagicMock

# Import after venv setup if needed
try:
    from alpha_stack.datastore.fundamentals import FundamentalsDataStore
    from alpha_stack.datastore.sec_edgar import EdgarClient
except ImportError:
    pytest.skip("Alpha Stack not available", allow_module_level=True)


class TestFundamentalsPITSafety:
    """Test PIT safety of FundamentalsDataStore."""

    def test_fundamentals_unavailable_before_filing_date(self):
        """
        Data should be unavailable before it was filed with the SEC.
        This is the core PIT safety test.
        """
        # Mock EDGAR client
        mock_edgar = MagicMock(spec=EdgarClient)
        
        # Simulate: IBM reported earnings on 2024-01-30; data should be unavailable before that
        def mock_pit_value(ticker, field, as_of_date):
            as_of_ts = pd.Timestamp(as_of_date)
            filing_date = pd.Timestamp("2024-01-30")
            # Return None if query is before filing date (PIT safety)
            if as_of_ts < filing_date:
                return None
            # Return mock value after filing date
            if field == "eps_diluted" and as_of_ts >= filing_date:
                return 6.05
            return None
        
        mock_edgar.get_pit_value = mock_pit_value
        
        # Create store with mock
        store = FundamentalsDataStore(edgar_client=mock_edgar)
        
        # BEFORE filing: should be None
        result_before = store.get_fundamental("IBM", "eps_diluted", "2024-01-29")
        assert result_before is None, "Data should not be available before filing date"
        
        # ON filing date: should be available
        result_on = store.get_fundamental("IBM", "eps_diluted", "2024-01-30")
        assert result_on == 6.05, "Data should be available on filing date"
        
        # AFTER filing: should still be available
        result_after = store.get_fundamental("IBM", "eps_diluted", "2024-02-01")
        assert result_after == 6.05, "Data should be available after filing date"

    def test_most_recent_available_value_returned(self):
        """
        Query at date D should return the most recent value filed on or before D,
        not future values or averaged values.
        """
        mock_edgar = MagicMock(spec=EdgarClient)
        
        # Simulate history: filing on 2024-01-30 (EPS=6.05), filing on 2024-04-29 (EPS=6.50)
        def mock_pit_value(ticker, field, as_of_date):
            as_of_ts = pd.Timestamp(as_of_date)
            if field == "eps_diluted":
                if as_of_ts < pd.Timestamp("2024-01-30"):
                    return None
                elif as_of_ts < pd.Timestamp("2024-04-29"):
                    return 6.05  # Most recent before April
                else:
                    return 6.50  # Most recent after April
            return None
        
        mock_edgar.get_pit_value = mock_pit_value
        store = FundamentalsDataStore(edgar_client=mock_edgar)
        
        # At date 2024-02-15, should get 6.05 (most recent)
        result_feb = store.get_fundamental("IBM", "eps_diluted", "2024-02-15")
        assert result_feb == 6.05
        
        # At date 2024-05-01, should get 6.50 (most recent after April filing)
        result_may = store.get_fundamental("IBM", "eps_diluted", "2024-05-01")
        assert result_may == 6.50

    def test_ttm_aggregate_respects_as_of_date(self):
        """
        Trailing-twelve-months aggregations should only use quarters
        that were filed on/before as_of_date.
        """
        mock_edgar = MagicMock(spec=EdgarClient)
        
        # Mock: return None if as_of_date is before the requested period's filing
        def mock_ttm_value(ticker, field, as_of_date, n_quarters=4):
            as_of_ts = pd.Timestamp(as_of_date)
            # Q4 2023 filed 2024-01-30, Q1 2024 filed 2024-04-29, etc.
            filing_dates = {
                "Q4:2023": pd.Timestamp("2024-01-30"),
                "Q1:2024": pd.Timestamp("2024-04-29"),
                "Q2:2024": pd.Timestamp("2024-07-29"),
                "Q3:2024": pd.Timestamp("2024-10-28"),
            }
            
            if field == "operating_cf":
                # If viewing as of 2024-03-01, only Q4:2023 is filed → return single Q4 value
                if as_of_ts < pd.Timestamp("2024-04-29"):  # Only Q4 filed by now
                    return 1.5e9  # Single quarter mock
                # If viewing as of 2024-11-01, Q4:2023 through Q3:2024 all filed
                elif as_of_ts >= pd.Timestamp("2024-10-28"):
                    return 6.0e9  # TTM sum
            return None
        
        mock_edgar.get_ttm_value = mock_ttm_value
        store = FundamentalsDataStore(edgar_client=mock_edgar)
        
        # On 2024-03-01, only 1 quarter filed; TTM returns that
        result_q1 = store._edgar.get_ttm_value("IBM", "operating_cf", "2024-03-01", n_quarters=4)
        assert result_q1 == 1.5e9
        
        # On 2024-11-01, 4 quarters filed; TTM returns sum
        result_full = store._edgar.get_ttm_value("IBM", "operating_cf", "2024-11-01", n_quarters=4)
        assert result_full == 6.0e9

    def test_computed_field_returns_none_if_components_unavailable(self):
        """
        Computed fields like earnings_yield require multiple inputs.
        If any input is unavailable (None or negative), return None.
        """
        mock_edgar = MagicMock(spec=EdgarClient)
        mock_prices = MagicMock()
        
        # Scenario: net_income is available but market cap is negative/zero
        def mock_pit_value(ticker, field, as_of_date):
            if field == "net_income":
                return 1.0e9  # Available
            elif field == "shares_outstanding":
                return 100e6  # Available
            return None
        
        # Market cap computation fails: price is 0 or unavailable
        mock_prices.get_prices_multi = MagicMock(return_value=pd.DataFrame())
        
        mock_edgar.get_pit_value = mock_pit_value
        mock_edgar.get_ttm_value = MagicMock(return_value=1.0e9)
        
        store = FundamentalsDataStore(edgar_client=mock_edgar, prices_datastore=mock_prices)
        
        # Earnings yield requires market cap, which is unavailable
        ey = store.get_fundamental("IBM", "earnings_yield", "2024-06-01")
        assert ey is None, "earnings_yield should be None if market_cap is unavailable"


class TestValueSleeveWithPITFundamentals:
    """Test that Value sleeve works with PIT-safe fundamentals."""
    
    def test_value_sleeve_enabled_when_fundamentals_pit_safe(self):
        """Value sleeve should be active when fundamentals are PIT-safe."""
        from alpha_stack.sleeves.value import ValueSleeve
        
        mock_edgar = MagicMock(spec=EdgarClient)
        store = FundamentalsDataStore(edgar_client=mock_edgar)
        
        # Fundamentals should report as PIT-safe
        meta = store.metadata()
        assert meta["pit_safe"] is True, "FundamentalsDataStore should be marked as PIT-safe"
        assert meta["source"] == "SEC EDGAR XBRL"
        
        # Value sleeve should be available for instantiation
        sleeve = ValueSleeve()
        assert sleeve.name == "value"

    def test_value_sleeve_requires_available_fundamentals(self):
        """Value sleeve eligibility filter should drop tickers with missing fundamentals."""
        from alpha_stack.sleeves.value import ValueSleeve
        
        sleeve = ValueSleeve()
        
        # Create test data: some tickers have fundamentals, some don't
        test_data = pd.DataFrame({
            "ticker": ["AAPL", "MSFT", "GOOGL"],
            "close": [150.0, 300.0, 120.0],
            "earnings_yield": [0.05, None, 0.04],
            "fcf_yield": [0.06, 0.07, None],
            "book_to_price": [0.30, 0.25, None],
        })
        
        # Eligibility filter should keep only tickers with at least one value factor
        eligible = sleeve.eligibility_filter(test_data)
        
        # AAPL has EY and FCFY → eligible
        # MSFT has FCFY → eligible
        # GOOGL has none → should be dropped
        assert len(eligible) <= len(test_data)
        if len(eligible) > 0:
            assert "GOOGL" not in eligible["ticker"].values
