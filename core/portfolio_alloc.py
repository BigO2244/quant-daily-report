# core/portfolio_alloc.py
from __future__ import annotations

import pandas as pd

DEFAULT_PORTFOLIO_BASE_EQUITY = 10_000.0

DEFAULT_SLEEVE_WEIGHTS = {
    "sleeve_1": 0.80,  # Momentum
    "sleeve_2": 0.20,  # Valuation
}

def scale_sleeve_daily(
    daily_df: pd.DataFrame,
    sleeve_name: str,
    base_equity: float = DEFAULT_PORTFOLIO_BASE_EQUITY,
    weights: dict[str, float] = DEFAULT_SLEEVE_WEIGHTS,
    equity_col: str = "equity",
) -> pd.DataFrame:
    """
    Scale a sleeve's daily equity series to its allocated capital.

    Assumptions:
      - daily_df[equity_col] is the sleeve's own equity curve (usually starting from ~ACCOUNT_EQUITY).
      - Scaling is linear by return: alloc_equity[t] = alloc_capital * (sleeve_equity[t] / sleeve_equity[0]).
    """
    if daily_df.empty:
        return daily_df.copy()

    w = float(weights.get(sleeve_name, 0.0))
    alloc_capital = base_equity * w

    df = daily_df.copy()
    start_equity = float(df[equity_col].iloc[0])

    if start_equity <= 0:
        raise ValueError(f"{sleeve_name}: start equity must be > 0, got {start_equity}")

    df["alloc_weight"] = w
    df["alloc_capital"] = alloc_capital
    df["alloc_equity"] = alloc_capital * (df[equity_col] / start_equity)

    # derived daily PnL/returns on the allocated sleeve
    df["alloc_day_pnl"] = df["alloc_equity"].diff().fillna(0.0)
    df["alloc_day_return"] = df["alloc_equity"].pct_change().fillna(0.0)

    return df


def combine_portfolio_daily(
    sleeve_daily_scaled: dict[str, pd.DataFrame],
    date_col: str = "date",
) -> pd.DataFrame:
    """
    Combine scaled sleeve frames into a single portfolio daily series by date.
    Requires each df has: date, alloc_equity, alloc_day_pnl, alloc_day_return
    """
    frames = []
    for name, df in sleeve_daily_scaled.items():
        if df.empty:
            continue
        tmp = df[[date_col, "alloc_equity", "alloc_day_pnl"]].copy()
        tmp = tmp.rename(
            columns={
                "alloc_equity": f"{name}_alloc_equity",
                "alloc_day_pnl": f"{name}_alloc_day_pnl",
            }
        )
        frames.append(tmp)

    if not frames:
        return pd.DataFrame(columns=[date_col, "portfolio_equity", "portfolio_day_pnl", "portfolio_day_return"])

    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on=date_col, how="outer")

    out = out.sort_values(date_col).reset_index(drop=True)
    equity_cols = [c for c in out.columns if c.endswith("_alloc_equity")]
    pnl_cols = [c for c in out.columns if c.endswith("_alloc_day_pnl")]

    out["portfolio_equity"] = out[equity_cols].sum(axis=1, skipna=True)
    out["portfolio_day_pnl"] = out[pnl_cols].sum(axis=1, skipna=True)
    out["portfolio_day_return"] = out["portfolio_equity"].pct_change().fillna(0.0)

    return out
