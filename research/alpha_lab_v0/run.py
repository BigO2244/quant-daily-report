"""Main orchestrator for Alpha Lab V0."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.alpha_lab_v0 import db, ingest_signals, ingest_ledger, build_prices, compute_nav, metrics, report

logger = logging.getLogger(__name__)


def generate_run_id() -> str:
    """Generate deterministic run ID with UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def run_alpha_lab(
    signals_store: Path,
    ledger_path: Path | None,
    prices_csv: Path | None,
    benchmark_csv: Path | None,
    out_root: Path,
    run_id: str | None = None
) -> dict[str, Any]:
    """Main Alpha Lab execution.
    
    Returns dict with run_dir, report_path, db_path.
    """
    # Generate run ID if not provided
    if run_id is None:
        run_id = generate_run_id()
    
    # Create run directory
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"[ALPHA_LAB] Starting run: {run_id}")
    logger.info(f"[ALPHA_LAB] Run directory: {run_dir}")
    
    # Initialize database
    db_path = run_dir / "research_db.duckdb"
    conn = db.init_research_db(db_path, run_id)
    
    # Track inputs for report
    inputs = {
        "signals_store": str(signals_store),
        "ledger_path": str(ledger_path) if ledger_path else None,
        "prices_csv": str(prices_csv) if prices_csv else None,
        "benchmark_csv": str(benchmark_csv) if benchmark_csv else None
    }
    
    # 1. Ingest signals
    signals = ingest_signals.ingest_signals_from_store(signals_store, run_id)
    signals_stats = ingest_signals.compute_signal_stats(signals)
    if signals:
        db.insert_signals(conn, signals, run_id)
        logger.info(f"[ALPHA_LAB] Inserted {len(signals)} signal records")
    
    # 2. Ingest ledger
    trades = []
    trades_stats = {}
    positions = []
    
    if ledger_path and ledger_path.exists():
        trades = ingest_ledger.ingest_ledger_trades(ledger_path, run_id)
        trades_stats = ingest_ledger.compute_trade_stats(trades)
        if trades:
            db.insert_trades(conn, trades, run_id)
            logger.info(f"[ALPHA_LAB] Inserted {len(trades)} trade records")
            
            # Derive latest positions
            if trades_stats.get("date_range"):
                latest_date = trades_stats["date_range"][1]
                positions = ingest_ledger.derive_positions_from_ledger(
                    ledger_path, latest_date, run_id
                )
                if positions:
                    db.insert_positions(conn, positions, run_id)
                    logger.info(f"[ALPHA_LAB] Inserted {len(positions)} position records")
    
    # 3. Ingest prices (optional)
    prices = []
    if prices_csv and prices_csv.exists():
        prices = build_prices.ingest_prices_from_csv(prices_csv, run_id)
        if prices:
            db.insert_prices(conn, prices, run_id)
            logger.info(f"[ALPHA_LAB] Inserted {len(prices)} price records")
    
    # 4. Compute NAV series
    nav_series = []
    if ledger_path and ledger_path.exists():
        nav_series = compute_nav.compute_nav_series(ledger_path, prices, run_id)
        if nav_series:
            db.insert_nav_series(conn, nav_series, run_id)
            logger.info(f"[ALPHA_LAB] Computed NAV for {len(nav_series)} dates")
    
    # 5. Compute metrics
    daily_metrics = []
    nav_summary = {}
    if nav_series:
        daily_metrics, nav_summary = metrics.compute_returns_and_drawdowns(nav_series)
        logger.info(f"[ALPHA_LAB] Computed metrics: max_dd={nav_summary.get('max_drawdown')}")
    
    # 6. Compute turnover
    turnover = []
    if trades and nav_series:
        turnover = metrics.compute_turnover(trades, nav_series)
    
    # 7. Generate report
    report_path = report.generate_report(
        run_dir,
        run_id,
        signals_stats,
        trades_stats,
        positions,
        nav_summary,
        inputs
    )
    
    # 8. Save tables
    report.save_tables(
        run_dir,
        signals,
        trades,
        positions,
        nav_series,
        daily_metrics,
        turnover
    )
    
    # 9. Create charts
    if nav_series or daily_metrics:
        report.create_charts(run_dir, nav_series, daily_metrics)
    
    # 10. Save run metadata
    run_meta = {
        "run_id": run_id,
        "inputs": inputs,
        "outputs": {
            "db_path": str(db_path),
            "report_path": str(report_path)
        },
        "stats": {
            "signals": signals_stats,
            "trades": trades_stats,
            "nav": nav_summary
        }
    }
    
    meta_path = run_dir / "run_meta.json"
    with open(meta_path, "w") as f:
        json.dump(run_meta, f, indent=2)
    
    # Create README
    readme_path = run_dir / "README_run.md"
    with open(readme_path, "w") as f:
        f.write(f"# Alpha Lab Run: {run_id}\n\n")
        f.write("## Reproduce This Run\n\n")
        f.write("```bash\n")
        f.write("python -m research.alpha_lab_v0.cli \\\n")
        f.write(f"  --signals_store {inputs['signals_store']} \\\n")
        if inputs['ledger_path']:
            f.write(f"  --ledger_path {inputs['ledger_path']} \\\n")
        if inputs['prices_csv']:
            f.write(f"  --prices_csv {inputs['prices_csv']} \\\n")
        f.write(f"  --out_root {out_root} \\\n")
        f.write(f"  --run_id {run_id}\n")
        f.write("```\n\n")
        f.write("## Outputs\n\n")
        f.write(f"- **Report:** {report_path.name}\n")
        f.write(f"- **Database:** {db_path.name}\n")
        f.write("- **Tables:** tables/\n")
        f.write("- **Charts:** charts/\n")
    
    conn.close()
    
    logger.info(f"[ALPHA_LAB] Run complete: {run_id}")
    
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "report_path": str(report_path),
        "db_path": str(db_path)
    }
