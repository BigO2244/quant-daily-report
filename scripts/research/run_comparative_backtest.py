#!/usr/bin/env python
"""
run_comparative_backtest.py — Execute the Value vs Trend comparative backtest.

Usage:
    python run_comparative_backtest.py [--start_date YYYY-MM-DD] [--end_date YYYY-MM-DD]

Outputs:
    - outputs/backtests/comparative_metrics_summary.csv
    - outputs/backtests/COMPARATIVE_BACKTEST_SUMMARY.md
    - outputs/backtests/{config}_equity_curve.csv (for each config)
    - outputs/backtests/{config}_trades.csv (for each config)
"""

import sys
import logging
from datetime import datetime
from pathlib import Path

# Add repo to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

# Standard imports
import pandas as pd

# Local imports
from alpha_stack.datastore import PricesDataStore, FundamentalsDataStore
from scripts.comparative_backtest_runner import ComparativeBacktestRunner


def main():
    """Execute comparative backtest with Trend and Value sleeves."""
    
    # ── setup ─────────────────────────────────────────────────────
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s — %(name)s — %(levelname)s — %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    # Parse CLI args
    start_date = "2020-01-01"
    end_date = datetime.now().strftime("%Y-%m-%d")
    
    if len(sys.argv) > 1 and sys.argv[1].startswith("--start"):
        start_date = sys.argv[1].split("=")[1]
    if len(sys.argv) > 2 and sys.argv[2].startswith("--end"):
        end_date = sys.argv[2].split("=")[1]
    
    logger.info(f"Starting comparative backtest: {start_date} to {end_date}")
    
    # ── load datastores ──────────────────────────────────────────
    logger.info("Initializing data stores...")
    try:
        prices_store = PricesDataStore()
        fundamentals_store = FundamentalsDataStore()
    except Exception as e:
        logger.error(f"Failed to initialize data stores: {e}")
        sys.exit(1)
    
    # ── get universe ─────────────────────────────────────────────
    # Load tickers from data/universe.csv
    logger.info("Loading universe...")
    try:
        universe_path = repo_root / "data" / "universe.csv"
        if not universe_path.exists():
            logger.error(f"Universe file not found: {universe_path}")
            sys.exit(1)
        universe_df = pd.read_csv(universe_path)
        universe_tickers = universe_df["ticker"].tolist()
        logger.info(f"Universe: {len(universe_tickers)} tickers")
    except Exception as e:
        logger.error(f"Failed to load universe: {e}")
        sys.exit(1)
    
    # ── sector map (optional) ────────────────────────────────────
    sector_map = {}  # Empty for now; ComparativeBacktestRunner makes it optional
    
    # ── initialize runner ────────────────────────────────────────
    logger.info("Initializing ComparativeBacktestRunner...")
    try:
        runner = ComparativeBacktestRunner(
            fundamentals_store=fundamentals_store,
            prices_store=prices_store,
            universe_tickers=universe_tickers,
            sector_map=sector_map,
            output_dir=str(repo_root / "outputs" / "backtests"),
            initial_equity=10_000.0,
            commission_bps=5.0,
            slippage_bps=2.0,
        )
    except Exception as e:
        logger.error(f"Failed to initialize runner: {e}")
        sys.exit(1)
    
    # ── run all configurations ───────────────────────────────────
    logger.info("Running all backtest configurations...")
    try:
        results = runner.run_all(
            start_date=pd.Timestamp(start_date),
            end_date=pd.Timestamp(end_date),
        )
        logger.info(f"✓ Backtest completed successfully")
        
        # Print summary
        print("\n" + "="*80)
        print("COMPARATIVE BACKTEST RESULTS")
        print("="*80)
        for config_name, metrics in results.items():
            print(f"\n{config_name.upper()}:")
            for metric_name, value in metrics.items():
                if isinstance(value, float):
                    print(f"  {metric_name:20s}: {value:8.2%}")
                else:
                    print(f"  {metric_name:20s}: {value}")
        
    except Exception as e:
        logger.error(f"Backtest execution failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    logger.info("✓ All outputs written to outputs/backtests/")
    logger.info("✓ Review COMPARATIVE_BACKTEST_SUMMARY.md for full analysis")


if __name__ == "__main__":
    main()
