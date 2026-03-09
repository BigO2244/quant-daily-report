"""
Alpha Stack — Filing Lag Audit
================================
Validates filing lag assumptions by sector.

Purpose:
  Verify that filing lags don't exceed 60-day threshold for majority of universe.
  Identify sectors (Healthcare, REIT) that file late.

Output:
  CSV with sector-level filing lag statistics (p50, p75, p90, pct exceeding 60d)
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def audit_filing_lags(
    fundamentals_store,
    universe_tickers: list,
    sector_map: dict,
    as_of_date: date | str = "2026-03-08",
) -> Optional[pd.DataFrame]:
    """
    Audit filing lags by sector.

    For each ticker, measure filing_lag = as_of_date - max(filed_date) for any field.
    Report by sector: p50, p75, p90 lags, and % exceeding 60 days.

    Parameters
    ----------
    fundamentals_store : FundamentalsDataStore
    universe_tickers : list of str
    sector_map : dict {ticker: sector}
    as_of_date : date or str

    Returns
    -------
    DataFrame with columns:
        sector, count, median_lag_days, p75_lag_days, p90_lag_days,
        pct_exceeding_60d, pct_exceeding_75d
    """
    as_of_ts = pd.Timestamp(as_of_date)
    records = []

    for ticker in universe_tickers:
        try:
            # Get filing ages for raw EDGAR fields that back the value factors
            # (computed fields don't have direct filing metadata)
            ages = []

            for field in ["net_income", "operating_cf", "equity"]:  # Raw fields for EY, FCFY, B/P
                filing_info = fundamentals_store.get_filing_metadata(ticker, field, as_of_date)
                if filing_info and "filed_date" in filing_info:
                    filing_ts = pd.Timestamp(filing_info["filed_date"])
                    age_days = (as_of_ts - filing_ts).days
                    ages.append(age_days)

            if ages:
                max_age = max(ages)
                sector = sector_map.get(ticker, "Unknown")
                records.append({
                    "ticker": ticker,
                    "sector": sector,
                    "max_filing_age_days": max_age,
                })

        except Exception as e:
            logger.debug("[FILING_LAG_AUDIT] Error for %s: %s", ticker, e)
            continue

    if not records:
        logger.warning("[FILING_LAG_AUDIT] No filing metadata available")
        return None

    df = pd.DataFrame(records)

    # Aggregate by sector
    agg_results = []
    for sector in sorted(df["sector"].unique()):
        sector_df = df[df["sector"] == sector]
        ages = sector_df["max_filing_age_days"]

        agg_results.append({
            "sector": sector,
            "count": len(sector_df),
            "median_lag_days": int(ages.quantile(0.50)),
            "p75_lag_days": int(ages.quantile(0.75)),
            "p90_lag_days": int(ages.quantile(0.90)),
            "pct_exceeding_60d": float((ages > 60).mean() * 100),
            "pct_exceeding_75d": float((ages > 75).mean() * 100),
        })

    result_df = pd.DataFrame(agg_results)
    logger.info("[FILING_LAG_AUDIT] Sector-level filing lags:\n%s", result_df.to_string())
    return result_df


# ============================================================================
# Runner
# ============================================================================

if __name__ == "__main__":
    # Example: audit filing lags for a sample universe
    from alpha_stack.datastore.fundamentals import FundamentalsDataStore
    from alpha_stack.datastore.prices import PricesDataStore

    logging.basicConfig(level=logging.INFO)

    # Load universe
    universe_csv = Path("data/universe.csv")
    if not universe_csv.exists():
        logger.error("data/universe.csv not found")
        exit(1)

    universe_df = pd.read_csv(universe_csv)
    universe_tickers = universe_df["ticker"].tolist()[:100]  # Sample first 100

    # Load sectors (from data if available, else synthetic)
    sector_map = {}
    if Path("data/universe.csv").exists():
        sector_df = pd.read_csv("data/universe.csv")
        if "sector" in sector_df.columns:
            sector_map = dict(zip(sector_df["ticker"], sector_df["sector"]))

    # Initialize datastores
    prices_store = PricesDataStore()
    fundamentals_store = FundamentalsDataStore(prices_datastore=prices_store)

    # Run audit
    results = audit_filing_lags(
        fundamentals_store,
        universe_tickers,
        sector_map,
        as_of_date="2026-03-08",
    )

    if results is not None:
        # Save results
        output_path = Path("outputs/alpha_stack_validation/filing_lag_audit_results.csv")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        results.to_csv(output_path, index=False)
        logger.info("[FILING_LAG_AUDIT] Saved to %s", output_path)
