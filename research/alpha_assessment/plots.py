from __future__ import annotations

from pathlib import Path

import pandas as pd


def write_nav_preview_csv(df: pd.DataFrame, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "canonical_nav_preview.csv"
    cols = [c for c in ["date", "strategy_nav", "spy_return", "excess_return"] if c in df.columns]
    df[cols].to_csv(path, index=False)
    return path
