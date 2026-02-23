# paper/paper_report.py
from __future__ import annotations

import os
import pandas as pd

from paper.paths import LEDGER_TRADES_PATH


def _read_csv_if_exists(path: str) -> pd.DataFrame:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return pd.DataFrame()
    return pd.read_csv(path)


def _fmt_money(value: float | int | None) -> str:
    if value is None:
        return "unavailable"
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "unavailable"


def _fmt_pct(value: float | int | None) -> str:
    if value is None:
        return "unavailable"
    try:
        return f"{float(value):.1%}"
    except Exception:
        return "unavailable"


def build_paper_report_html(
    run_date: str,
    ledger_path: str,
    trades_path: str,
    benchmark_ticker: str = "SPY",
    reconciliation: dict | None = None,
    shadow_status: dict | None = None,
) -> str:
    canonical_trades_path = str(LEDGER_TRADES_PATH)
    canonical_nav_path = "outputs/perf/nav_timeseries.csv"
    led = _read_csv_if_exists(ledger_path)
    day = led[led["date"] == run_date].copy() if (not led.empty and "date" in led.columns) else pd.DataFrame()

    trades = _read_csv_if_exists(trades_path)
    trades = trades[trades["date"] == run_date].copy() if (not trades.empty and "date" in trades.columns) else pd.DataFrame()

    nav_ts = _read_csv_if_exists(canonical_nav_path)
    nav_row = pd.DataFrame()
    if not nav_ts.empty and "date" in nav_ts.columns:
        nav_ts["date"] = pd.to_datetime(nav_ts["date"])
        nav_row = nav_ts[nav_ts["date"] == pd.to_datetime(run_date)]

    mode = str((shadow_status or {}).get("trading_mode") or "").upper()
    missing_inputs = []
    if not os.path.exists(canonical_trades_path):
        missing_inputs.append(canonical_trades_path)
    if not os.path.exists(canonical_nav_path):
        missing_inputs.append(canonical_nav_path)

    summary_unavailable = mode == "SHADOW" and bool(missing_inputs)

    equity = None
    cash = None
    invested = None
    exposure = None
    turnover_dollars = None
    turnover_pct = None
    if not summary_unavailable:
        try:
            if not nav_row.empty:
                equity = float(nav_row["equity"].iloc[0]) if "equity" in nav_row.columns else None
                cash = float(nav_row["cash"].iloc[0]) if "cash" in nav_row.columns else None
                if equity is not None and cash is not None:
                    invested = float(equity - cash)
                elif not day.empty and "market_value" in day.columns:
                    invested = float(day["market_value"].sum())
                if "gross_exposure" in nav_row.columns:
                    exposure = float(nav_row["gross_exposure"].iloc[0])
                elif equity and invested is not None:
                    exposure = invested / equity
                if "turnover_dollars" in nav_row.columns:
                    turnover_dollars = float(nav_row["turnover_dollars"].iloc[0])
                if "turnover_pct" in nav_row.columns:
                    turnover_pct = float(nav_row["turnover_pct"].iloc[0])
            elif not day.empty:
                equity = float(day["total_equity"].iloc[0]) if "total_equity" in day.columns else None
                cash = float(day["cash"].iloc[0]) if "cash" in day.columns else None
                invested = float(day["market_value"].sum()) if "market_value" in day.columns else None
                exposure = (invested / equity) if (equity and invested is not None) else None
        except Exception:
            equity, cash, invested, exposure = None, None, None, None

    if not trades.empty:
        trades_view = trades[["ticker", "side", "shares", "price", "slippage_cost", "notional", "reason"]].copy()
    else:
        trades_view = pd.DataFrame(columns=["ticker", "side", "shares", "price", "slippage_cost", "notional", "reason"])

    holdings = (
        day.sort_values("market_value", ascending=False)[["ticker", "sleeve", "shares", "price", "market_value"]].head(15)
        if (not day.empty and "market_value" in day.columns)
        else pd.DataFrame(columns=["ticker", "sleeve", "shares", "price", "market_value"])
    )

    def df_to_html(df: pd.DataFrame) -> str:
        if df is None or df.empty:
            return "<p><em>None</em></p>"
        return df.to_html(index=False, border=0)

    nav_html = ""
    if not nav_row.empty:
        eq = float(nav_row["equity"].iloc[0]) if "equity" in nav_row.columns else None
        r1d = float(nav_row["return_1d"].iloc[0]) if "return_1d" in nav_row.columns else None
        month_rows = nav_ts[nav_ts["date"].dt.to_period("M") == pd.to_datetime(run_date).to_period("M")]
        week_rows = nav_ts[nav_ts["date"].dt.isocalendar().week == pd.to_datetime(run_date).isocalendar().week]
        si_base = float(nav_ts["equity"].iloc[0]) if "equity" in nav_ts.columns and len(nav_ts) else None
        wtd = (eq / float(week_rows["equity"].iloc[0]) - 1.0) if (eq is not None and not week_rows.empty) else None
        mtd = (eq / float(month_rows["equity"].iloc[0]) - 1.0) if (eq is not None and not month_rows.empty) else None
        si = (eq / si_base - 1.0) if (eq is not None and si_base) else None
        nav_html = f"""
  <h3>Ledger-backed NAV</h3>
  <ul>
    <li><b>NAV Equity:</b> {_fmt_money(eq)}</li>
    <li><b>1D Return:</b> {_fmt_pct(r1d)}</li>
    <li><b>WTD:</b> {_fmt_pct(wtd)}</li>
    <li><b>MTD:</b> {_fmt_pct(mtd)}</li>
    <li><b>Since Inception:</b> {_fmt_pct(si)}</li>
  </ul>
""".rstrip()

    contrib_t = _read_csv_if_exists(f"outputs/perf/contribution_tickers_{run_date}.csv")
    contrib_s = _read_csv_if_exists(f"outputs/perf/contribution_sleeves_{run_date}.csv")
    contrib_html = ""
    if not contrib_t.empty:
        top = contrib_t.sort_values("contribution", ascending=False).head(5)
        contrib_html += f"<h3>Top Ticker Contributors</h3>{df_to_html(top)}"
    if not contrib_s.empty:
        contrib_html += f"<h3>Sleeve Contribution</h3>{df_to_html(contrib_s)}"

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

    market_status_raw = (shadow_status or {}).get("market_status")
    market_guard = (shadow_status or {}).get("market_guard") if isinstance(shadow_status, dict) else None
    if not market_status_raw and isinstance(market_guard, dict):
        guard_status = market_guard.get("status")
        if guard_status is None and market_guard.get("is_open_now") is not None:
            market_status_raw = "OPEN" if bool(market_guard.get("is_open_now")) else "CLOSED"
        else:
            market_status_raw = guard_status
    market_status = str(market_status_raw or "").strip().upper() or "UNKNOWN"
    if shadow_status:
        shadow_html = f"""
  <h3>Quasi-Live / Shadow Trading Status</h3>
  <ul>
    <li><b>Trading mode:</b> {shadow_status.get('trading_mode', 'paper')}</li>
    <li><b>Market open/closed:</b> {market_status}</li>
    <li><b>Orders generated:</b> {int(shadow_status.get('orders_generated', 0))}</li>
    <li><b>Orders blocked:</b> {int(shadow_status.get('orders_blocked', 0))}</li>
    <li><b>Broker recon status:</b> {shadow_status.get('broker_recon_status', 'UNKNOWN')}</li>
  </ul>
""".rstrip()

    unavailable_html = ""
    if summary_unavailable:
        reason = "Paper execution summary unavailable (SHADOW run)"
        detail = "".join(f"<li>{p}</li>" for p in missing_inputs) or "<li>Unknown reason</li>"
        unavailable_html = f"<p><em>{reason}</em></p><ul><li><b>Missing inputs:</b></li>{detail}</ul>"

    return f"""
<div style="font-family: Arial, sans-serif;">
  <h2>Paper Trading Execution — {run_date}</h2>
    {unavailable_html if summary_unavailable else f'''<ul>
    <li><b>Total Equity:</b> {_fmt_money(equity)}</li>
    <li><b>Cash:</b> {_fmt_money(cash)}</li>
    <li><b>Invested:</b> {_fmt_money(invested)}</li>
    <li><b>Exposure:</b> {_fmt_pct(exposure)}</li>
    <li><b>Turnover ($):</b> {_fmt_money(turnover_dollars)}</li>
    <li><b>Turnover (%):</b> {_fmt_pct(turnover_pct)}</li>
    <li><b>Benchmark:</b> {benchmark_ticker}</li>
  </ul>'''}

  <h3>Trades Executed</h3>
  {df_to_html(trades_view)}

  <h3>Holdings (Top 15)</h3>
  {df_to_html(holdings)}

  {recon_html}

  {validation_html}

  {nav_html}

  {contrib_html}

  {shadow_html}

  <p style="color:#666; margin-top:16px;">
    Execution model: next-open fills with slippage + cash buffer. Ledger is append-only.
  </p>
</div>
""".strip()
