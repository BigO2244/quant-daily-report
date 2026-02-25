"""Mark holdings to market and maintain NAV timeseries artifacts."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from paper.paper_broker import fetch_prev_closes_yfinance
from paper.reporting_consistency import compute_nav

logger = logging.getLogger(__name__)


def mark_holdings(holdings: pd.DataFrame, cash: float, asof_date: str) -> tuple[pd.DataFrame, dict]:
    tickers = holdings["ticker"].astype(str).str.upper().tolist() if not holdings.empty else []
    px_df = fetch_prev_closes_yfinance(tickers, asof_date=asof_date)
    px_map = {str(r["ticker"]).upper(): float(r["prev_close"]) for _, r in px_df.iterrows()} if not px_df.empty else {}

    mtm, nav = compute_nav(ledger=holdings, prices=px_map, cash=float(cash))
    nav = {
        "date": asof_date,
        **nav,
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
    cols = [
        "date",
        "equity",
        "cash",
        "gross_exposure",
        "net_exposure",
        "return_1d",
        "turnover_dollars",
        "turnover_pct",
        "turnover",
    ]
    ts = pd.read_csv(out_path) if out_path.exists() and out_path.stat().st_size > 0 else pd.DataFrame(columns=cols)
    ts = ts.reindex(columns=cols)

    ts = ts[ts["date"].astype(str) != str(asof_date)].copy() if not ts.empty else ts
    new_row = pd.DataFrame(
        [
            {
                "date": asof_date,
                "equity": float(nav.get("equity", 0.0)),
                "cash": float(nav.get("cash", 0.0)),
                "gross_exposure": float(nav.get("gross_exposure", 0.0)),
                "net_exposure": float(nav.get("net_exposure", 0.0)),
                "return_1d": np.nan,
                "turnover_dollars": np.nan,
                "turnover_pct": np.nan,
                "turnover": np.nan,
            }
        ]
    )
    if ts.empty:
        ts = new_row
    else:
        ts = pd.concat([ts, new_row], ignore_index=True)
    ts["date"] = pd.to_datetime(ts["date"])
    ts = ts.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)

    def _trade_notional_for_date(trade_date: str) -> float:
        if ledger.empty or "trade_date" not in ledger.columns or "notional" not in ledger.columns:
            return 0.0
        day = ledger.loc[ledger["trade_date"].astype(str) == str(trade_date)].copy()
        if day.empty:
            return 0.0
        if all(col in day.columns for col in ("trade_date", "order_id")):
            before = len(day)
            sort_cols = ["trade_date"]
            if "timestamp_et" in day.columns:
                sort_cols.append("timestamp_et")
            day = (
                day.sort_values(sort_cols, na_position="last")
                .drop_duplicates(subset=["trade_date", "order_id"], keep="last")
                .reset_index(drop=True)
            )
            if len(day) != before:
                logger.info(
                    "[PERF] deduped turnover ledger rows removed=%d trade_date=%s",
                    before - len(day),
                    trade_date,
                )
        day = day[["notional"]].copy()
        day["notional"] = pd.to_numeric(day["notional"], errors="coerce").fillna(0.0)
        return float(day["notional"].abs().sum())

    for i in range(len(ts)):
        if i == 0:
            ts.loc[i, "return_1d"] = 0.0
            ts.loc[i, "turnover_dollars"] = 0.0
            ts.loc[i, "turnover_pct"] = 0.0
            ts.loc[i, "turnover"] = 0.0
            continue
        prev_eq = float(ts.loc[i - 1, "equity"])
        curr_eq = float(ts.loc[i, "equity"])
        ts.loc[i, "return_1d"] = (curr_eq / prev_eq - 1.0) if prev_eq else 0.0
        # Turnover convention: notional traded on trade_date == this row's date divided by prior equity.
        d = ts.loc[i, "date"].strftime("%Y-%m-%d")
        notional = _trade_notional_for_date(d)
        turnover_pct = (notional / prev_eq) if prev_eq else 0.0
        ts.loc[i, "turnover_dollars"] = notional
        ts.loc[i, "turnover_pct"] = turnover_pct
        ts.loc[i, "turnover"] = turnover_pct

    # Populate first-row turnover from same-day notional / row equity baseline.
    if len(ts) > 0:
        d0 = ts.loc[0, "date"].strftime("%Y-%m-%d")
        eq0 = float(ts.loc[0, "equity"])
        n0 = _trade_notional_for_date(d0)
        pct0 = (n0 / eq0) if eq0 else 0.0
        ts.loc[0, "turnover_dollars"] = n0
        ts.loc[0, "turnover_pct"] = pct0
        ts.loc[0, "turnover"] = pct0

    ts["date"] = ts["date"].dt.strftime("%Y-%m-%d")
    ts.to_csv(out_path, index=False)
    logger.info("[PERF] updated nav_timeseries: %s", out_path)
    return str(out_path)
