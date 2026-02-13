"""Daily contribution attribution from start-of-day holdings weights."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from paper.paper_broker import fetch_prev_closes_yfinance

logger = logging.getLogger(__name__)


def compute_daily_attribution(asof_date: str, prev_date: str) -> dict[str, pd.DataFrame]:
    prev_holdings_path = Path(f"outputs/perf/holdings_mtm_{prev_date}.csv")
    prev_nav_path = Path(f"outputs/perf/nav_{prev_date}.json")
    if not prev_holdings_path.exists() or not prev_nav_path.exists():
        return {"tickers": pd.DataFrame(), "sleeves": pd.DataFrame()}

    holdings = pd.read_csv(prev_holdings_path)
    nav_prev = pd.read_json(prev_nav_path, typ="series")
    equity_start = float(nav_prev.get("equity", 0.0))
    if holdings.empty or equity_start <= 0:
        return {"tickers": pd.DataFrame(), "sleeves": pd.DataFrame()}

    tickers = holdings["ticker"].astype(str).str.upper().tolist()
    prev_px = fetch_prev_closes_yfinance(tickers, asof_date=prev_date)
    curr_px = fetch_prev_closes_yfinance(tickers, asof_date=asof_date)
    prev_map = {str(r["ticker"]).upper(): float(r["prev_close"]) for _, r in prev_px.iterrows()} if not prev_px.empty else {}
    curr_map = {str(r["ticker"]).upper(): float(r["prev_close"]) for _, r in curr_px.iterrows()} if not curr_px.empty else {}

    rows = []
    for _, row in holdings.iterrows():
        t = str(row["ticker"]).upper()
        mv_start = float(row["market_value"])
        weight = mv_start / equity_start if equity_start else 0.0
        p0 = prev_map.get(t)
        p1 = curr_map.get(t)
        ret = (p1 / p0 - 1.0) if (p0 and p1) else 0.0
        contrib = weight * ret
        rows.append({"ticker": t, "weight_start": weight, "return": ret, "contribution": contrib, "sleeve": row.get("sleeve", "")})

    tickers_df = pd.DataFrame(rows)
    sleeves_df = pd.DataFrame(columns=["sleeve", "weight_start", "sleeve_return", "contribution"])
    if not tickers_df.empty:
        grp = tickers_df.groupby("sleeve", as_index=False)
        sleeves_df = grp.apply(
            lambda g: pd.Series(
                {
                    "weight_start": g["weight_start"].sum(),
                    "sleeve_return": (g["contribution"].sum() / g["weight_start"].sum()) if g["weight_start"].sum() else 0.0,
                    "contribution": g["contribution"].sum(),
                }
            ),
            include_groups=False,
        ).reset_index()
    return {"tickers": tickers_df, "sleeves": sleeves_df}


def write_attribution_outputs(asof_date: str, tickers_df: pd.DataFrame, sleeves_df: pd.DataFrame) -> dict[str, str]:
    out = Path("outputs/perf")
    out.mkdir(parents=True, exist_ok=True)
    t_path = out / f"contribution_tickers_{asof_date}.csv"
    s_path = out / f"contribution_sleeves_{asof_date}.csv"
    tickers_df.to_csv(t_path, index=False)
    sleeves_df.to_csv(s_path, index=False)
    logger.info("[ATTRIBUTION] wrote artifacts for %s", asof_date)
    return {"tickers": str(t_path), "sleeves": str(s_path)}
