# paper/paper_report.py
from __future__ import annotations

import os
import pandas as pd


def _read_csv_if_exists(path: str) -> pd.DataFrame:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return pd.DataFrame()
    return pd.read_csv(path)


def build_paper_report_html(run_date: str, ledger_path: str, trades_path: str, benchmark_ticker: str = "SPY") -> str:
    led = _read_csv_if_exists(ledger_path)
    day = led[led["date"] == run_date].copy() if not led.empty else pd.DataFrame()

    equity = float(day["total_equity"].iloc[0]) if not day.empty else 0.0
    cash = float(day["cash"].iloc[0]) if not day.empty else 0.0
    invested = float(day["market_value"].sum()) if not day.empty else 0.0
    exposure = invested / equity if equity > 0 else 0.0

    trades = _read_csv_if_exists(trades_path)
    trades = trades[trades["date"] == run_date].copy() if not trades.empty else pd.DataFrame()

    if not trades.empty:
        trades_view = trades[["ticker", "side", "shares", "price", "slippage_cost", "notional", "reason"]].copy()
    else:
        trades_view = pd.DataFrame(columns=["ticker", "side", "shares", "price", "slippage_cost", "notional", "reason"])

    holdings = (
        day.sort_values("market_value", ascending=False)[["ticker", "sleeve", "shares", "price", "market_value"]]
        .head(15)
        if not day.empty
        else pd.DataFrame(columns=["ticker", "sleeve", "shares", "price", "market_value"])
    )

    def df_to_html(df: pd.DataFrame) -> str:
        if df is None or df.empty:
            return "<p><em>None</em></p>"
        return df.to_html(index=False, border=0)

    return f"""
<div style="font-family: Arial, sans-serif;">
  <h2>Paper Trading Execution — {run_date}</h2>
  <ul>
    <li><b>Total Equity:</b> ${equity:,.2f}</li>
    <li><b>Cash:</b> ${cash:,.2f}</li>
    <li><b>Invested:</b> ${invested:,.2f}</li>
    <li><b>Exposure:</b> {exposure:.1%}</li>
    <li><b>Benchmark:</b> {benchmark_ticker}</li>
  </ul>

  <h3>Trades Executed</h3>
  {df_to_html(trades_view)}

  <h3>Holdings (Top 15)</h3>
  {df_to_html(holdings)}

  <p style="color:#666; margin-top:16px;">
    Execution model: next-open fills with slippage + cash buffer. Ledger is append-only.
  </p>
</div>
""".strip()
