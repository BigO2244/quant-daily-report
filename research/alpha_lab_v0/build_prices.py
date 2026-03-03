"""Price data ingestion and management."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def ingest_prices_from_csv(
    prices_csv: Path,
    run_id: str
) -> list[dict[str, Any]]:
    """Load price data from CSV with columns: date, ticker, close."""
    if not prices_csv or not prices_csv.exists():
        logger.info("[ALPHA_LAB] No prices CSV provided")
        return []
    
    try:
        df = pd.read_csv(prices_csv)
        
        # Normalize column names
        col_map = {}
        for col in df.columns:
            col_lower = col.lower()
            if col_lower in ("date", "Date"):
                col_map[col] = "date"
            elif col_lower in ("ticker", "symbol", "Ticker", "Symbol"):
                col_map[col] = "ticker"
            elif col_lower in ("close", "Close", "price", "Price"):
                col_map[col] = "close"
        
        df = df.rename(columns=col_map)
        
        # Validate required columns
        if "date" not in df.columns or "ticker" not in df.columns or "close" not in df.columns:
            logger.error("[ALPHA_LAB] prices CSV must have columns: date, ticker, close")
            return []
        
        # Normalize tickers
        df["ticker"] = df["ticker"].str.upper()
        
        prices = []
        for _, row in df.iterrows():
            prices.append({
                "date": row["date"],
                "ticker": row["ticker"],
                "close": float(row["close"])
            })
        
        logger.info(f"[ALPHA_LAB] Ingested {len(prices)} price records from {prices_csv.name}")
        return prices
    
    except Exception as e:
        logger.error(f"[ALPHA_LAB] Error reading prices CSV: {e}")
        return []
