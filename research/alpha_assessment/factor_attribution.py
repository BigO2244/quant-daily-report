from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_latest_ticker_contributions(repo_root: Path) -> pd.DataFrame:
    files = sorted((repo_root / "outputs" / "perf").glob("contribution_tickers_*.csv"))
    if not files:
        return pd.DataFrame()
    return pd.read_csv(files[-1])
