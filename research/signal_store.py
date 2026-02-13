"""Persist daily signal snapshots for research diagnostics."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

SIGNAL_COLS = [
    "date",
    "ticker",
    "momentum_score",
    "value_score",
    "trend_score",
    "quality_score",
    "final_target_weight",
    "sleeve_source",
    "price",
]


def persist_signal_snapshot(df: pd.DataFrame, asof_date: str) -> str:
    out_dir = Path("signals_store")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = df.copy() if df is not None else pd.DataFrame()
    if out.empty:
        out = pd.DataFrame(columns=SIGNAL_COLS)
    for col in SIGNAL_COLS:
        if col not in out.columns:
            out[col] = pd.NA
    out["date"] = asof_date
    out = out[SIGNAL_COLS]

    parquet_path = out_dir / f"{asof_date}.parquet"
    try:
        out.to_parquet(parquet_path, index=False)
        logger.info("[RESEARCH] wrote signal snapshot parquet: %s", parquet_path)
        return str(parquet_path)
    except Exception:
        csv_path = out_dir / f"{asof_date}.csv"
        out.to_csv(csv_path, index=False)
        logger.info("[RESEARCH] wrote signal snapshot csv: %s", csv_path)
        return str(csv_path)
