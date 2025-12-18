import os
import datetime as dt
import pandas as pd

# ============================================================
# Sleeve 1 — structured access (do NOT call main())
# ============================================================
from sleeves.sleeve_1.backtest import (
    prepare_data as s1_prepare_data,
    backtest as s1_backtest,
)

# ============================================================
# Sleeve 2 — allow either run_backtest OR prepare_data/backtest
# ============================================================
try:
    from sleeves.sleeve_2.backtest import run_backtest as s2_run_backtest
except Exception:
    s2_run_backtest = None

try:
    from sleeves.sleeve_2.backtest import (
        prepare_data as s2_prepare_data,
        backtest as s2_backtest,
    )
except Exception:
    s2_prepare_data = None
    s2_backtest = None


# ============================================================
# Email sender (exact repo-aware lookup)
# ============================================================
send_email = None
try:
    from core.quant_report import send_email
except Exception:
    pass


# ============================================================
# Output config
# ============================================================
OUTPUT_DIR = "outputs/daily"


# ============================================================
# Helpers
# ============================================================
def _safe_df(df):
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()


def _fmt_money(x):
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return "n/a"


def _fmt_pct(x):
    try:
        return f"{100 * float(x):.2f}%"
    except Exception:
        return "n/a"


def html_table(df: pd.DataFrame, title: str, max_rows: int = 25) -> str:
    df = _safe_df(df)
    df = df.copy()
    df = df.where(pd.notnull(df), "—")
    if df.empty:
        return f"<h3>{title}</h3><p><em>No data</em></p>"

    return (
        f"<h3>{title}</h3>"
        + df.head(max_rows).to_html(
            index=False, border=0, classes="tbl", justify="left"
        )
    )


def filter_sleeve2_cash_proxy(trades: pd.DataFrame) -> pd.DataFrame:
    """
    Remove SGOV 'cash_proxy_fund_entries' rows from Sleeve 2 trades in the email.
    Keeps the report focused on real model trades.
    """
    trades = _safe_df(trades).copy()
    if trades.empty:
        return trades

    if "ticker" in trades.columns and "reason_exit" in trades.columns:
        trades = trades[
            ~(
                (trades["ticker"] == "SGOV")
                & (trades["reason_exit"] == "cash_proxy_fund_entries")
            )
        ].copy()

    return trades


# ============================================================
# Sleeve runners
# ============================================================
def run_sleeve_1():
    print("[SLEEVE 1] Preparing data...")
    signals = s1_prepare_data()
    print("[SLEEVE 1] Running backtest...")
    equity_df, trades_df = s1_backtest(signals)
    return equity_df, trades_df


def run_sleeve_2():
    if s2_run_backtest is not None:
        print("[SLEEVE 2] Running run_backtest()...")
        return s2_run_backtest(period="1y", interval="1d")

    if s2_prepare_data is not None and s2_backtest is not None:
        print("[SLEEVE 2] Preparing data...")
        signals = s2_prepare_data()
        print("[SLEEVE 2] Running backtest...")
        return s2_backtest(signals)

    raise RuntimeError("No valid Sleeve 2 runner found")


# ============================================================
# Report builder
# ============================================================
def summarize_equity(equity_df: pd.DataFrame, sleeve_name: str) -> dict:
    equity_df = _safe_df(equity_df)

    if equity_df.empty or "equity" not in equity_df.columns:
        return {
            "Sleeve": sleeve_name,
            "Equity": "n/a",
            "Day PnL": "n/a",
            "Day Return": "n/a",
        }

    last = equity_df.iloc[-1]
    prev = equity_df.iloc[-2] if len(equity_df) > 1 else last

    pnl = last["equity"] - prev["equity"]
    ret = pnl / prev["equity"] if prev["equity"] else None

    return {
        "Sleeve": sleeve_name,
        "Equity": _fmt_money(last["equity"]),
        "Day PnL": _fmt_money(pnl),
        "Day Return": _fmt_pct(ret),
    }


def build_html_report(
    report_date: str,
    s1_equity, s1_trades,
    s2_equity, s2_trades,
) -> str:

    summary_df = pd.DataFrame([
        summarize_equity(s1_equity, "Sleeve 1 — Momentum"),
        summarize_equity(s2_equity, "Sleeve 2 — Valuation (P/E)"),
    ])

    css = """
    <style>
      body { font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Arial; color:#111827; }
      .wrap { max-width: 960px; margin: 0 auto; padding: 16px; }
      .card { background:#f9fafb; border:1px solid #e5e7eb; border-radius: 10px; padding: 12px; margin: 12px 0; }
      h2 { margin-bottom: 4px; }
      h3 { margin-top: 16px; }
      .tbl { width:100%; border-collapse: collapse; font-size: 13px; }
      .tbl th { text-align:left; border-bottom:1px solid #e5e7eb; padding:6px; }
      .tbl td { border-bottom:1px solid #f3f4f6; padding:6px; }
      .muted { color:#6b7280; font-size:12px; }
    </style>
    """

    html = f"""
    <html>
    <head>{css}</head>
    <body>
      <div class="wrap">
        <h2>Daily Quant Report</h2>
        <div class="muted">{report_date}</div>

        <div class="card">
          {html_table(summary_df, "Portfolio Snapshot")}
        </div>

        <div class="card">
          {html_table(_safe_df(s1_trades), "Recent Trades — Sleeve 1", 15)}
          {html_table(filter_sleeve2_cash_proxy(s2_trades), "Recent Trades — Sleeve 2", 15)}
        </div>

        <div class="card">
          {html_table(_safe_df(s1_equity).tail(10), "Equity — Sleeve 1 (last 10 days)", 10)}
          {html_table(_safe_df(s2_equity).tail(10), "Equity — Sleeve 2 (last 10 days)", 10)}
        </div>

        <div class="muted">
          Automated daily report. Strategy logic unchanged.
        </div>
      </div>
    </body>
    </html>
    """
    return html


# ============================================================
# Main
# ============================================================
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = dt.date.today().strftime("%Y-%m-%d")

    try:
        s1_equity, s1_trades = run_sleeve_1()
    except Exception as e:
        print(f"[WARN] Sleeve 1 failed: {e}")
        s1_equity, s1_trades = pd.DataFrame(), pd.DataFrame()

    try:
        s2_equity, s2_trades = run_sleeve_2()
    except Exception as e:
        print(f"[WARN] Sleeve 2 failed: {e}")
        s2_equity, s2_trades = pd.DataFrame(), pd.DataFrame()

    html = build_html_report(
        today,
        s1_equity, s1_trades,
        s2_equity, s2_trades,
    )

    out_path = os.path.join(OUTPUT_DIR, f"quant_report_{today}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[OK] HTML report written: {out_path}")

    if send_email:
        send_email(
            subject=f"Daily Quant Report — {today}",
            body_html=html,
        )
        print("[OK] Email sent")
    else:
        print("[WARN] send_email not found — HTML generated only")


if __name__ == "__main__":
    main()

