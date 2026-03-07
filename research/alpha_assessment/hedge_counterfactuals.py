from __future__ import annotations

import pandas as pd


def estimate_no_overlay_nav(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy().sort_values("date")
    strategy_return = pd.to_numeric(out.get("strategy_return"), errors="coerce").fillna(0.0)
    exposure = pd.to_numeric(out.get("gross_exposure"), errors="coerce")
    exposure = exposure.where(exposure.notna(), 1.0)
    adjusted = strategy_return.where(exposure == 0, strategy_return / exposure.clip(lower=1e-6))
    start_nav = float(out["strategy_nav"].dropna().iloc[0]) if out["strategy_nav"].notna().any() else 10000.0
    nav_vals = [start_nav]
    for r in adjusted.iloc[1:]:
        nav_vals.append(nav_vals[-1] * (1.0 + float(r)))
    out["counterfactual_nav_no_overlay"] = pd.Series(nav_vals, index=out.index)
    return out
