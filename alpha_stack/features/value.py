"""
Alpha Stack — Value Features
================================
Value factor computation using SEC EDGAR fundamentals.

STATUS: ACTIVE — Computes PIT-safe value factors from SEC EDGAR XBRL.

Factors:
    earnings_yield      = net_income (TTM) / market_cap
    fcf_yield           = (operating_cf - capex, TTM) / market_cap
    book_to_price       = stockholders_equity / market_cap
    (shareholder_yield: UNAVAILABLE — requires dividend/buyback data not in SEC EDGAR)

Composite:
    S_value_raw = 0.40*z(EY) + 0.30*z(FCFY) + 0.30*z(B/P)
                 (weights rebalanced after SHY removal)

All z-scores are sector-relative (within-sector normalization) to control
for sector composition bias.

PIT SAFETY: All fundamental values use filing availability date (filed_date),
not period-end date. Filing lags are built-in (40-90 days post period-end).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def compute_value_features(
    fundamentals_store,
    universe_tickers: list,
    as_of_date: date | str,
    sector_map: Optional[dict] = None,
) -> pd.DataFrame:
    """
    Compute PIT-safe value features from SEC EDGAR fundamentals.

    Parameters
    ----------
    fundamentals_store : FundamentalsDataStore
        PIT-safe fundamentals interface
    universe_tickers : list of str
        Universe members to compute features for
    as_of_date : date or str (YYYY-MM-DD)
        As-of date for fundamentals lookup
    sector_map : dict, optional
        {ticker: sector_name} for sector-relative normalization.

    Returns
    -------
    DataFrame with columns:
        ticker, earnings_yield, fcf_yield, book_to_price,
        sector, z_ey, z_fcfy, z_bp
    (shareholder_yield removed as it is unavailable in SEC EDGAR)
    """
    records = []

    for ticker in universe_tickers:
        try:
            ey = fundamentals_store.get_fundamental(ticker, "earnings_yield", as_of_date)
            fcfy = fundamentals_store.get_fundamental(ticker, "fcf_yield", as_of_date)
            bp = fundamentals_store.get_fundamental(ticker, "book_to_price", as_of_date)
            sector = sector_map.get(ticker, "Unknown") if sector_map else "Unknown"

            records.append({
                "ticker": ticker,
                "sector": sector,
                "earnings_yield": ey,
                "fcf_yield": fcfy,
                "book_to_price": bp,
            })
        except Exception as e:
            logger.warning("[VALUE_FEATURES] Error fetching fundamentals for %s: %s", ticker, e)
            continue

    if not records:
        logger.warning("[VALUE_FEATURES] No fundamental data available for universe")
        return _empty_value_features()

    df = pd.DataFrame(records)

    # Sector-relative z-scores (within-sector normalization)
    for factor_col, z_col in [
        ("earnings_yield", "z_ey"),
        ("fcf_yield", "z_fcfy"),
        ("book_to_price", "z_bp"),
    ]:
        if factor_col in df.columns:
            if "sector" in df.columns and len(df["sector"].unique()) > 1:
                # Sector-relative: z-score within each sector
                df[z_col] = df.groupby("sector")[factor_col].transform(_zscore_safe)
            else:
                # Cross-sectional: z-score across all
                df[z_col] = _zscore_safe(df[factor_col])

    return df[[c for c in df.columns if c in [
        "ticker", "earnings_yield", "fcf_yield", "book_to_price",
        "sector", "z_ey", "z_fcfy", "z_bp"
    ]]].reset_index(drop=True)


def _zscore_safe(s: pd.Series) -> pd.Series:
    """Safe z-score: returns 0 if std is 0."""
    mu = s.mean()
    sigma = s.std()
    if sigma == 0 or pd.isna(sigma):
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sigma


def _empty_value_features(columns: Optional[list] = None) -> pd.DataFrame:
    """Return empty value features DataFrame."""
    if columns is None:
        columns = ["ticker", "earnings_yield", "fcf_yield", "book_to_price",
                   "sector", "z_ey", "z_fcfy", "z_bp"]
    return pd.DataFrame(columns=columns)
