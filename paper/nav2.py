from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from paper.ledger2 import ensure_ledger2_exists
from paper.paper_broker import fetch_prev_closes_yfinance


def _load_ledger(path: str) -> pd.DataFrame:
    ensure_ledger2_exists(path)
    df = pd.read_csv(path)
    return df if not df.empty else pd.DataFrame(columns=["ticker", "side", "quantity", "fill_price", "fees", "trade_date"])


def _asof_prices(tickers: list[str], asof_date: str) -> dict[str, float]:
    if not tickers:
        return {}
    px = fetch_prev_closes_yfinance(sorted(set(tickers)), asof_date=asof_date)
    if px.empty:
        return {}
    return {str(r["ticker"]).upper(): float(r["prev_close"]) for _, r in px.iterrows()}


def update_nav_outputs(
    asof_date: str,
    ledger_path: str = "outputs/ledger/trades.csv",
    nav_path_tpl: str = "outputs/perf/nav_{asof}.json",
    nav_timeseries_path: str = "outputs/perf/nav_timeseries.csv",
) -> dict:
    df = _load_ledger(ledger_path)
    if not df.empty:
        df = df[df["trade_date"].astype(str) <= str(asof_date)].copy()

    cash = 0.0
    holdings: dict[str, dict[str, float]] = {}
    if not df.empty:
        for _, row in df.sort_values(["trade_date", "timestamp_et"], na_position="last").iterrows():
            ticker = str(row.get("ticker", "")).upper()
            side = str(row.get("side", "")).upper()
            qty = float(row.get("quantity", 0.0) or 0.0)
            px = float(row.get("fill_price", 0.0) or 0.0)
            fees = float(row.get("fees", 0.0) or 0.0)
            if qty <= 0 or px <= 0:
                continue
            pos = holdings.setdefault(ticker, {"shares": 0.0, "avg_cost": 0.0})
            if side == "BUY":
                new_shares = pos["shares"] + qty
                pos["avg_cost"] = ((pos["shares"] * pos["avg_cost"]) + (qty * px)) / new_shares if new_shares else 0.0
                pos["shares"] = new_shares
                cash -= qty * px + fees
            else:
                sell_qty = min(qty, pos["shares"])
                pos["shares"] = max(0.0, pos["shares"] - sell_qty)
                cash += sell_qty * px - fees

    tickers = [t for t, p in holdings.items() if p["shares"] > 0]
    px_map = _asof_prices(tickers, asof_date)
    missing_prices = [t for t in tickers if t not in px_map]

    positions_value = 0.0
    for ticker in tickers:
        positions_value += holdings[ticker]["shares"] * float(px_map.get(ticker, holdings[ticker]["avg_cost"]))

    equity = cash + positions_value
    nav_payload = {"date": asof_date, "equity": equity, "cash": cash, "positions_value": positions_value}

    nav_path = Path(nav_path_tpl.format(asof=asof_date))
    nav_path.parent.mkdir(parents=True, exist_ok=True)
    nav_path.write_text(json.dumps(nav_payload, indent=2) + "\n", encoding="utf-8")

    ts_path = Path(nav_timeseries_path)
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    if ts_path.exists() and ts_path.stat().st_size > 0:
        ts = pd.read_csv(ts_path)
    else:
        ts = pd.DataFrame(columns=["date", "equity", "cash", "positions_value", "return_1d"])

    ts = ts[ts["date"].astype(str) != str(asof_date)].copy() if not ts.empty else ts
    new_row = pd.DataFrame([{"date": asof_date, "equity": equity, "cash": cash, "positions_value": positions_value, "return_1d": 0.0}])
    ts = new_row if ts.empty else pd.concat([ts, new_row], ignore_index=True)
    ts["date"] = pd.to_datetime(ts["date"])
    ts = ts.sort_values("date").reset_index(drop=True)
    ts["return_1d"] = ts["equity"].pct_change().fillna(0.0)
    ts["date"] = ts["date"].dt.strftime("%Y-%m-%d")
    ts.to_csv(ts_path, index=False)

    return {
        "nav_path": str(nav_path),
        "nav_timeseries_path": str(ts_path),
        "equity": float(equity),
        "missing_prices": sorted(set(missing_prices)),
    }
