"""Ingest trades from ledger into research database."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def ingest_ledger_trades(
    ledger_path: Path,
    run_id: str
) -> list[dict[str, Any]]:
    """Load ledger and normalize into trade records."""
    if not ledger_path.exists():
        logger.warning(f"[ALPHA_LAB] Ledger not found: {ledger_path}")
        return []
    
    try:
        # Use paper.ledger.load_ledger to respect existing conventions
        from paper.ledger import load_ledger
        df = load_ledger(str(ledger_path))
    except ImportError:
        # Fallback to direct CSV read
        df = pd.read_csv(ledger_path)
    
    if df.empty:
        logger.warning("[ALPHA_LAB] Ledger is empty")
        return []
    
    trades = []
    
    for _, row in df.iterrows():
        trades.append({
            "trade_date": row.get("trade_date"),
            "ticker": str(row.get("ticker", "")).upper(),
            "side": str(row.get("side", "")).upper(),
            "qty": float(row.get("quantity", 0.0)),
            "fill_price": float(row.get("fill_price", 0.0)) if pd.notna(row.get("fill_price")) else None,
            "order_id": str(row.get("order_id", "")),
            "sleeve": str(row.get("sleeve", "")) if pd.notna(row.get("sleeve")) else None,
            "source": str(row.get("source", "")) if pd.notna(row.get("source")) else None,
            "reason": str(row.get("reason", "")) if pd.notna(row.get("reason")) else None,
            "run_id": str(row.get("run_id", "")) if pd.notna(row.get("run_id")) else None,
            "source_file": str(ledger_path.name)
        })
    
    logger.info(f"[ALPHA_LAB] Ingested {len(trades)} trades from ledger")
    return trades


def compute_trade_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary statistics from trades."""
    if not trades:
        return {
            "num_trades": 0,
            "num_dates": 0,
            "date_range": None,
            "buys": 0,
            "sells": 0,
            "gross_notional": None
        }
    
    df = pd.DataFrame(trades)
    
    buys = len(df[df["side"] == "BUY"])
    sells = len(df[df["side"] == "SELL"])
    
    # Compute gross notional if fill_price exists
    gross_notional = None
    if "fill_price" in df.columns and "qty" in df.columns:
        df_priced = df.dropna(subset=["fill_price"])
        if not df_priced.empty:
            gross_notional = (df_priced["qty"] * df_priced["fill_price"]).abs().sum()
    
    return {
        "num_trades": len(df),
        "num_dates": len(df["trade_date"].unique()),
        "date_range": (df["trade_date"].min(), df["trade_date"].max()),
        "buys": buys,
        "sells": sells,
        "gross_notional": gross_notional
    }


def derive_positions_from_ledger(
    ledger_path: Path,
    asof_date: str,
    run_id: str
) -> list[dict[str, Any]]:
    """Use paper.positions to rebuild positions as of date."""
    if not ledger_path.exists():
        logger.warning(f"[ALPHA_LAB] Ledger not found: {ledger_path}")
        return []
    
    try:
        from paper.ledger import load_ledger
        from paper.positions import rebuild_positions_from_ledger
        
        ledger_df = load_ledger(str(ledger_path))
        if ledger_df.empty:
            return []
        
        result = rebuild_positions_from_ledger(ledger_df, asof_date)
        positions_df = result["positions"]
        cash = result["cash"]
        
        positions = []
        for _, row in positions_df.iterrows():
            positions.append({
                "asof_date": asof_date,
                "ticker": str(row["ticker"]).upper(),
                "shares": float(row["shares"]),
                "avg_cost": float(row.get("avg_cost", 0.0)),
                "market_price": None,  # Will be filled if prices available
                "market_value": None,
                "sleeve": str(row.get("sleeve", "")) if pd.notna(row.get("sleeve")) else None
            })
        
        logger.info(f"[ALPHA_LAB] Derived {len(positions)} positions as of {asof_date}, cash={cash:.2f}")
        return positions
    
    except Exception as e:
        logger.error(f"[ALPHA_LAB] Error deriving positions: {e}")
        return []
