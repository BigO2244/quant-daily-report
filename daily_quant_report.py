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
# Sleeve Trend — structured access
# ============================================================
from sleeves.sleeve_trend.backtest import (
    prepare_data as st_prepare_data,
    backtest as st_backtest,
)

# ============================================================
# Sleeve 2 — import module once; resolve symbols dynamically
# ============================================================
try:
    import sleeves.sleeve_2.backtest as s2_mod
except Exception:
    s2_mod = None

s2_run_backtest = getattr(s2_mod, "run_backtest", None) if s2_mod else None
s2_prepare_data = getattr(s2_mod, "prepare_data", None) if s2_mod else None
s2_backtest = getattr(s2_mod, "backtest", None) if s2_mod else None


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

def run_sleeve_trend():
    print("[SLEEVE TREND] Preparing data...")
    signals = st_prepare_data()
    print("[SLEEVE TREND] Running backtest...")
    equity_df, trades_df = st_backtest(signals)
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
def summarize_allocated_equity(equity_df: pd.DataFrame, label: str, alloc_capital: float) -> dict:
    """
    Report-only scaling so both sleeves reconcile to a single portfolio allocation:
      alloc_equity[t] = alloc_capital * (raw_equity[t] / raw_equity[0])
    This keeps sleeve logic unchanged while making the snapshot add up to the portfolio baseline.
    """
    equity_df = _safe_df(equity_df)

    if equity_df.empty or "equity" not in equity_df.columns:
        return {"Sleeve": label, "Equity": "—", "Day PnL": "—", "Day Return": "—"}

    df = equity_df.reset_index(drop=True)
    start = float(df["equity"].iloc[0])
    last = float(df["equity"].iloc[-1])
    prev = float(df["equity"].iloc[-2]) if len(df) > 1 else last

    if start <= 0:
        return {"Sleeve": label, "Equity": "—", "Day PnL": "—", "Day Return": "—"}

    alloc_last = alloc_capital * (last / start)
    alloc_prev = alloc_capital * (prev / start)

    pnl = alloc_last - alloc_prev
    ret = pnl / alloc_prev if alloc_prev else None

    return {
        "Sleeve": label,
        "Equity": _fmt_money(alloc_last),
        "Day PnL": _fmt_money(pnl),
        "Day Return": _fmt_pct(ret),
    }


def build_html_report(
    report_date: str,
    s1_equity, s1_trades,
    s2_equity, s2_trades,
) -> str:

    BASE_EQUITY = 10_000.0
    S1_W = 0.80
    S2_W = 0.20

    summary_df = pd.DataFrame([
        summarize_allocated_equity(
            s1_equity,
            "Sleeve 1 — Momentum (80%)",
            BASE_EQUITY * S1_W,
        ),
        summarize_allocated_equity(
            s2_equity,
            "Sleeve 2 — Valuation (P/E) (20%)",
            BASE_EQUITY * S2_W,
        ),
    ])

    # Add portfolio total row (sum of allocated sleeves)
    def _money_to_float(x):
        try:
            return float(str(x).replace("$", "").replace(",", ""))
        except Exception:
            return 0.0

    s1_eq = _money_to_float(summary_df.loc[0, "Equity"])
    s2_eq = _money_to_float(summary_df.loc[1, "Equity"])
    s1_pnl = _money_to_float(summary_df.loc[0, "Day PnL"])
    s2_pnl = _money_to_float(summary_df.loc[1, "Day PnL"])

    p_eq = s1_eq + s2_eq
    p_pnl = s1_pnl + s2_pnl
    p_prev = p_eq - p_pnl
    p_ret = (p_pnl / p_prev) if p_prev else None

    portfolio_row = pd.DataFrame([{
        "Sleeve": "TOTAL — Portfolio ($10,000)",
        "Equity": _fmt_money(p_eq),
        "Day PnL": _fmt_money(p_pnl),
        "Day Return": _fmt_pct(p_ret),
    }])

    summary_df = pd.concat([summary_df, portfolio_row], ignore_index=True)

    css = """
     body { font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Arial; color:#111827; }
     .wrap { max-width: 960px; margin: 0 auto; padding: 16px; }
     .card { background:#f9fafb; border:1px solid #e5e7eb; border-radius: 10px; padding: 12px; margin: 12px 0; }
     h2 { margin-bottom: 4px; }
     h3 { margin-top: 16px; }
     .tbl { width:100%; border-collapse: collapse; font-size: 13px; }
     .tbl th { text-align:left; border-bottom:1px solid #e5e7eb; padding:6px; }
     .tbl td { border-bottom:1px solid #f3f4f6; padding:6px; }
     .muted { color:#6b7280; font-size:12px; }
   """
 
    html = f"""
    <html>
   <head><style>{css}</style></head>
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
        st_equity, st_trades = run_sleeve_trend()
    except Exception as e:
        print(f"[WARN] Sleeve Trend failed: {e}")
        st_equity, st_trades = pd.DataFrame(), pd.DataFrame()

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
     try:
        send_email(
            subject=f"Daily Quant Report — {today}",
            body_html=html,
        )
        print("[OK] Email sent")
     except Exception as e:
        print(f"[WARN] Email not sent: {e}")
     else:
      print("[WARN] send_email not found — HTML generated only")



if __name__ == "__main__":
    main()

