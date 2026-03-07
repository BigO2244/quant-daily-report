from __future__ import annotations

from typing import Any

import pandas as pd


def summarize_performance(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"rows": 0, "total_return": None, "max_drawdown": None}
    out = df.copy().sort_values("date")
    nav = pd.to_numeric(out.get("strategy_nav"), errors="coerce")
    returns = pd.to_numeric(out.get("strategy_return"), errors="coerce")
    total_return = None
    if nav.notna().sum() >= 2:
        first = float(nav.dropna().iloc[0])
        last = float(nav.dropna().iloc[-1])
        total_return = (last / first) - 1.0 if first != 0 else None
    dd = None
    if nav.notna().sum() >= 2:
        peak = nav.cummax()
        drawdown = (nav / peak) - 1.0
        dd = float(drawdown.min())
    return {
        "rows": int(len(out)),
        "total_return": total_return,
        "max_drawdown": dd,
        "avg_daily_return": float(returns.dropna().mean()) if returns.notna().any() else None,
    }
