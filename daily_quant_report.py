import os
import datetime as dt
import pandas as pd
from paper.signals_io import write_signals_snapshot
from paper.paper_broker import run_paper_day
from paper.paper_report import build_paper_report_html
from paper.trading_calendar import prev_trading_day



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
from sleeves.sleeve_trend import config as trend_cfg

# ============================================================
# Sleeve 2 — import module once; resolve symbols dynamically
# ============================================================
try:
    import sleeves.sleeve_2.backtest as s2_mod
except Exception:
    s2_mod = None

s2_run_backtest = getattr(s2_mod, "run_backtest", None) if s2_mod else None
s2_run_backtest_details = getattr(s2_mod, "run_backtest_with_details", None) if s2_mod else None
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
    validate_allocation_result,
    CASH_TICKER,
    DEFAULT_PORTFOLIO_BASE_EQUITY,
    WEIGHT_TOLERANCE,
)

from core.quant_report import (
    download_prices,
    add_atr,
    create_trade_email,
)

from engine.backtest_engine import infer_latest_entries, attach_entry_prices

from sleeves.sleeve_2.config import (
    STOP_ATR_MULT,
    TAKE_PROFIT_ATR_MULT,
    STOP_PCT,
    TAKE_PROFIT_PCT,
    LONG_THRESHOLD as S2_LONG_THRESHOLD,
    Z_EXTREME_SHORT,
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


def _asof_date_from_df(df: pd.DataFrame) -> pd.Timestamp | None:
    if df is None or df.empty:
        return None
    if "date" in df.columns:
        return pd.to_datetime(df["date"]).max()
    return None


def _compute_execution_price(price_map: dict, ticker: str) -> float | None:
    entry = price_map.get(ticker, {})
    return entry.get("next_open") or entry.get("last_close")


def _build_price_map(prices: pd.DataFrame, asof: pd.Timestamp) -> dict:
    price_map = {}
    if prices.empty:
        return price_map

    df = prices.copy()
    df["date"] = pd.to_datetime(df["date"])

    for ticker, group in df.groupby("ticker"):
        group = group.sort_values("date")
        past = group[group["date"] <= asof]
        future = group[group["date"] > asof]
        last_close = past["close"].iloc[-1] if not past.empty else None
        next_open = future["open"].iloc[0] if not future.empty else None
        price_map[ticker] = {
            "last_close": float(last_close) if last_close is not None else None,
            "next_open": float(next_open) if next_open is not None else None,
        }

    return price_map


def _build_atr_map(prices: pd.DataFrame, asof: pd.Timestamp) -> dict:
    if prices.empty:
        return {}
    atr_df = add_atr(prices)
    atr_df = atr_df.sort_values(["ticker", "date"])
    atr_map = {}
    for ticker, group in atr_df.groupby("ticker"):
        past = group[group["date"] <= asof]
        if past.empty:
            atr_map[ticker] = None
        else:
            atr_map[ticker] = float(past["atr"].iloc[-1]) if pd.notna(past["atr"].iloc[-1]) else None
    return atr_map


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
# Sleeve health check
# ============================================================

def _sleeve_is_valid(equity_df: pd.DataFrame) -> tuple[bool, str]:
    """
    Check whether a sleeve produced valid results.

    Returns (is_valid, reason) where reason is empty string if valid.
    """
    equity_df = _safe_df(equity_df)
    if equity_df.empty:
        return False, "empty equity_df"
    if "equity" not in equity_df.columns:
        return False, "no 'equity' column"
    if len(equity_df) < 1:
        return False, "zero rows"
    last_eq = equity_df["equity"].iloc[-1]
    if pd.isna(last_eq) or last_eq <= 0:
        return False, f"invalid terminal equity ({last_eq})"
    return True, ""


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
    equity_df, trades_df = st_backtest(signals)
    return equity_df, trades_df, signals


def run_sleeve_2():
    if s2_run_backtest_details is not None:
        print("[SLEEVE 2] Running run_backtest_with_details()...")
        return s2_run_backtest_details(period="1y", interval="1d")
    if s2_run_backtest is not None:
        print("[SLEEVE 2] Running run_backtest()...")
        equity_df, trades_df = s2_run_backtest(period="1y", interval="1d")
        return {"equity_df": equity_df, "trades_df": trades_df}
    if s2_prepare_data is not None and s2_backtest is not None:
        print("[SLEEVE 2] Preparing data...")
        signals = s2_prepare_data()
        print("[SLEEVE 2] Running backtest...")
        equity_df, trades_df = s2_backtest(signals)
        return {"equity_df": equity_df, "trades_df": trades_df}
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
                                        (real_trades.get("reason_exit", "") == "cash_proxy_fund_entries"))].copy()

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

        # Also handle engine-style trades (which have "date" + "weight_to" instead
        # of "entry_date" + "shares").  This allows Sleeve 2 engine trades to
        # register as active positions for allocation purposes.
        if not real_trades.empty and "weight_to" in real_trades.columns and not positions:
            real_trades_sorted = real_trades.copy()
            if "date" in real_trades_sorted.columns:
                real_trades_sorted["date"] = pd.to_datetime(real_trades_sorted["date"], errors="coerce")
                real_trades_sorted = real_trades_sorted.sort_values("date", ascending=False)
            latest_engine_trades = real_trades_sorted.head(5)
            for _, row in latest_engine_trades.iterrows():
                ticker = row.get("ticker", "")
                w = abs(row.get("weight_to", 0.0))
                if ticker and w > 1e-6:
                    positions.append({
                        "ticker": ticker,
                        "target_weight": w,
                        "reason": "engine_signal",
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
# Portfolio equity computation (FIXED)
# ============================================================

def compute_portfolio_equity(
    sleeve_equity_map: dict[str, pd.DataFrame],
    sleeve_allocations: dict[str, float],
    cash_weight: float,
    base_equity: float = DEFAULT_PORTFOLIO_BASE_EQUITY,
) -> dict:
    """
    Compute TRUE portfolio equity from sleeve returns and allocations.

    This computes:
        portfolio_return = sum(sleeve_alloc_i * sleeve_return_i) + cash_weight * 0
        portfolio_equity = base_equity * (1 + portfolio_return)

    Returns dict with: equity, prev_equity, day_pnl, day_return, cumulative_return

    NOTE: This is the ONLY correct way to compute portfolio total.
    Do NOT sum sleeve equities - they are independent backtests on their own base.
    """
    portfolio_return = 0.0
    portfolio_prev_return = 0.0

    for sleeve_name, alloc in sleeve_allocations.items():
        if alloc <= WEIGHT_TOLERANCE:
            continue

        equity_df = _safe_df(sleeve_equity_map.get(sleeve_name, pd.DataFrame()))
        if equity_df.empty or "equity" not in equity_df.columns:
            continue

        df = equity_df.reset_index(drop=True)
        start = float(df["equity"].iloc[0])
        last = float(df["equity"].iloc[-1])
        prev = float(df["equity"].iloc[-2]) if len(df) > 1 else last

        if start <= 0:
            continue

        # Sleeve cumulative return (from its own backtest)
        sleeve_cum_return = (last / start) - 1.0
        sleeve_prev_return = (prev / start) - 1.0

        # Weighted contribution to portfolio return
        portfolio_return += alloc * sleeve_cum_return
        portfolio_prev_return += alloc * sleeve_prev_return

    # Cash contributes 0 return (already accounted for by not adding anything)

    # Portfolio equity
    portfolio_equity = base_equity * (1.0 + portfolio_return)
    portfolio_prev_equity = base_equity * (1.0 + portfolio_prev_return)

    day_pnl = portfolio_equity - portfolio_prev_equity
    day_return = day_pnl / portfolio_prev_equity if portfolio_prev_equity > 0 else 0.0

    return {
        "equity": portfolio_equity,
        "prev_equity": portfolio_prev_equity,
        "day_pnl": day_pnl,
        "day_return": day_return,
        "cumulative_return": portfolio_return,
    }


# ============================================================
# Daily snapshot builder
# ============================================================

def build_daily_snapshot(
    report_date: pd.Timestamp,
    alloc_result: AllocationResult,
    portfolio_stats: dict,
    st_signals: pd.DataFrame,
    s2_details: dict,
) -> dict:
    weights_df = _safe_df(alloc_result.combined_weights)
    weights_df = weights_df[weights_df["ticker"] != CASH_TICKER].copy()
    weights_df = weights_df[weights_df["target_weight"].abs() > WEIGHT_TOLERANCE]

    # --- Paper trading signals snapshot (daily immutable file) ---
    # Prefer a YYYY-MM-DD string you already use in the report.
    # If you already have something like report_date_str / asof_date_str / today_str, use that here.
    run_date_str = report_date.strftime("%Y-%m-%d")


    signals_path = write_signals_snapshot(
        df_targets=weights_df,
        run_date=run_date_str,
        out_dir="signals",
        sleeve_col="sleeve"  # if column exists; otherwise writer will default to "core"
    )
    print(f"[PAPER] Wrote signals snapshot: {signals_path}")


    tickers = sorted(weights_df["ticker"].unique().tolist()) if not weights_df.empty else []

    prices = pd.DataFrame()
    if tickers:
        prices = download_prices(tickers, period="6mo", interval="1d")

    price_map = _build_price_map(prices, report_date)
    atr_map = _build_atr_map(prices, report_date)

    entry_map = {}
    if s2_details and s2_details.get("weights_df") is not None and not s2_details.get("weights_df").empty:
        weights_history = s2_details.get("weights_df")
        prices_wide = s2_details.get("prices_wide")
        entries = infer_latest_entries(weights_history)
        entries = attach_entry_prices(entries, prices_wide)
        for _, row in entries.iterrows():
            entry_map[row["ticker"]] = {
                "entry_date": pd.to_datetime(row["entry_date"]).strftime("%Y-%m-%d"),
                "entry_price": row.get("entry_price"),
            }

    # Holdings
    holdings = []
    risk_levels = []
    model_equity = portfolio_stats.get("equity", DEFAULT_PORTFOLIO_BASE_EQUITY)

    for _, row in weights_df.iterrows():
        ticker = row["ticker"]
        weight = float(row["target_weight"])
        direction = "LONG" if weight > 0 else "SHORT"
        price_info = price_map.get(ticker, {})
        last_px = price_info.get("last_close")
        entry_info = entry_map.get(ticker)

        entry_date = entry_info.get("entry_date") if entry_info else report_date.strftime("%Y-%m-%d")
        entry_px = entry_info.get("entry_price") if entry_info and entry_info.get("entry_price") else last_px

        if last_px is None or entry_px is None:
            pnl_dollars = None
            pnl_pct = None
        else:
            shares = abs(weight) * model_equity / last_px if last_px > 0 else 0.0
            if weight > 0:
                pnl_dollars = shares * (last_px - entry_px)
                pnl_pct = (last_px / entry_px) - 1.0 if entry_px > 0 else None
            else:
                pnl_dollars = shares * (entry_px - last_px)
                pnl_pct = (entry_px / last_px) - 1.0 if last_px > 0 else None

        days_held = (report_date - pd.to_datetime(entry_date)).days if entry_date else None

        holdings.append({
            "ticker": ticker,
            "direction": direction,
            "entry_date": entry_date,
            "entry_price": entry_px,
            "last_price": last_px,
            "pnl_dollars": pnl_dollars,
            "pnl_pct": pnl_pct,
            "days_held": days_held,
        })

        atr = atr_map.get(ticker)
        if entry_px is None:
            stop_loss = None
            take_profit = None
        elif atr is not None:
            if weight > 0:
                stop_loss = entry_px - STOP_ATR_MULT * atr
                take_profit = entry_px + TAKE_PROFIT_ATR_MULT * atr
            else:
                stop_loss = entry_px + STOP_ATR_MULT * atr
                take_profit = entry_px - TAKE_PROFIT_ATR_MULT * atr
        else:
            if weight > 0:
                stop_loss = entry_px * (1 - STOP_PCT)
                take_profit = entry_px * (1 + TAKE_PROFIT_PCT)
            else:
                stop_loss = entry_px * (1 + STOP_PCT)
                take_profit = entry_px * (1 - TAKE_PROFIT_PCT)

        risk_levels.append({
            "ticker": ticker,
            "entry_price": entry_px,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        })

    # Trades for today
    prev_weights = {}
    if s2_details and s2_details.get("weights_df") is not None and not s2_details.get("weights_df").empty:
        hist = s2_details.get("weights_df")
        if len(hist) > 1:
            prev_row = hist.iloc[-2]
            prev_weights = prev_row.to_dict()

    new_weights = {row["ticker"]: float(row["target_weight"]) for _, row in weights_df.iterrows()}
    all_tickers = sorted(set(prev_weights.keys()) | set(new_weights.keys()))
    orders = []

    for ticker in all_tickers:
        if ticker == CASH_TICKER:
            continue
        prev_w = float(prev_weights.get(ticker, 0.0))
        new_w = float(new_weights.get(ticker, 0.0))

        if abs(prev_w) <= WEIGHT_TOLERANCE and abs(new_w) <= WEIGHT_TOLERANCE:
            continue

        def _append_order(action, weight):
            exec_px = _compute_execution_price(price_map, ticker)
            shares = None
            notional = None
            if exec_px and model_equity > 0:
                notional = abs(weight) * model_equity
                shares = round(notional / exec_px, 2) if exec_px > 0 else None
            orders.append({
                "action": action,
                "ticker": ticker,
                "target_weight": weight,
                "execution_price": exec_px,
                "shares": shares,
                "notional": notional,
            })

        if abs(prev_w) <= WEIGHT_TOLERANCE and abs(new_w) > WEIGHT_TOLERANCE:
            _append_order("BUY" if new_w > 0 else "SHORT", new_w)
        elif abs(prev_w) > WEIGHT_TOLERANCE and abs(new_w) <= WEIGHT_TOLERANCE:
            _append_order("SELL" if prev_w > 0 else "COVER", 0.0)
        elif prev_w * new_w < 0:
            _append_order("SELL" if prev_w > 0 else "COVER", 0.0)
            _append_order("BUY" if new_w > 0 else "SHORT", new_w)

    # Watchlist
    watchlist = []
    held = {h["ticker"] for h in holdings}

    if st_signals is not None and not st_signals.empty:
        st_latest = st_signals[st_signals["date"] == st_signals["date"].max()].copy()
        st_latest = st_latest[~st_latest["ticker"].isin(held)]
        st_latest = st_latest[st_latest["passes_liquidity"]]
        st_latest["delta_long"] = trend_cfg.LONG_THRESHOLD - st_latest["final_signal"]
        st_latest["delta_short"] = trend_cfg.SHORT_THRESHOLD - st_latest["final_signal"]
        near_long = st_latest[(st_latest["signal_long"]) & (st_latest["delta_long"] >= 0) & (st_latest["delta_long"] <= 5)]
        near_short = st_latest[(st_latest["signal_short"]) & (st_latest["delta_short"] >= 0) & (st_latest["delta_short"] <= 5)]
        for _, row in pd.concat([near_long, near_short]).head(5).iterrows():
            watchlist.append({
                "ticker": row["ticker"],
                "reason": f"trend score={row['final_signal']:.1f} vs {trend_cfg.LONG_THRESHOLD if row['signal_long'] else trend_cfg.SHORT_THRESHOLD} threshold",
            })

    s2_signals = s2_details.get("signals") if s2_details else pd.DataFrame()
    if s2_signals is not None and not s2_signals.empty:
        s2_latest = s2_signals[s2_signals["date"] == s2_signals["date"].max()].copy()
        s2_latest = s2_latest[~s2_latest["ticker"].isin(held)]
        s2_latest["delta_score"] = S2_LONG_THRESHOLD - s2_latest["score_long"]
        near_long = s2_latest[(s2_latest["delta_score"] >= 0) & (s2_latest["delta_score"] <= 5)]
        near_short = s2_latest[s2_latest["z_pe"] >= (Z_EXTREME_SHORT - 0.25)]
        for _, row in near_long.head(5).iterrows():
            watchlist.append({
                "ticker": row["ticker"],
                "reason": f"score={row['score_long']:.1f} vs {S2_LONG_THRESHOLD} threshold",
            })
        for _, row in near_short.head(5).iterrows():
            watchlist.append({
                "ticker": row["ticker"],
                "reason": f"z_pe={row['z_pe']:.2f} vs {Z_EXTREME_SHORT} short threshold",
            })

    watchlist = watchlist[:10]

    broker_equity = os.environ.get("BROKER_EQUITY")
    broker_equity_val = float(broker_equity) if broker_equity not in (None, "") else None

    reconciliation = {
        "model_start_equity": DEFAULT_PORTFOLIO_BASE_EQUITY,
        "model_current_equity": portfolio_stats.get("equity"),
        "broker_equity": broker_equity_val,
        "difference": (portfolio_stats.get("equity") - broker_equity_val) if broker_equity_val else None,
        "note": "slippage/fees/timing" if broker_equity_val else "broker equity placeholder (set BROKER_EQUITY)",
    }

    allocations = {
        "risk_on": 1.0 - alloc_result.cash_weight,
        "cash": alloc_result.cash_weight,
        "sleeves": alloc_result.sleeve_allocations,
    }

    return {
        "asof": report_date,
        "allocations": allocations,
        "orders": orders,
        "risk_levels": risk_levels,
        "holdings": holdings,
        "watchlist": watchlist,
        "reconciliation": reconciliation,
    }


# ============================================================
# Report builder (FIXED)
# ============================================================

def build_html_report(
    report_date: str,
    st_equity, st_trades,
    s2_equity, s2_trades,
    alloc_result: AllocationResult = None,
) -> str:
    """
    Build HTML report with CORRECT portfolio math.

    Portfolio Snapshot now shows:
    - Sleeve rows: allocated notional only (attribution), NOT additive equity
    - CASH row: allocated notional with 0 return
    - TOTAL row: TRUE portfolio equity computed from weighted sleeve returns

    The TOTAL is the ONLY authoritative equity figure.
    """
    BASE_EQUITY = DEFAULT_PORTFOLIO_BASE_EQUITY

    # Build summary with dynamic allocation
    if alloc_result is not None:
        trend_alloc = alloc_result.sleeve_allocations.get("sleeve_trend", 0.0)
        val_alloc = alloc_result.sleeve_allocations.get("sleeve_2", 0.0)
        cash_alloc = alloc_result.cash_weight

        # Build sleeve equity map for portfolio computation
        sleeve_equity_map = {
            "sleeve_trend": st_equity,
            "sleeve_2": s2_equity,
        }

        # Compute TRUE portfolio equity (the only correct total)
        portfolio_stats = compute_portfolio_equity(
            sleeve_equity_map=sleeve_equity_map,
            sleeve_allocations=alloc_result.sleeve_allocations,
            cash_weight=cash_alloc,
            base_equity=BASE_EQUITY,
        )

        # Build sleeve rows - show ALLOCATED NOTIONAL only (attribution)
        # Do NOT show sleeve backtest equity as it's not additive
        rows = []

        if trend_alloc > WEIGHT_TOLERANCE:
            alloc_notional = BASE_EQUITY * trend_alloc
            # Compute sleeve-level return for display (attribution only)
            st_df = _safe_df(st_equity)
            if not st_df.empty and "equity" in st_df.columns:
                st_start = float(st_df["equity"].iloc[0])
                st_last = float(st_df["equity"].iloc[-1])
                st_prev = float(st_df["equity"].iloc[-2]) if len(st_df) > 1 else st_last
                if st_start > 0:
                    st_ret = (st_last / st_start) - 1.0
                    st_day_ret = (st_last - st_prev) / st_prev if st_prev > 0 else 0.0
                    # Attribution: this sleeve's contribution to portfolio
                    contrib_equity = BASE_EQUITY * trend_alloc * (1.0 + st_ret)
                    rows.append({
                        "Sleeve": f"Sleeve Trend — Momentum ({trend_alloc:.0%})",
                        "Allocated": _fmt_money(alloc_notional),
                        "Equity": _fmt_money(contrib_equity),
                        "Day Return": _fmt_pct(st_day_ret),
                    })
                else:
                    rows.append({"Sleeve": f"Sleeve Trend — Momentum ({trend_alloc:.0%})", "Allocated": _fmt_money(alloc_notional), "Equity": "—", "Day Return": "—"})
            else:
                rows.append({"Sleeve": f"Sleeve Trend — Momentum ({trend_alloc:.0%})", "Allocated": _fmt_money(alloc_notional), "Equity": "—", "Day Return": "—"})
        else:
            rows.append({"Sleeve": "Sleeve Trend — Momentum (inactive)", "Allocated": "—", "Equity": "—", "Day Return": "—"})

        if val_alloc > WEIGHT_TOLERANCE:
            alloc_notional = BASE_EQUITY * val_alloc
            s2_df = _safe_df(s2_equity)
            if not s2_df.empty and "equity" in s2_df.columns:
                s2_start = float(s2_df["equity"].iloc[0])
                s2_last = float(s2_df["equity"].iloc[-1])
                s2_prev = float(s2_df["equity"].iloc[-2]) if len(s2_df) > 1 else s2_last
                if s2_start > 0:
                    s2_ret = (s2_last / s2_start) - 1.0
                    s2_day_ret = (s2_last - s2_prev) / s2_prev if s2_prev > 0 else 0.0
                    contrib_equity = BASE_EQUITY * val_alloc * (1.0 + s2_ret)
                    rows.append({
                        "Sleeve": f"Sleeve 2 — Valuation ({val_alloc:.0%})",
                        "Allocated": _fmt_money(alloc_notional),
                        "Equity": _fmt_money(contrib_equity),
                        "Day Return": _fmt_pct(s2_day_ret),
                    })
                else:
                    rows.append({"Sleeve": f"Sleeve 2 — Valuation ({val_alloc:.0%})", "Allocated": _fmt_money(alloc_notional), "Equity": "—", "Day Return": "—"})
            else:
                rows.append({"Sleeve": f"Sleeve 2 — Valuation ({val_alloc:.0%})", "Allocated": _fmt_money(alloc_notional), "Equity": "—", "Day Return": "—"})
        else:
            rows.append({"Sleeve": "Sleeve 2 — Valuation (inactive)", "Allocated": "—", "Equity": "—", "Day Return": "—"})

        # CASH row
        if cash_alloc > WEIGHT_TOLERANCE:
            cash_notional = BASE_EQUITY * cash_alloc
            rows.append({
                "Sleeve": f"CASH ({cash_alloc:.0%})",
                "Allocated": _fmt_money(cash_notional),
                "Equity": _fmt_money(cash_notional),  # Cash doesn't grow
                "Day Return": _fmt_pct(0),
            })

        summary_df = pd.DataFrame(rows)

        # TOTAL row - THE AUTHORITATIVE PORTFOLIO EQUITY
        # Computed from weighted sleeve returns, NOT by summing rows above
        total_row = pd.DataFrame([{
            "Sleeve": f"TOTAL — Portfolio (${BASE_EQUITY:,.0f})",
            "Allocated": _fmt_money(BASE_EQUITY),
            "Equity": _fmt_money(portfolio_stats["equity"]),
            "Day Return": _fmt_pct(portfolio_stats["day_return"]),
        }])

        summary_df = pd.concat([summary_df, total_row], ignore_index=True)

        alloc_summary = allocation_summary_df(alloc_result)
        holdings = holdings_snapshot_df(alloc_result)
        skipped_df = pd.DataFrame(alloc_result.skipped_trades) if alloc_result.skipped_trades else pd.DataFrame()

    else:
        # Legacy static allocation fallback
        # Still compute correctly using weighted returns
        sleeve_equity_map = {"sleeve_trend": st_equity, "sleeve_2": s2_equity}
        static_allocs = {"sleeve_trend": 0.80, "sleeve_2": 0.20}

        portfolio_stats = compute_portfolio_equity(
            sleeve_equity_map=sleeve_equity_map,
            sleeve_allocations=static_allocs,
            cash_weight=0.0,
            base_equity=BASE_EQUITY,
        )

        rows = [
            {"Sleeve": "Sleeve Trend — Momentum (80%)", "Allocated": _fmt_money(BASE_EQUITY * 0.80), "Equity": "—", "Day Return": "—"},
            {"Sleeve": "Sleeve 2 — Valuation (20%)", "Allocated": _fmt_money(BASE_EQUITY * 0.20), "Equity": "—", "Day Return": "—"},
        ]

        total_row = {
            "Sleeve": f"TOTAL — Portfolio (${BASE_EQUITY:,.0f})",
            "Allocated": _fmt_money(BASE_EQUITY),
            "Equity": _fmt_money(portfolio_stats["equity"]),
            "Day Return": _fmt_pct(portfolio_stats["day_return"]),
        }

        summary_df = pd.concat([pd.DataFrame(rows), pd.DataFrame([total_row])], ignore_index=True)
        alloc_summary, holdings, skipped_df = None, None, pd.DataFrame()

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
        <div class="muted">Automated daily report. Portfolio TOTAL computed from weighted sleeve returns (not sum of rows).</div>
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

    # ── Run sleeves ───────────────────────────────────────────────
    try:
        s1_equity, s1_trades = run_sleeve_1()
    except Exception as e:
        print(f"[WARN] Sleeve 1 failed: {e}")
        s1_equity, s1_trades = pd.DataFrame(), pd.DataFrame()

    try:
        st_equity, st_trades, st_signals = run_sleeve_trend()
    except Exception as e:
        print(f"[WARN] Sleeve Trend failed: {e}")
        st_equity, st_trades, st_signals = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    try:
        s2_details = run_sleeve_2()
        s2_equity = s2_details.get("equity_df", pd.DataFrame())
        s2_trades = s2_details.get("trades_df", pd.DataFrame())
    except Exception as e:
        print(f"[WARN] Sleeve 2 failed: {e}")
        s2_details = {}
        s2_equity, s2_trades = pd.DataFrame(), pd.DataFrame()

    # ── Sleeve health checks ─────────────────────────────────────
    # Validate each sleeve BEFORE allocation.  Invalid sleeves get
    # their weight routed to CASH, never to another sleeve.
    trend_valid, trend_reason = _sleeve_is_valid(st_equity)
    s2_valid, s2_reason = _sleeve_is_valid(s2_equity)

    if not trend_valid:
        print(f"sleeve_trend inactive: {trend_reason} -> routed to CASH")
    if not s2_valid:
        print(f"sleeve_2 inactive: {s2_reason} -> routed to CASH")

    # ── Extract sleeve outputs for dynamic allocation ─────────────
    trend_output = extract_sleeve_output(st_equity, st_trades, "sleeve_trend", 1.0)
    val_output = extract_sleeve_output(s2_equity, s2_trades, "sleeve_2", 1.0)

    # ── Run dynamic allocation ────────────────────────────────────
    allocator = PortfolioAllocator()
    alloc_result = allocator.allocate([trend_output, val_output])

    # ── SAFE ALLOCATION POLICY ────────────────────────────────────
    # If a sleeve is invalid, force its allocation to 0 and route
    # the freed weight to CASH (never to another sleeve).
    patched = False
    freed_weight = 0.0

    if not trend_valid and alloc_result.sleeve_allocations.get("sleeve_trend", 0.0) > WEIGHT_TOLERANCE:
        freed_weight += alloc_result.sleeve_allocations["sleeve_trend"]
        alloc_result.sleeve_allocations["sleeve_trend"] = 0.0
        patched = True

    if not s2_valid and alloc_result.sleeve_allocations.get("sleeve_2", 0.0) > WEIGHT_TOLERANCE:
        freed_weight += alloc_result.sleeve_allocations["sleeve_2"]
        alloc_result.sleeve_allocations["sleeve_2"] = 0.0
        patched = True

    if patched:
        alloc_result.cash_weight = alloc_result.cash_weight + freed_weight
        # Re-normalise total_weight (should be 1.0)
        alloc_result.total_weight = (
            sum(alloc_result.sleeve_allocations.values())
            + alloc_result.cash_weight
        )
        print(f"[ALLOCATION] Freed {freed_weight:.1%} from inactive sleeve(s) -> CASH")

    # ── Validate allocation ───────────────────────────────────────
    errors = validate_allocation_result(alloc_result)
    if errors:
        print(f"[WARN] Allocation validation errors: {errors}")

    # ── Log allocation summary ────────────────────────────────────
    print(f"\n[ALLOCATION] Sleeve allocations:")
    for sleeve, pct in alloc_result.sleeve_allocations.items():
        print(f"  {sleeve}: {pct:.1%}")
    print(f"  CASH: {alloc_result.cash_weight:.1%}")
    print(f"  Total weight: {alloc_result.total_weight:.4f}")

    # ── Portfolio stats for model equity ─────────────────────────
    sleeve_equity_map = {
        "sleeve_trend": st_equity,
        "sleeve_2": s2_equity,
    }
    portfolio_stats = compute_portfolio_equity(
        sleeve_equity_map=sleeve_equity_map,
        sleeve_allocations=alloc_result.sleeve_allocations,
        cash_weight=alloc_result.cash_weight,
        base_equity=DEFAULT_PORTFOLIO_BASE_EQUITY,
    )

    # ── Build daily snapshot/email ────────────────────────────────
    report_date = s2_details.get("asof") if s2_details.get("asof") is not None else _asof_date_from_df(st_equity)
    report_date = report_date or pd.Timestamp(dt.date.today())

    daily_snapshot = build_daily_snapshot(
        report_date=report_date,
        alloc_result=alloc_result,
        portfolio_stats=portfolio_stats,
        st_signals=st_signals,
        s2_details=s2_details,
    )
    # --- Paper trading execution (real-world cadence) ---
    # Signals are generated "after close" for report_date.
    # Paper execution confirms trades for report_date using PRIOR business day's signals.
    trade_date_str = report_date.strftime("%Y-%m-%d")
    signal_date_str = prev_trading_day(trade_date_str)
    signals_path_exec = os.path.join("signals", f"{signal_date_str}.json")

    paper_summary = None
    paper_html = ""

    if os.path.exists(signals_path_exec):
        try:
            paper_summary = run_paper_day(
                run_date=trade_date_str,
                signals_path=signals_path_exec,
                ledger_path="paper/ledger.csv",
                trades_path="paper/trades.csv",
                config_path="paper/config_paper.json",
            )
            paper_html = build_paper_report_html(
                run_date=trade_date_str,
                ledger_path="paper/ledger.csv",
                trades_path="paper/trades.csv",
                benchmark_ticker="SPY",
            )
            print(f"[PAPER] Executed paper trading for {trade_date_str} using signals {signals_path_exec}")
        except Exception as e:
            print(f"[PAPER][WARN] Paper execution failed: {repr(e)}")
    else:
        print(f"[PAPER][WARN] Missing signals for execution: {signals_path_exec}")

    # --- Paper trading execution + report append (real-world workflow) ---
    trade_date_str = report_date.strftime("%Y-%m-%d")
    signal_date_str = prev_trading_day(trade_date_str)
    signals_exec_path = os.path.join("signals", f"{signal_date_str}.json")

    paper_summary = None
    paper_html = ""

    if os.path.exists(signals_exec_path):
        try:
            paper_summary = run_paper_day(
                run_date=trade_date_str,
                signals_path=signals_exec_path,
                ledger_path="paper/ledger.csv",
                trades_path="paper/trades.csv",
                config_path="paper/config_paper.json",
                force=False,  # never force in production email runs
            )
            paper_html = build_paper_report_html(
                run_date=trade_date_str,
                ledger_path="paper/ledger.csv",
                trades_path="paper/trades.csv",
                benchmark_ticker="SPY",
            )
            print(f"[PAPER] Paper execution complete for {trade_date_str} using signals {signals_exec_path}")
        except Exception as e:
            print(f"[PAPER][WARN] Paper execution failed: {repr(e)}")
    else:
        print(f"[PAPER][WARN] Missing signals for paper execution: {signals_exec_path}")

    
    subject, email_body = create_trade_email(daily_snapshot)
    email_path = os.path.join(OUTPUT_DIR, f"trade_rundown_{today}.txt")
    with open(email_path, "w", encoding="utf-8") as f:
        f.write(email_body)
    print(f"[OK] Daily trade email written: {email_path}")

    if paper_summary:
        email_body += (
            f"\n\n---\nPaper Trading Execution ({paper_summary['date']}): "
            f"equity=${paper_summary['total_equity']:.2f}, "
            f"cash=${paper_summary['cash']:.2f}, "
            f"trades={paper_summary['num_trades']}, "
            f"turnover=${paper_summary['turnover_notional']:.2f}\n"
    )


    # ── Build report ──────────────────────────────────────────────
    html = build_html_report(today, st_equity, st_trades, s2_equity, s2_trades, alloc_result)
    if paper_html:
     html = html + "<hr/>" + paper_html


    out_path = os.path.join(OUTPUT_DIR, f"quant_report_{today}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[OK] HTML report written: {out_path}")

    print("\n[EMAIL PREVIEW]\n")
    print(email_body)

    if send_email:
        try:
            send_email(subject=subject, body_html=html, body_text=email_body)
            print("[OK] Email sent")
        except Exception as e:
            print(f"[WARN] Email not sent: {e}")
    else:
        print("[WARN] send_email not found — HTML generated only")


if __name__ == "__main__":
    main()
