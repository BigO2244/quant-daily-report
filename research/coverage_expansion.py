"""
Daily Performance Persistence Improvements v1

Ensures strategy_nav, strategy_return, and related fields are written consistently.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import logging

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def ensure_nav_timeseries_persistence(
    trade_date: str,
    asof_date: str,
    nav_dict: dict[str, Any] | None = None,
    nav_timeseries_path: str | Path = "outputs/perf/nav_timeseries.csv",
) -> tuple[pd.DataFrame, int]:
    """
    Ensure nav_timeseries.csv has a row for today, with safe backfill from signals.
    
    Args:
        trade_date: Trade date (post-market) in YYYY-MM-DD
        asof_date: As-of date (pre-market) in YYYY-MM-DD, used for nav computation
        nav_dict: Dictionary with keys: equity, cash, gross_exposure, net_exposure, turnover
        nav_timeseries_path: Path to nav_timeseries.csv
    
    Returns:
        (updated_df, rows_written) tuple
        
    Strategy:
    1. If nav_dict provided with equity, append row
    2. Else, try to backfill from signals/YYYY-MM-DD.json if available
    3. If neither, create minimal row with nulls and warning
    """
    nav_timeseries_path = Path(nav_timeseries_path)
    nav_timeseries_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Load existing
    if nav_timeseries_path.exists() and nav_timeseries_path.stat().st_size > 0:
        df = pd.read_csv(nav_timeseries_path)
    else:
        df = pd.DataFrame(columns=[
            "date", "equity", "cash", "gross_exposure", "net_exposure",
            "return_1d", "turnover_dollars", "turnover_pct", "turnover"
        ])
    
    # Normalize date column
    if not df.empty and "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    
    # Check if row for asof_date already exists
    already_exists = not df.empty and (df["date"].astype(str) == asof_date).any()
    if already_exists:
        logger.info("[NAV_PERSIST] Row already exists for %s; skipping append", asof_date)
        return df, 0
    
    # Build new row
    new_row_dict = {
        "date": asof_date,
        "equity": None,
        "cash": None,
        "gross_exposure": None,
        "net_exposure": None,
        "return_1d": None,
        "turnover_dollars": None,
        "turnover_pct": None,
        "turnover": None,
    }
    
    # Attempt 1: Use provided nav_dict
    if nav_dict and nav_dict.get("equity"):
        new_row_dict["equity"] = float(nav_dict.get("equity", 0.0))
        new_row_dict["cash"] = float(nav_dict.get("cash", 0.0))
        new_row_dict["gross_exposure"] = float(nav_dict.get("gross_exposure", 0.0))
        new_row_dict["net_exposure"] = float(nav_dict.get("net_exposure", 0.0))
        source = "nav_dict"
    # Attempt 2: Backfill from daily signal snapshot
    elif _try_backfill_from_signals(new_row_dict, asof_date):
        source = "signals_backfill"
    else:
        source = "minimal_null"
        logger.warning("[NAV_PERSIST] No nav data available for %s; creating null row", asof_date)
    
    # Append to DataFrame
    new_df = pd.DataFrame([new_row_dict])
    if df.empty:
        df = new_df
    else:
        df = pd.concat([df, new_df], ignore_index=True)
    
    # Deduplicate and sort
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    
    # Compute returns
    for i in range(len(df)):
        equity = pd.to_numeric(df.loc[i, "equity"], errors="coerce")
        if i == 0 or pd.isna(equity):
            continue
        prev_equity = pd.to_numeric(df.loc[i - 1, "equity"], errors="coerce")
        if pd.notna(prev_equity) and prev_equity != 0:
            df.loc[i, "return_1d"] = float(equity / prev_equity - 1.0)
    
    # Write
    df.to_csv(nav_timeseries_path, index=False)
    logger.info("[NAV_PERSIST] Wrote nav_timeseries.csv rows=%d asof=%s source=%s", len(df), asof_date, source)
    
    return df, 1


def _try_backfill_from_signals(row_dict: dict[str, Any], asof_date: str) -> bool:
    """
    Attempt to fill nav fields from signals/YYYY-MM-DD.json.
    
    Returns:
        True if backfill succeeded, False otherwise
    """
    signal_path = Path(f"signals/{asof_date}.json")
    if not signal_path.exists():
        return False
    
    try:
        with open(signal_path) as f:
            sig = json.load(f)
        
        # Extract breaker exposure as proxy for gross/net exposure
        breaker = sig.get("breaker") or {}
        cash_weight_from_signal = sig.get("cash_target_weight")
        invested = breaker.get("invested_after_overlay")
        
        if cash_weight_from_signal:
            row_dict["net_exposure"] = float(1.0 - cash_weight_from_signal)
            row_dict["gross_exposure"] = float(1.0 - cash_weight_from_signal)
        elif invested is not None:
            row_dict["net_exposure"] = float(invested)
            row_dict["gross_exposure"] = float(invested)
        
        logger.debug("[NAV_PERSIST] Backfilled exposure from signals/%s.json", asof_date)
        return True
    except Exception as e:
        logger.debug("[NAV_PERSIST] Backfill from signals failed: %s", e)
        return False


def ensure_spy_vix_alignment(
    canonical_df: pd.DataFrame,
    benchmark_path: str | Path = "outputs/perf/benchmark_close_history.csv",
    vix_path: str | Path = "outputs/perf/vix_history.csv",
) -> pd.DataFrame:
    """
    Left-join benchmark and VIX onto canonical dates; forward-fill where missing.
    
    Args:
        canonical_df: Canonical performance DataFrame (must have "date" column)
        benchmark_path: Path to benchmark_close_history.csv
        vix_path: Path to vix_history.csv
    
    Returns:
        Updated canonical_df with spy_close, spy_return, vix_close, vix_regime forward-filled
    """
    out = canonical_df.copy()
    
    # Load benchmark
    if Path(benchmark_path).exists():
        bench = pd.read_csv(benchmark_path)
        bench["date"] = pd.to_datetime(bench["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        bench_subset = bench[["date", "spy_close", "spy_return"]].copy()
        out = out.merge(bench_subset, on="date", how="left", suffixes=("", "_bench"))
        for col in ["spy_close", "spy_return"]:
            if col in out.columns:
                if f"{col}_bench" in out.columns:
                    out[col] = out[col].fillna(out[f"{col}_bench"])
                    out = out.drop(columns=[f"{col}_bench"])
        # Forward-fill SPY data (stable across trading days)
        out["spy_close"] = out["spy_close"].fillna(method="ffill")
        out["spy_return"] = out["spy_return"].fillna(method="ffill")
    
    # Load VIX
    if Path(vix_path).exists():
        vix = pd.read_csv(vix_path)
        vix["date"] = pd.to_datetime(vix["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        vix_subset = vix[["date", "vix_close", "vix_regime"]].copy()
        out = out.merge(vix_subset, on="date", how="left", suffixes=("", "_vix"))
        for col in ["vix_close", "vix_regime"]:
            if col in out.columns:
                if f"{col}_vix" in out.columns:
                    out[col] = out[col].fillna(out[f"{col}_vix"])
                    out = out.drop(columns=[f"{col}_vix"])
        # Forward-fill VIX data
        out["vix_close"] = out["vix_close"].fillna(method="ffill")
        out["vix_regime"] = out["vix_regime"].fillna(method="ffill")
    
    return out
