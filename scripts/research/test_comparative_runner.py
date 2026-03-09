#!/usr/bin/env python
"""Quick validation test of comparative backtest runner"""
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

try:
    import sys
    sys.path.insert(0, '.')
    
    import pandas as pd
    from alpha_stack.datastore.fundamentals import FundamentalsDataStore
    from alpha_stack.datastore.prices import PricesDataStore
    from scripts.comparative_backtest_runner import ComparativeBacktestRunner
    
    logger.info("✓ Imports successful")
    
    # Small sample
    tickers = ["AAPL", "MSFT", "GOOGL"]
    
    prices_store = PricesDataStore()
    fundamentals_store = FundamentalsDataStore(prices_datastore=prices_store)
    
    logger.info("✓ Datastores initialized")
    
    runner = ComparativeBacktestRunner(
        fundamentals_store=fundamentals_store,
        prices_store=prices_store,
        universe_tickers=tickers,
        output_dir="outputs/backtests",
    )
    
    logger.info("✓ ComparativeBacktestRunner initialized")
    logger.info("Ready to run backtest")
    
except Exception as e:
    logger.error(f"Error: {e}", exc_info=True)
    sys.exit(1)
