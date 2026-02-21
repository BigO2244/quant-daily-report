# paper/signals_io.py
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

import pandas as pd


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def normalize_weights(
    df: pd.DataFrame, weight_col: str = "target_weight"
) -> pd.DataFrame:
    out = df.copy()
    out[weight_col] = out[weight_col].astype(float)
    s = float(out[weight_col].sum())
    if s <= 0:
        raise ValueError(f"Sum of {weight_col} is <= 0; cannot normalize.")
    out[weight_col] = out[weight_col] / s
    return out


def write_signals_snapshot(
    df_targets: pd.DataFrame,
    run_date: str,
    asof_date: str | None = None,
    out_dir: str = "signals",
    cash_target_weight: Optional[float] = None,
    sleeve_col: Optional[str] = "sleeve",
    ticker_col: str = "ticker",
    weight_col: str = "target_weight",
    model_version: str | None = None,
    extra: dict | None = None,
) -> str:
    """
    Writes signals/YYYY-MM-DD.json

    Expected df_targets columns:
      - ticker
      - target_weight
      - optional sleeve

    Returns the filepath written.
    """
    required = {ticker_col, weight_col}
    missing = [c for c in required if c not in df_targets.columns]
    if missing:
        raise ValueError(
            f"df_targets missing required columns: {missing}. Have: {list(df_targets.columns)}"
        )

    df = df_targets[
        [
            c
            for c in [ticker_col, weight_col, sleeve_col]
            if c and c in df_targets.columns
        ]
    ].copy()

    if sleeve_col is None or sleeve_col not in df.columns:
        df["sleeve"] = "core"
        sleeve_col = "sleeve"

    # Clean
    df[ticker_col] = df[ticker_col].astype(str).str.upper().str.strip()
    df = df[df[ticker_col].str.len() > 0].copy()

    # If all target weights are zero/non-positive (e.g., breaker lock), emit CASH-only.
    weight_sum = float(pd.to_numeric(df.get(weight_col), errors="coerce").fillna(0.0).sum())
    if weight_sum <= 0.0:
        cash_row = {ticker_col: "CASH", weight_col: 1.0}
        if sleeve_col:
            cash_row[sleeve_col] = "core"
        df = pd.DataFrame([cash_row])

    # Normalize weights to sum to 1
    df = normalize_weights(df, weight_col=weight_col)

    # Ensure directory + write
    ensure_dir(out_dir)
    out_path = os.path.join(out_dir, f"{run_date}.json")

    signals = df.rename(
        columns={
            ticker_col: "ticker",
            weight_col: "target_weight",
            sleeve_col: "sleeve",
        }
    )[["ticker", "target_weight", "sleeve"]].to_dict(orient="records")

    payload = {
        "snapshot_date": run_date,
        "meta": {
            "trade_date": run_date,
            "asof_date": asof_date or run_date,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "signals": signals,
    }
    if model_version:
        payload["meta"]["model_version"] = model_version
    if cash_target_weight is not None:
        payload["cash_target_weight"] = float(cash_target_weight)
    if extra:
        payload.update(extra)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")

    return out_path
