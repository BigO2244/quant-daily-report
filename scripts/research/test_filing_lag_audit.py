#!/usr/bin/env python
"""Quick test of filing lag audit"""
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

# Test imports
try:
    import pandas as pd
    from alpha_stack.datastore.fundamentals import FundamentalsDataStore
    from alpha_stack.datastore.prices import PricesDataStore
    from alpha_stack.research.filing_lag_audit import audit_filing_lags
    logger.info("✓ All imports successful")
except Exception as e:
    logger.error(f"Import error: {e}")
    sys.exit(1)

# Load universe (sample)
try:
    universe_df = pd.read_csv('data/universe.csv')
    tickers = universe_df['ticker'].tolist()[:30]  # Sample 30
    sector_map_series = universe_df.get('sector')
    if sector_map_series is not None:
        sector_map = dict(zip(universe_df['ticker'], sector_map_series))
    else:
        sector_map = {t: 'Unknown' for t in universe_df['ticker']}
    logger.info(f"✓ Loaded {len(tickers)} tickers from universe")
except Exception as e:
    logger.error(f"Error loading universe: {e}")
    sys.exit(1)

# Run audit
try:
    prices_store = PricesDataStore()
    fundamentals_store = FundamentalsDataStore(prices_datastore=prices_store)
    logger.info("✓ Datastores initialized")
    
    results = audit_filing_lags(
        fundamentals_store,
        tickers,
        sector_map,
        as_of_date='2026-03-08'
    )
    
    if results is not None:
        logger.info("\n=== Filing Lag Audit Results ===")
        print(results.to_string(index=False))
        
        output_path = Path('outputs/alpha_stack_validation/filing_lag_audit_results.csv')
        output_path.parent.mkdir(parents=True, exist_ok=True)
        results.to_csv(output_path, index=False)
        logger.info(f"✓ Saved results to {output_path}")
    else:
        logger.warning("No results from audit (empty universe?)")
        
except Exception as e:
    logger.error(f"Audit error: {e}", exc_info=True)
    sys.exit(1)

logger.info("✓ Filing lag audit complete")
