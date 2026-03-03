"""Ingest signals from signals_store directory into research database."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def parse_date_from_filename(filename: str) -> str | None:
    """Extract YYYY-MM-DD from filename like '2026-02-23.parquet'."""
    match = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
    return match.group(1) if match else None


def normalize_signal_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names and add missing columns."""
    # Normalize column names
    col_map = {}
    for col in df.columns:
        col_lower = col.lower()
        if col_lower in ("weight", "w", "target_weight"):
            col_map[col] = "target_weight"
        elif col_lower == "ticker":
            col_map[col] = "ticker"
        elif col_lower == "score":
            col_map[col] = "score"
        elif col_lower == "rank":
            col_map[col] = "rank"
        elif col_lower == "sleeve":
            col_map[col] = "sleeve"
    
    df = df.rename(columns=col_map)
    
    # Ensure required columns exist
    if "ticker" not in df.columns:
        raise ValueError("signals must have 'ticker' column")
    
    # Add missing optional columns
    for col in ["target_weight", "score", "rank", "sleeve"]:
        if col not in df.columns:
            df[col] = None
    
    # Normalize tickers to uppercase
    df["ticker"] = df["ticker"].str.upper()
    
    return df


def ingest_signals_from_store(
    signals_store_path: Path,
    run_id: str
) -> list[dict[str, Any]]:
    """Scan signals_store directory and extract signal records."""
    if not signals_store_path.exists():
        logger.warning(f"[ALPHA_LAB] signals_store not found: {signals_store_path}")
        return []
    
    signals = []
    
    # Find all CSV and parquet files
    for file_path in sorted(signals_store_path.glob("*.csv")):
        try:
            signal_date = parse_date_from_filename(file_path.name)
            if not signal_date:
                logger.warning(f"[ALPHA_LAB] Could not parse date from {file_path.name}")
                continue
            
            df = pd.read_csv(file_path)
            df = normalize_signal_columns(df)
            
            for _, row in df.iterrows():
                signals.append({
                    "signal_date": signal_date,
                    "ticker": row["ticker"],
                    "target_weight": row.get("target_weight"),
                    "score": row.get("score"),
                    "rank": row.get("rank"),
                    "sleeve": row.get("sleeve"),
                    "source_file": str(file_path.name)
                })
            
            logger.info(f"[ALPHA_LAB] Ingested {len(df)} signals from {file_path.name}")
        
        except Exception as e:
            logger.error(f"[ALPHA_LAB] Error reading {file_path.name}: {e}")
            continue
    
    for file_path in sorted(signals_store_path.glob("*.parquet")):
        try:
            signal_date = parse_date_from_filename(file_path.name)
            if not signal_date:
                logger.warning(f"[ALPHA_LAB] Could not parse date from {file_path.name}")
                continue
            
            df = pd.read_parquet(file_path)
            df = normalize_signal_columns(df)
            
            for _, row in df.iterrows():
                signals.append({
                    "signal_date": signal_date,
                    "ticker": row["ticker"],
                    "target_weight": row.get("target_weight"),
                    "score": row.get("score"),
                    "rank": row.get("rank"),
                    "sleeve": row.get("sleeve"),
                    "source_file": str(file_path.name)
                })
            
            logger.info(f"[ALPHA_LAB] Ingested {len(df)} signals from {file_path.name}")
        
        except Exception as e:
            logger.error(f"[ALPHA_LAB] Error reading {file_path.name}: {e}")
            continue
    
    return signals


def compute_signal_stats(signals: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary statistics from signals."""
    if not signals:
        return {
            "num_files": 0,
            "num_rows": 0,
            "num_dates": 0,
            "date_range": None,
            "tickers": []
        }
    
    df = pd.DataFrame(signals)
    
    # Group by date and compute weight sums
    date_groups = df.groupby("signal_date")
    weight_sums = []
    for date, group in date_groups:
        weight_col = group["target_weight"].dropna()
        if len(weight_col) > 0:
            weight_sums.append(weight_col.sum())
    
    # Top tickers by frequency
    ticker_counts = df["ticker"].value_counts().head(10)
    
    return {
        "num_files": len(df["source_file"].unique()),
        "num_rows": len(df),
        "num_dates": len(df["signal_date"].unique()),
        "date_range": (df["signal_date"].min(), df["signal_date"].max()),
        "tickers": ticker_counts.to_dict(),
        "weight_sums": {
            "min": min(weight_sums) if weight_sums else None,
            "median": sorted(weight_sums)[len(weight_sums)//2] if weight_sums else None,
            "max": max(weight_sums) if weight_sums else None
        }
    }
