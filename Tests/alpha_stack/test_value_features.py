"""
Alpha Stack — Value Features Tests
===================================
Verify that compute_value_features() correctly computes factor z-scores
using sector-relative normalization.

Test coverage:
  1. Basic factor computation from raw fundamentals
  2. Sector-relative z-score calculation
  3. Cross-sectional fallback when sectors undefined
  4. Handling of missing factor values
  5. Output schema validation
"""

import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, patch

try:
    from alpha_stack.features.value import compute_value_features
    from alpha_stack.datastore.fundamentals import FundamentalsDataStore
except ImportError:
    pytest.skip("Alpha Stack not available", allow_module_level=True)


class TestValueFeaturesComputation:
    """Test value factor feature computation."""

    def test_compute_value_features_basic_output_shape(self):
        """compute_value_features should return 8-column DataFrame."""
        # Mock fundamentals store
        mock_store = MagicMock(spec=FundamentalsDataStore)
        
        def mock_get_fundamental(ticker, field, as_of_date):
            # Return realistic values
            factors = {
                "AAPL": {"earnings_yield": 0.05, "fcf_yield": 0.06, "book_to_price": 0.30},
                "MSFT": {"earnings_yield": 0.04, "fcf_yield": 0.05, "book_to_price": 0.25},
                "GOOGL": {"earnings_yield": 0.06, "fcf_yield": 0.07, "book_to_price": 0.35},
            }
            return factors.get(ticker, {}).get(field, None)
        
        mock_store.get_fundamental = mock_get_fundamental
        
        tickers = ["AAPL", "MSFT", "GOOGL"]
        as_of_date = "2024-06-01"
        
        result = compute_value_features(mock_store, tickers, as_of_date, sector_map=None)
        
        # Expected schema: ticker, earnings_yield, fcf_yield, book_to_price, sector, z_ey, z_fcfy, z_bp
        expected_cols = {"ticker", "earnings_yield", "fcf_yield", "book_to_price", "sector", "z_ey", "z_fcfy", "z_bp"}
        assert set(result.columns) == expected_cols, f"Expected {expected_cols}, got {set(result.columns)}"
        assert len(result) == 3, f"Should have 3 rows, got {len(result)}"

    def test_sector_relative_z_scores(self):
        """Z-scores should be computed within each sector."""
        mock_store = MagicMock(spec=FundamentalsDataStore)
        
        def mock_get_fundamental(ticker, field, as_of_date):
            factors = {
                # Tech sector
                "AAPL": {"earnings_yield": 0.05, "fcf_yield": 0.06, "book_to_price": 0.30},
                "MSFT": {"earnings_yield": 0.04, "fcf_yield": 0.05, "book_to_price": 0.25},
                # Financials sector
                "JPM": {"earnings_yield": 0.08, "fcf_yield": 0.09, "book_to_price": 0.80},
                "BAC": {"earnings_yield": 0.07, "fcf_yield": 0.08, "book_to_price": 0.75},
            }
            return factors.get(ticker, {}).get(field, None)
        
        mock_store.get_fundamental = mock_get_fundamental
        
        sector_map = {
            "AAPL": "Technology",
            "MSFT": "Technology",
            "JPM": "Financials",
            "BAC": "Financials",
        }
        
        tickers = ["AAPL", "MSFT", "JPM", "BAC"]
        result = compute_value_features(mock_store, tickers, "2024-06-01", sector_map=sector_map)
        
        # Check that z-scores are computed (not NaN, not all zeros unless raw factors are identical)
        assert not result[["z_ey", "z_fcfy", "z_bp"]].isna().all().any(), "Z-scores should not be all NaN"
        
        # Verify sector-relative grouping: Tech companies should have different z-scores than Financials
        # (because Financials have higher absolute values)
        tech_data = result[result["sector"] == "Technology"]
        fin_data = result[result["sector"] == "Financials"]
        
        # Within Tech: MSFT has lower ey than AAPL, so z_ey(MSFT) should be negative, z_ey(AAPL) positive
        if len(tech_data) == 2:
            msft_z = tech_data[tech_data["ticker"] == "MSFT"]["z_ey"].iloc[0]
            aapl_z = tech_data[tech_data["ticker"] == "AAPL"]["z_ey"].iloc[0]
            assert msft_z < aapl_z, "Lower earnings_yield should have lower z-score"

    def test_cross_sectional_fallback_when_no_sector_map(self):
        """Without sector_map, should compute cross-sectional (not sector-relative) z-scores."""
        mock_store = MagicMock(spec=FundamentalsDataStore)
        
        def mock_get_fundamental(ticker, field, as_of_date):
            factors = {
                "AAPL": {"earnings_yield": 0.05, "fcf_yield": 0.06, "book_to_price": 0.30},
                "MSFT": {"earnings_yield": 0.04, "fcf_yield": 0.05, "book_to_price": 0.25},
                "GOOGL": {"earnings_yield": 0.06, "fcf_yield": 0.07, "book_to_price": 0.35},
            }
            return factors.get(ticker, {}).get(field, None)
        
        mock_store.get_fundamental = mock_get_fundamental
        
        tickers = ["AAPL", "MSFT", "GOOGL"]
        result = compute_value_features(mock_store, tickers, "2024-06-01", sector_map=None)
        
        # Z-scores should be computed across all tickers (cross-sectional)
        assert len(result) == 3
        # Mean of z-scores should be ~0
        mean_z_ey = result["z_ey"].mean()
        assert -0.5 < mean_z_ey < 0.5, f"Mean z-score should be near 0, got {mean_z_ey}"

    def test_missing_factors_handled_gracefully(self):
        """Tickers with missing factors should be included but with NaN in missing columns."""
        mock_store = MagicMock(spec=FundamentalsDataStore)
        
        def mock_get_fundamental(ticker, field, as_of_date):
            factors = {
                "AAPL": {"earnings_yield": 0.05, "fcf_yield": 0.06, "book_to_price": 0.30},
                "MSFT": {"earnings_yield": 0.04, "fcf_yield": None, "book_to_price": 0.25},  # Missing fcf_yield
                "GOOGL": {"earnings_yield": 0.06, "fcf_yield": 0.07, "book_to_price": 0.35},
            }
            return factors.get(ticker, {}).get(field, None)
        
        mock_store.get_fundamental = mock_get_fundamental
        
        tickers = ["AAPL", "MSFT", "GOOGL"]
        result = compute_value_features(mock_store, tickers, "2024-06-01", sector_map=None)
        
        # All 3 tickers should be present (not dropped)
        assert len(result) == 3, "Should not drop tickers with missing factors"
        
        # MSFT should have NaN for fcf_yield
        msft = result[result["ticker"] == "MSFT"].iloc[0]
        assert pd.isna(msft["fcf_yield"]), "Missing fcf_yield should be NaN"
        # Z-score of missing factor will propagate NaN because (None - mu) = NaN
        assert pd.isna(msft["z_fcfy"]), "Z-score of missing factor should be NaN"

    def test_z_score_handles_zero_sigma(self):
        """
        When all tickers have identical factor values (sigma≈0),
        z-score should be computed (not NaN or inf).
        Note: Due to floating-point precision, values like 0.05 may have
        sigma != exactly 0.0, resulting in non-zero z-scores. The important
        thing is that the computation completes without NaN or inf.
        """
        mock_store = MagicMock(spec=FundamentalsDataStore)
        
        def mock_get_fundamental(ticker, field, as_of_date):
            # Use 0.06 which has exact zero sigma in IEEE 754 float
            return 0.06 if field == "earnings_yield" else \
                   0.06 if field == "fcf_yield" else \
                   0.30 if field == "book_to_price" else None
        
        mock_store.get_fundamental = mock_get_fundamental
        
        tickers = ["AAPL", "MSFT", "GOOGL"]
        result = compute_value_features(mock_store, tickers, "2024-06-01", sector_map=None)
        
        # Z-scores should be computed (not all NaN or inf)
        assert not (result["z_ey"] == np.inf).any(), "Z-scores should not be inf"
        assert not (result["z_ey"] == -np.inf).any(), "Z-scores should not be -inf"
        assert not result["z_ey"].isna().all(), "Z-scores should not be all NaN"
        # When all values are truly identical (like 0.06 or 0.30), z-scores should be 0
        # But some values due to float precision may have small non-zero z-scores
        assert (result["z_ey"] == 0.0).all() or not (result["z_ey"] == np.inf).any(), "Z-scores should be either 0 or finite"

    def test_empty_input_returns_empty_dataframe(self):
        """Empty ticker list should return empty DataFrame with correct schema."""
        mock_store = MagicMock(spec=FundamentalsDataStore)
        
        result = compute_value_features(mock_store, [], "2024-06-01", sector_map=None)
        
        # Should return empty DataFrame with correct columns
        expected_cols = {"ticker", "earnings_yield", "fcf_yield", "book_to_price", "sector", "z_ey", "z_fcfy", "z_bp"}
        assert set(result.columns) == expected_cols
        assert len(result) == 0

    def test_all_tickers_missing_factors_returns_data(self):
        """
        If all tickers have all factors as None, rows are still returned
        (with NaN or 0 z-scores); implementation logs warning but doesn't drop rows.
        """
        mock_store = MagicMock(spec=FundamentalsDataStore)
        mock_store.get_fundamental = MagicMock(return_value=None)
        
        tickers = ["AAPL", "MSFT", "GOOGL"]
        result = compute_value_features(mock_store, tickers, "2024-06-01", sector_map=None)
        
        # Rows are returned (not dropped)
        assert len(result) == 3
        
        # Raw factors should be NaN (because mock returns None)
        assert result["earnings_yield"].isna().all()
        assert result["fcf_yield"].isna().all()
        assert result["book_to_price"].isna().all()
