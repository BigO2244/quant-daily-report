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
# Portfolio allocation (dynamic)
# ============================================================
from core.portfolio_alloc import (
    PortfolioAllocator,
    SleeveOutput,
    AllocationResult,
    create_sleeve_output,
    allocation_summary_df,
    holdings_snapshot_df,
    trade_blotter_df,
    validate_allocation_result,
    CASH_TICKER,
    DEFAULT_PORTFOLIO_BASE_EQUITY,
    WEIGHT_TOLERANCE,
)

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
    df = _safe_df(df).copy()
    df = df.where(pd.notnull(df), "—")
    if df.empty:
        return f"<h3>{title}</h3><p><em>No data</em></p>"
    return f"<h3>{title}</h3>" + df.head(max_rows).to_html(index=False, border=0, classes="tbl", justify="left")

def filter_sleeve2_cash_proxy(trades: pd.DataFrame) -> pd.DataFrame:
    """Remove SGOV 'cash_proxy_fund_entries' rows from trades."""
    trades = _safe_df(trades).copy()
    if trades.empty:
        return trades
    if "ticker" in trades.columns and "reason_exit" in trades.columns:
        trades = trades[~((trades["ticker"] == "SGOV") & (trades["reason_exit"] == "cash_proxy_fund_entries"))].copy()
    return trades

# ============================================================
# Sleeve runners
# ============================================================
def run_sleeve_1():
    print("[SLEEVE 1] Preparing data...")
    signals = s1_prepare_data()
    print("[SLEEVE 1] Running backtest...")
    return s1_backtest(signals)

def run_sleeve_trend():
    print("[SLEEVE TREND] Preparing data...")
    signals = st_prepare_data()
    print("[SLEEVE TREND] Running backtest...")
    return st_backtest(signals)

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
# Sleeve output extraction (for dynamic allocation)
# ============================================================
def extract_sleeve_output(
    equity_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    sleeve_name: str,
    base_strength: float = 1.0,
) -> SleeveOutput:
    """
    Extract a SleeveOutput from backtest results.
    Does NOT modify any signal logic - just reads backtest output.
    """
    equity_df = _safe_df(equity_df)
    trades_df = _safe_df(trades_df)
    positions = []
    
    if equity_df.empty:
        return create_sleeve_output([], sleeve_name, 0.0, "No equity data")
    
    latest_equity = float(equity_df["equity"].iloc[-1]) if "equity" in equity_df.columns else 10000.0
    start_equity = float(equity_df["equity"].iloc[0]) if "equity" in equity_df.columns else 10000.0
    
    if not trades_df.empty and "ticker" in trades_df.columns:
        real_trades = trades_df.copy()
        if "reason_exit" in real_trades.columns:
            real_trades = real_trades[~((real_trades.get("ticker", "") == "SGOV") & 
                                        (real_trades.get("reason_exit", "") == "cash_proxy_fund_entries"))]
        
        if not real_trades.empty and "entry_date" in real_trades.columns:
            real_trades["entry_date"] = pd.to_datetime(real_trades["entry_date"], errors="coerce")
            latest_trades = real_trades.nlargest(5, "entry_date")
            
            for _, row in latest_trades.iterrows():
                ticker = row.get("ticker", "")
                shares = row.get("shares", 0)
                entry_price = row.get("entry_price", 0)
                if ticker and shares > 0 and entry_price > 0:
                    notional = shares * entry_price
                    weight = notional / latest_equity if latest_equity > 0 else 0
                    positions.append({
                        "ticker": ticker,
                        "target_weight": weight,
                        "reason": row.get("reason_exit", "signal"),
                        "signal_strength": 1.0,
                    })
    
    is_active = len(positions) > 0
    if is_active and start_equity > 0:
        sleeve_return = (latest_equity / start_equity) - 1.0
        strength = min(1.0, base_strength * max(0.5, min(1.5, 1.0 + sleeve_return)))
    else:
        strength = 0.0
    
    notes = f"Active: {len(positions)} positions, equity ${latest_equity:,.0f}" if is_active else "Inactive"
    return create_sleeve_output(positions, sleeve_name, strength, notes)

