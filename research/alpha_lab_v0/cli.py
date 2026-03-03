"""CLI for Alpha Lab V0."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from research.alpha_lab_v0.run import run_alpha_lab

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s"
    )


def auto_detect_ledger() -> Path | None:
    """Auto-detect ledger path using paper.paths if available."""
    try:
        from paper.paths import LEDGER_TRADES_PATH
        if LEDGER_TRADES_PATH.exists():
            return LEDGER_TRADES_PATH
    except ImportError:
        pass
    
    # Fallback paths
    fallback = Path("outputs/ledger/trades.csv")
    if fallback.exists():
        return fallback
    
    return None


def main() -> int:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Alpha Lab V0: Research subsystem for signal and execution analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic run with auto-detected ledger
  python -m research.alpha_lab_v0.cli --signals_store signals_store

  # Full run with prices
  python -m research.alpha_lab_v0.cli \\
    --signals_store signals_store \\
    --ledger_path outputs/ledger/trades.csv \\
    --prices_csv data/prices.csv \\
    --out_root reports/alpha_lab_runs

  # Deterministic run with fixed run_id
  python -m research.alpha_lab_v0.cli \\
    --signals_store signals_store \\
    --run_id test_run_001
        """
    )
    
    parser.add_argument(
        "--signals_store",
        type=Path,
        default=Path("signals_store"),
        help="Path to signals_store directory (default: signals_store)"
    )
    
    parser.add_argument(
        "--signals_json_dir",
        type=Path,
        help="Optional: Additional signals directory (e.g., signals/)"
    )
    
    parser.add_argument(
        "--ledger_path",
        type=Path,
        help="Path to ledger CSV (auto-detected if omitted)"
    )
    
    parser.add_argument(
        "--prices_csv",
        type=Path,
        help="Optional: Price data CSV with columns: date, ticker, close"
    )
    
    parser.add_argument(
        "--benchmark_csv",
        type=Path,
        help="Optional: Benchmark data CSV (e.g., SPY) for regime analysis"
    )
    
    parser.add_argument(
        "--out_root",
        type=Path,
        default=Path("reports/alpha_lab_runs"),
        help="Output root directory (default: reports/alpha_lab_runs)"
    )
    
    parser.add_argument(
        "--run_id",
        type=str,
        help="Optional: Fixed run ID (else auto-generated with timestamp)"
    )
    
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    setup_logging(args.verbose)
    
    # Auto-detect ledger if not provided
    ledger_path = args.ledger_path
    if ledger_path is None:
        ledger_path = auto_detect_ledger()
        if ledger_path:
            logger.info(f"[ALPHA_LAB] Auto-detected ledger: {ledger_path}")
        else:
            logger.warning("[ALPHA_LAB] No ledger found. Proceeding with signals only.")
    
    # Check signals_store exists
    if not args.signals_store.exists():
        logger.error(f"[ALPHA_LAB] signals_store not found: {args.signals_store}")
        return 1
    
    try:
        # Run Alpha Lab
        result = run_alpha_lab(
            signals_store=args.signals_store,
            ledger_path=ledger_path,
            prices_csv=args.prices_csv,
            benchmark_csv=args.benchmark_csv,
            out_root=args.out_root,
            run_id=args.run_id
        )
        
        # Print outputs (minimal stdout per requirements)
        print(f"RUN_ID: {result['run_id']}")
        print(f"RUN_DIR: {result['run_dir']}")
        print(f"REPORT_PATH: {result['report_path']}")
        print(f"DB_PATH: {result['db_path']}")
        
        return 0
    
    except Exception as e:
        logger.error(f"[ALPHA_LAB] Error: {e}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())
