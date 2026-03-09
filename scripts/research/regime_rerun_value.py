"""
Regime-Aware Backtest Matrix — Value Re-run
==========================================
Re-runs ONLY value_only and combined_static configurations after EDGAR CIK fix.
Uses existing trend_only and combined_allocator results.

Usage:
    cd quant-daily-report-main
    python scripts/regime_rerun_value.py

Output:
    Updates outputs/regime_matrix/master_results.csv with corrected value metrics
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("regime_value_rerun")

# ── path setup ───────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Import original matrix runner
from scripts.regime_aware_backtest_matrix import RegimeAwareBacktestMatrix, CONFIGS

def main():
    """Re-run only value_only and combined_static configurations."""
    
    logger.info("=" * 70)
    logger.info("RE-RUNNING VALUE CONFIGS AFTER EDGAR FIX")
    logger.info("=" * 70)
    
    # Backup existing results
    output_dir = ROOT / "outputs" / "regime_matrix"
    master_file = output_dir / "master_results.csv"
    backup_file = output_dir / "master_results_backup_pre_value_fix.csv"
    
    if master_file.exists():
        logger.info("Backing up existing master_results.csv...")
        import shutil
        shutil.copy(master_file, backup_file)
        logger.info("Backup saved: %s", backup_file.name)
        
        # Load existing results
        existing_df = pd.read_csv(master_file)
        logger.info("Loaded %d existing results", len(existing_df))
        
        # Keep only trend_only and combined_allocator (already complete)
        keep_df = existing_df[existing_df["config"].isin(["trend_only", "combined_allocator"])].copy()
        logger.info("Keeping %d trend_only and combined_allocator results", len(keep_df))
    else:
        keep_df = pd.DataFrame()
        logger.info("No existing master_results.csv found.")
    
    # Monkey-patch CONFIGS to only run value and combined_static
    import scripts.regime_aware_backtest_matrix as matrix_module
    original_configs = matrix_module.CONFIGS
    matrix_module.CONFIGS = ["value_only", "combined_static"]
    
    logger.info("Running matrix for configs: %s", matrix_module.CONFIGS)
    
    try:
        # Run matrix (will only do value_only and combined_static)
        runner = RegimeAwareBacktestMatrix(force_refresh=False)
        new_df = runner.run()
        
        if new_df is None or new_df.empty:
            logger.error("Matrix run returned no results!")
            return
        
        logger.info("Matrix run complete: %d new results", len(new_df))
        
        # Merge with existing results
        if not keep_df.empty:
            # Remove old value_only and combined_static from keep_df (shouldn't exist, but just in case)
            keep_df = keep_df[~keep_df["config"].isin(["value_only", "combined_static"])]
            
            # Combine
            final_df = pd.concat([keep_df, new_df], ignore_index=True)
            
            # Sort by window, config
            config_order = ["trend_only", "value_only", "combined_static", "combined_allocator"]
            final_df["config_sort"] = final_df["config"].apply(lambda x: config_order.index(x) if x in config_order else 99)
            final_df = final_df.sort_values(["window", "config_sort"]).drop(columns=["config_sort"])
            final_df = final_df.reset_index(drop=True)
        else:
            final_df = new_df
        
        logger.info("Final master results: %d rows", len(final_df))
        
        # Save merged results
        final_df.to_csv(master_file, index=False)
        logger.info("Updated master_results.csv saved.")
        
    finally:
        # Restore original CONFIGS
        matrix_module.CONFIGS = original_configs
    
    logger.info("=" * 70)
    logger.info("VALUE RE-RUN COMPLETE")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