# ============================================================
# Report builder
# ============================================================
def summarize_allocated_equity(equity_df: pd.DataFrame, label: str, alloc_capital: float) -> dict:
    """Scale sleeve equity to allocated capital for reporting."""
    equity_df = _safe_df(equity_df)
    if equity_df.empty or "equity" not in equity_df.columns:
        return {"Sleeve": label, "Equity": "—", "Day PnL": "—", "Day Return": "—"}
    
    df = equity_df.reset_index(drop=True)
    start, last = float(df["equity"].iloc[0]), float(df["equity"].iloc[-1])
    prev = float(df["equity"].iloc[-2]) if len(df) > 1 else last
    
    if start <= 0:
        return {"Sleeve": label, "Equity": "—", "Day PnL": "—", "Day Return": "—"}
    
    alloc_last = alloc_capital * (last / start)
    alloc_prev = alloc_capital * (prev / start)
    pnl = alloc_last - alloc_prev
    ret = pnl / alloc_prev if alloc_prev else None
    
    return {"Sleeve": label, "Equity": _fmt_money(alloc_last), "Day PnL": _fmt_money(pnl), "Day Return": _fmt_pct(ret)}

def build_html_report(
    report_date: str,
    st_equity, st_trades,
    s2_equity, s2_trades,
    alloc_result: AllocationResult = None,
) -> str:
    """Build HTML report with dynamic allocation support."""
    BASE_EQUITY = DEFAULT_PORTFOLIO_BASE_EQUITY
    
    # Build summary with dynamic or static allocation
    if alloc_result is not None:
        trend_alloc = alloc_result.sleeve_allocations.get("sleeve_trend", 0.0)
        val_alloc = alloc_result.sleeve_allocations.get("sleeve_2", 0.0)
        
        rows = []
        if trend_alloc > WEIGHT_TOLERANCE:
            rows.append(summarize_allocated_equity(st_equity, f"Sleeve Trend — Momentum ({trend_alloc:.0%})", BASE_EQUITY * trend_alloc))
        else:
            rows.append({"Sleeve": "Sleeve Trend — Momentum (inactive)", "Equity": "—", "Day PnL": "—", "Day Return": "—"})
        
        if val_alloc > WEIGHT_TOLERANCE:
            rows.append(summarize_allocated_equity(s2_equity, f"Sleeve 2 — Valuation ({val_alloc:.0%})", BASE_EQUITY * val_alloc))
        else:
            rows.append({"Sleeve": "Sleeve 2 — Valuation (inactive)", "Equity": "—", "Day PnL": "—", "Day Return": "—"})
        
        cash_alloc = alloc_result.cash_weight
        if cash_alloc > WEIGHT_TOLERANCE:
            rows.append({"Sleeve": f"CASH ({cash_alloc:.0%})", "Equity": _fmt_money(BASE_EQUITY * cash_alloc), "Day PnL": _fmt_money(0), "Day Return": _fmt_pct(0)})
        
        summary_df = pd.DataFrame(rows)
        alloc_summary = allocation_summary_df(alloc_result)
        holdings = holdings_snapshot_df(alloc_result)
        skipped_df = pd.DataFrame(alloc_result.skipped_trades) if alloc_result.skipped_trades else pd.DataFrame()
    else:
        # Legacy static allocation
        summary_df = pd.DataFrame([
            summarize_allocated_equity(st_equity, "Sleeve Trend — Momentum (80%)", BASE_EQUITY * 0.80),
            summarize_allocated_equity(s2_equity, "Sleeve 2 — Valuation (20%)", BASE_EQUITY * 0.20),
        ])
        alloc_summary, holdings, skipped_df = None, None, pd.DataFrame()

    # Portfolio total row
    def _money_to_float(x):
        try:
            return float(str(x).replace("$", "").replace(",", ""))
        except:
            return 0.0
    
    total_equity = sum(_money_to_float(summary_df.iloc[i].get("Equity", 0)) for i in range(len(summary_df)))
    total_pnl = sum(_money_to_float(summary_df.iloc[i].get("Day PnL", 0)) for i in range(len(summary_df)))
    p_prev = total_equity - total_pnl
    p_ret = (total_pnl / p_prev) if p_prev else None
    
    summary_df = pd.concat([summary_df, pd.DataFrame([{
        "Sleeve": f"TOTAL — Portfolio (${BASE_EQUITY:,.0f})",
        "Equity": _fmt_money(total_equity),
        "Day PnL": _fmt_money(total_pnl),
        "Day Return": _fmt_pct(p_ret),
    }])], ignore_index=True)

    # Build exit log
    exit_log_rows = []
    for trades_df, sleeve_name in [(st_trades, "Trend"), (s2_trades, "Valuation")]:
        filtered = filter_sleeve2_cash_proxy(_safe_df(trades_df)) if sleeve_name == "Valuation" else _safe_df(trades_df)
        if not filtered.empty and "reason_exit" in filtered.columns:
            for _, row in filtered.tail(10).iterrows():
                exit_log_rows.append({
                    "Ticker": row.get("ticker", ""),
                    "Sleeve": sleeve_name,
                    "Exit Reason": row.get("reason_exit", ""),
                    "Days Held": row.get("hold_days", ""),
                    "P&L": _fmt_money(row.get("pnl", 0)),
                })

    # CSS
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

    # Build sections
    alloc_section = f'<div class="card"><h3>Sleeve Allocation (Dynamic)</h3>{alloc_summary.to_html(index=False, border=0, classes="tbl", justify="left")}</div>' if alloc_summary is not None else ""
    holdings_section = f'<div class="card">{html_table(holdings, "Holdings Snapshot", 20)}</div>' if holdings is not None and not holdings.empty else ""
    skipped_section = f'<div class="card">{html_table(skipped_df, "Skipped Trades (Constraint Hits)", 10)}</div>' if not skipped_df.empty else ""
    exit_section = f'<div class="card">{html_table(pd.DataFrame(exit_log_rows), "Exit Log", 15)}</div>' if exit_log_rows else ""

    return f"""
    <html>
    <head><style>{css}</style></head>
    <body>
      <div class="wrap">
        <h2>Daily Quant Report</h2>
        <div class="muted">{report_date}</div>
        <div class="card">{html_table(summary_df, "Portfolio Snapshot")}</div>
        {alloc_section}
        {holdings_section}
        <div class="card">
          {html_table(filter_sleeve2_cash_proxy(st_trades), "Recent Trades — Sleeve Trend", 15)}
          {html_table(filter_sleeve2_cash_proxy(s2_trades), "Recent Trades — Sleeve 2", 15)}
        </div>
        {exit_section}
        {skipped_section}
        <div class="card">
          {html_table(_safe_df(st_equity).tail(10), "Equity — Sleeve Trend (last 10 days)", 10)}
          {html_table(_safe_df(s2_equity).tail(10), "Equity — Sleeve 2 (last 10 days)", 10)}
        </div>
        <div class="muted">Automated daily report. Dynamic allocation enabled. Strategy logic unchanged.</div>
      </div>
    </body>
    </html>
    """

