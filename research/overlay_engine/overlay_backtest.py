from __future__ import annotations

import pandas as pd

from research.overlay_engine.overlay_strategies import apply_overlay_to_returns


def run_overlay_backtest(canonical_df: pd.DataFrame, *, enforce_lag: bool = True) -> pd.DataFrame:
    if canonical_df.empty:
        return canonical_df.copy()

    df = canonical_df.copy().sort_values("date").reset_index(drop=True)
    df["strategy_return"] = pd.to_numeric(df.get("strategy_return"), errors="coerce")
    df["overlay_multiplier"] = pd.to_numeric(df.get("overlay_multiplier"), errors="coerce").fillna(1.0)

    if enforce_lag:
        # Lag by one bar so trade-date return does not consume same-day overlay signal.
        df["overlay_multiplier_lagged"] = df["overlay_multiplier"].shift(1)
        df.loc[df.index[0], "overlay_multiplier_lagged"] = 1.0
    else:
        df["overlay_multiplier_lagged"] = df["overlay_multiplier"]

    df["overlay_return"] = apply_overlay_to_returns(df["strategy_return"], df["overlay_multiplier_lagged"])

    base_nav = pd.to_numeric(df.get("strategy_nav"), errors="coerce")
    if base_nav.notna().any():
        start_nav = float(base_nav.dropna().iloc[0])
    else:
        start_nav = 10000.0
    nav_vals = [start_nav]
    for value in df["overlay_return"].iloc[1:]:
        nav_vals.append(nav_vals[-1] * (1.0 + float(value)))
    df["overlay_nav"] = nav_vals
    return df
