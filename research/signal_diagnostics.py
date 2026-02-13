"""Forward-return diagnostics by signal decile."""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def _diag_for_signal(df: pd.DataFrame, signal_col: str) -> pd.DataFrame:
    work = df[["date", "ticker", signal_col, "price"]].copy().dropna()
    work = work.sort_values(["ticker", "date"])
    for h in (5, 21, 63):
        work[f"fwd_{h}d"] = work.groupby("ticker")["price"].shift(-h) / work["price"] - 1.0
    work["decile"] = work.groupby("date")[signal_col].transform(lambda s: pd.qcut(s.rank(method="first"), 10, labels=False, duplicates="drop"))
    out = work.groupby("decile", as_index=False)[["fwd_5d", "fwd_21d", "fwd_63d"]].mean()
    return out


def run(signal_store_path: str) -> tuple[str, str]:
    p = Path(signal_store_path)
    df = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)
    out_dir = Path("outputs/research")
    out_dir.mkdir(parents=True, exist_ok=True)
    mom_path = out_dir / "signal_diag_momentum.csv"
    val_path = out_dir / "signal_diag_value.csv"
    _diag_for_signal(df, "momentum_score").to_csv(mom_path, index=False)
    _diag_for_signal(df, "value_score").to_csv(val_path, index=False)
    return str(mom_path), str(val_path)