# ============================================================
# Main
# ============================================================
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = dt.date.today().strftime("%Y-%m-%d")

    # Run sleeves
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

    # Extract sleeve outputs for dynamic allocation
    trend_output = extract_sleeve_output(st_equity, st_trades, "sleeve_trend", 1.0)
    val_output = extract_sleeve_output(s2_equity, s2_trades, "sleeve_2", 1.0)
    
    # Run dynamic allocation
    allocator = PortfolioAllocator()
    alloc_result = allocator.allocate([trend_output, val_output])
    
    # Validate allocation
    errors = validate_allocation_result(alloc_result)
    if errors:
        print(f"[WARN] Allocation validation errors: {errors}")
    
    # Log allocation summary
    print(f"\n[ALLOCATION] Sleeve allocations:")
    for sleeve, pct in alloc_result.sleeve_allocations.items():
        print(f"  {sleeve}: {pct:.1%}")
    print(f"  CASH: {alloc_result.cash_weight:.1%}")
    print(f"  Total weight: {alloc_result.total_weight:.4f}")

    # Build report
    html = build_html_report(today, st_equity, st_trades, s2_equity, s2_trades, alloc_result)

    out_path = os.path.join(OUTPUT_DIR, f"quant_report_{today}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[OK] HTML report written: {out_path}")

    if send_email:
        try:
            send_email(subject=f"Daily Quant Report — {today}", body_html=html)
            print("[OK] Email sent")
        except Exception as e:
            print(f"[WARN] Email not sent: {e}")
    else:
        print("[WARN] send_email not found — HTML generated only")


if __name__ == "__main__":
    main()
