"""Mark holdings to market and maintain NAV timeseries artifacts."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from paper.paper_broker import fetch_prev_closes_yfinance

logger = logging.getLogger(__name__)


def mark_holdings(holdings: pd.DataFrame, cash: float, asof_date: str) -> tuple[pd.DataFrame, dict]:
    tickers = holdings["ticker"].astype(str).str.upper().tolist() if not holdings.empty else []
    px_df = fetch_prev_closes_yfinance(tickers, asof_date=asof_date)
    px_map = {str(r["ticker"]).upper(): float(r["prev_close"]) for _, r in px_df.iterrows()} if not px_df.empty else {}

    rows = []
    for _, r in holdings.iterrows():
        tkr = str(r["ticker"]).upper()
        shares = float(r["shares"])
        avg_cost = float(r["avg_cost"])
        px = px_map.get(tkr)
        if px is None:
            raise ValueError(f"Missing asof close for {tkr} on {asof_date}")
        mv = shares * px
        upnl = (px - avg_cost) * shares
        rows.append({"ticker": tkr, "shares": shares, "price": px, "market_value": mv, "avg_cost": avg_cost, "unrealized_pnl": upnl, "sleeve": r.get("sleeve", "")})

    mtm = pd.DataFrame(rows)
    equity = float(cash + (mtm["market_value"].sum() if not mtm.empty else 0.0))
    gross = float(mtm["market_value"].abs().sum() / equity) if equity else 0.0
    net = float(mtm["market_value"].sum() / equity) if equity else 0.0
    nav = {
        "date": asof_date,
        "equity": equity,
        "cash": float(cash),
        "gross_exposure": gross,
        "net_exposure": net,
        "totals": {
            "market_value": float(mtm["market_value"].sum() if not mtm.empty else 0.0),
            "unrealized_pnl": float(mtm["unrealized_pnl"].sum() if not mtm.empty else 0.0),
        },
    }
    return mtm, nav


def write_perf_outputs(mtm: pd.DataFrame, nav: dict, asof_date: str) -> dict[str, str]:
    out_dir = Path("outputs/perf")
    out_dir.mkdir(parents=True, exist_ok=True)
    mtm_path = out_dir / f"holdings_mtm_{asof_date}.csv"
    nav_path = out_dir / f"nav_{asof_date}.json"
    mtm.to_csv(mtm_path, index=False)
    nav_path.write_text(json.dumps(nav, indent=2) + "\n", encoding="utf-8")
    return {"holdings_mtm": str(mtm_path), "nav": str(nav_path)}


def update_nav_timeseries(asof_date: str, nav: dict, ledger: pd.DataFrame) -> str:
    out_path = Path("outputs/perf/nav_timeseries.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["date", "equity", "cash", "gross_exposure", "net_exposure", "return_1d", "turnover"]
    ts = pd.read_csv(out_path) if out_path.exists() and out_path.stat().st_size > 0 else pd.DataFrame(columns=cols)

    ts = ts[ts["date"].astype(str) != str(asof_date)].copy() if not ts.empty else ts
    ts = pd.concat([ts, pd.DataFrame([{
        "date": asof_date,
        "equity": float(nav["equity"]),
        "cash": float(nav["cash"]),
        "gross_exposure": float(nav["gross_exposure"]),
        "net_exposure": float(nav["net_exposure"]),
        "return_1d": np.nan,
        "turnover": np.nan,
    }])], ignore_index=True)
    ts["date"] = pd.to_datetime(ts["date"])
    ts = ts.sort_values("date").reset_index(drop=True)

    for i in range(len(ts)):
        if i == 0:
            ts.loc[i, "return_1d"] = 0.0
            ts.loc[i, "turnover"] = 0.0
            continue
        prev_eq = float(ts.loc[i - 1, "equity"])
        curr_eq = float(ts.loc[i, "equity"])
        ts.loc[i, "return_1d"] = (curr_eq / prev_eq - 1.0) if prev_eq else 0.0
        # Turnover convention: notional traded on trade_date == this row's date divided by prior equity.
        d = ts.loc[i, "date"].strftime("%Y-%m-%d")
        notional = float(ledger.loc[ledger["trade_date"].astype(str) == d, "notional"].sum()) if (not ledger.empty and "trade_date" in ledger.columns) else 0.0
        ts.loc[i, "turnover"] = (notional / prev_eq) if prev_eq else 0.0

    ts["date"] = ts["date"].dt.strftime("%Y-%m-%d")
    ts.to_csv(out_path, index=False)
    logger.info("[PERF] updated nav_timeseries: %s", out_path)
    return str(out_path)
