# paper/paper_report.py
from __future__ import annotations

import os
import pandas as pd


def _read_csv_if_exists(path: str) -> pd.DataFrame:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return pd.DataFrame()
    return pd.read_csv(path)


def build_paper_report_html(
    run_date: str,
    ledger_path: str,
    trades_path: str,
    benchmark_ticker: str = "SPY",
    reconciliation: dict | None = None,
    shadow_status: dict | None = None,
) -> str:
    led = _read_csv_if_exists(ledger_path)
    day = led[led["date"] == run_date].copy() if not led.empty else pd.DataFrame()

    equity = float(day["total_equity"].iloc[0]) if not day.empty else 0.0
    cash = float(day["cash"].iloc[0]) if not day.empty else 0.0
    invested = float(day["market_value"].sum()) if not day.empty else 0.0
    exposure = invested / equity if equity > 0 else 0.0

    trades = _read_csv_if_exists(trades_path)
    trades = (
        trades[trades["date"] == run_date].copy()
        if not trades.empty
        else pd.DataFrame()
    )

    if not trades.empty:
        trades_view = trades[
            ["ticker", "side", "shares", "price", "slippage_cost", "notional", "reason"]
        ].copy()
    else:
        trades_view = pd.DataFrame(
            columns=[
                "ticker",
                "side",
                "shares",
                "price",
                "slippage_cost",
                "notional",
                "reason",
            ]
        )

    holdings = (
        day.sort_values("market_value", ascending=False)[
            ["ticker", "sleeve", "shares", "price", "market_value"]
        ].head(15)
        if not day.empty
        else pd.DataFrame(
            columns=["ticker", "sleeve", "shares", "price", "market_value"]
        )
    )

    def df_to_html(df: pd.DataFrame) -> str:
        if df is None or df.empty:
            return "<p><em>None</em></p>"
        return df.to_html(index=False, border=0)

    recon_html = ""
    validation_html = ""
    shadow_html = ""
    if reconciliation:
        rows = reconciliation.get("position_reconciliation", []) or []
        recon_df = pd.DataFrame(rows)
        if not recon_df.empty:
            recon_df = recon_df[["ticker", "target_weight", "achieved_weight", "delta_weight", "cash_limited"]].copy()
            recon_df["target_weight"] = (100.0 * recon_df["target_weight"]).map(lambda x: f"{x:.2f}%")
            recon_df["achieved_weight"] = (100.0 * recon_df["achieved_weight"]).map(lambda x: f"{x:.2f}%")
            recon_df["delta_weight"] = (100.0 * recon_df["delta_weight"]).map(lambda x: f"{x:+.2f}%")
            recon_df["cash_limited"] = recon_df["cash_limited"].map(lambda x: "YES" if bool(x) else "")
        else:
            recon_df = pd.DataFrame(columns=["ticker", "target_weight", "achieved_weight", "delta_weight", "cash_limited"])

        scaled = reconciliation.get("scaled_tickers", []) or []
        recon_html = f"""
  <h3>Post-Trade Reconciliation</h3>
  <ul>
    <li><b>Target Cash Weight:</b> {100.0 * float(reconciliation.get('target_cash_weight', 0.0)):.2f}%</li>
    <li><b>Achieved Cash Weight:</b> {100.0 * float(reconciliation.get('achieved_cash_weight', 0.0)):.2f}%</li>
    <li><b>Invested Dollars:</b> ${float(reconciliation.get('invested_dollars', 0.0)):,.2f} / ${float(reconciliation.get('investable_dollars', 0.0)):,.2f} investable</li>
    <li><b>Cash Dollars:</b> ${float(reconciliation.get('cash_dollars', 0.0)):,.2f} / ${float(reconciliation.get('target_cash_dollars', 0.0)):,.2f} target</li>
    <li><b>Cash-constrained tickers:</b> {', '.join(scaled) if scaled else 'None'}</li>
  </ul>
  {df_to_html(recon_df)}
""".rstrip()

        validation = reconciliation.get("open_window_validation") or {}
        reasons = validation.get("reasons") or []
        reason_items = "".join([f"<li>{r}</li>" for r in reasons]) or "<li>None</li>"
        validation_html = f"""
  <h3>Open-Window Validation</h3>
  <ul>
    <li><b>Trade Date:</b> {validation.get('trade_date', run_date)}</li>
    <li><b>Signals File:</b> {validation.get('signals_path', 'n/a')}</li>
    <li><b>Asof Date:</b> {validation.get('asof_date', 'n/a')}</li>
    <li><b>Cutoff Date:</b> {validation.get('cutoff_date', 'n/a')}</li>
    <li><b>Result:</b> {validation.get('result', 'UNKNOWN')}</li>
  </ul>
  <p><b>Reasons</b></p>
  <ul>{reason_items}</ul>
""".rstrip()


    if shadow_status:
        shadow_html = f"""
  <h3>Quasi-Live / Shadow Trading Status</h3>
  <ul>
    <li><b>Trading mode:</b> {shadow_status.get('trading_mode', 'paper')}</li>
    <li><b>Market open/closed:</b> {shadow_status.get('market_status', 'UNKNOWN')}</li>
    <li><b>Orders generated:</b> {int(shadow_status.get('orders_generated', 0))}</li>
    <li><b>Orders blocked:</b> {int(shadow_status.get('orders_blocked', 0))}</li>
    <li><b>Broker recon status:</b> {shadow_status.get('broker_recon_status', 'UNKNOWN')}</li>
  </ul>
""".rstrip()

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

  {recon_html}

  {validation_html}

  {shadow_html}

  <p style="color:#666; margin-top:16px;">
    Execution model: next-open fills with slippage + cash buffer. Ledger is append-only.
  </p>
</div>
""".strip()
