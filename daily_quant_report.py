import datetime as dt
import logging
import os
import sys

import pandas as pd
from paper.signals_io import write_signals_snapshot
from paper.paper_broker import run_paper_day
from paper.paper_report import build_paper_report_html

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
s2_run_backtest_details = (
    getattr(s2_mod, "run_backtest_with_details", None) if s2_mod else None
)
s2_prepare_data = getattr(s2_mod, "prepare_data", None) if s2_mod else None
s2_backtest = getattr(s2_mod, "backtest", None) if s2_mod else None
# ============================================================
# Email sender (exact repo-aware lookup)
# ============================================================
try:
    from core.quant_report import send_email
except Exception:
    send_email = None
# ============================================================
# Portfolio allocation (dynamic)
# ============================================================
from core.portfolio_alloc import (  # noqa: E402
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
from core.quant_report import (  # noqa: E402
    download_prices,
    add_atr,
)
from core.alpha_attribution import (  # noqa: E402
    calc_alpha_stats,
    load_benchmark_prices,
)
from engine.backtest_engine import (  # noqa: E402
    infer_latest_entries,
    attach_entry_prices,
)
from sleeves.sleeve_2.config import (  # noqa: E402
    STOP_ATR_MULT,
    TAKE_PROFIT_ATR_MULT,
    STOP_PCT,
    TAKE_PROFIT_PCT,
    LONG_THRESHOLD as S2_LONG_THRESHOLD,
    Z_EXTREME_SHORT,
)

logger = logging.getLogger(__name__)
# ============================================================
# Output config
# ============================================================
OUTPUT_DIR = "outputs/daily"
DATE_FORMAT = os.getenv("DATE_FORMAT", "US")
DISPLAY_DECIMALS = int(os.getenv("DISPLAY_DECIMALS", "2"))


# ============================================================
# Helpers
# ============================================================
def _safe_df(df):
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()


def _fmt_money(x):
    if isinstance(x, str) and x.strip().startswith("$"):
        return x
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return "n/a"


def _fmt_pct(x):
    if isinstance(x, str) and "%" in x:
        return x
    try:
        return f"{100 * float(x):.{DISPLAY_DECIMALS}f}%"
    except Exception:
        return "n/a"


def _fmt_number(x):
    if isinstance(x, str):
        return x
    try:
        return f"{float(x):,.{DISPLAY_DECIMALS}f}"
    except Exception:
        return "n/a"


def _fmt_date(value) -> str:
    try:
        dt_value = pd.to_datetime(value)
        if DATE_FORMAT.upper() == "US":
            return dt_value.strftime("%m/%d/%Y")
        return dt_value.strftime("%Y-%m-%d")
    except Exception:
        return "n/a"


def _asof_date_from_df(df: pd.DataFrame) -> pd.Timestamp | None:
    if df is None or df.empty:
        return None
    if "date" in df.columns:
        return pd.to_datetime(df["date"]).max()
    return None


def _format_text_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "(none)"

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    def _fmt_row(cells: list[str]) -> str:
        return " | ".join(
            str(cell).ljust(col_widths[i]) for i, cell in enumerate(cells)
        )

    lines = [_fmt_row(headers), "-+-".join("-" * w for w in col_widths)]
    for row in rows:
        lines.append(_fmt_row(row))
    return "\n".join(lines)


def create_pm_first_trade_email(snapshot: dict) -> tuple[str, str]:
    asof = snapshot.get("asof")
    asof_str = _fmt_date(asof) if asof is not None else "n/a"
    subject = f"Daily Trade Rundown — {asof_str}"

    allocations = snapshot.get("allocations", {})
    cash_pct = allocations.get("cash", 0.0)
    risk_on_pct = allocations.get("risk_on", 1.0 - cash_pct)
    sleeve_splits = allocations.get("sleeves", {})
    cash_reason = allocations.get("cash_reason")


    lines = []
    lines.append(subject)

    lines.append("")
    lines.append("1) Trades for Today (NEW ORDERS)")
    orders = snapshot.get("orders", [])
    if not orders:
        lines.append("   - None")
    else:
        for order in orders:
            action = order.get("action", "")
            ticker = order.get("ticker", "")
            weight = order.get("target_weight", 0.0)
            exec_px = order.get("execution_price")
            reason = order.get("reason")
            notional = order.get("notional")
            shares = order.get("shares")
            line = f"   - {action} {ticker} | target={_fmt_pct(weight)} | exp_px={_fmt_money(exec_px)}"
            if shares is not None:
                line += f" | est_shares={shares}"
            if notional is not None:
                line += f" | est_notional={_fmt_money(notional)}"
            if reason:
                line += f" | reason={reason}"
            lines.append(line)

    lines.append("")
    lines.append("2) Performance Summary (Portfolio)")
    perf = snapshot.get("performance_summary", {})
    if not perf:
        lines.append("   - None")
    else:
        lines.append(
            f"   - Total Return (Since Inception): {_fmt_pct(perf.get('total_return'))}"
        )
        lines.append(f"   - Week-to-Date: {_fmt_pct(perf.get('wtd'))}")
        lines.append(f"   - Month-to-Date: {_fmt_pct(perf.get('mtd'))}")
        lines.append(f"   - Year-to-Date: {_fmt_pct(perf.get('ytd'))}")

    lines.append("")
    lines.append("3) Current Holdings (LIVE BOOK)")
    holdings = snapshot.get("holdings", [])
    headers = [
        "Ticker",
        "Direction",
        "Entry Date",
        "Entry Price",
        "Last Price",
        "P&L ($)",
        "P&L (%)",
        "Days Held",
    ]
    rows = []
    for h in holdings:
        rows.append(
            [
                h.get("ticker", ""),
                h.get("direction", ""),
                _fmt_date(h.get("entry_date", "")),
                _fmt_money(h.get("entry_price")),
                _fmt_money(h.get("last_price")),
                _fmt_money(h.get("pnl_dollars")),
                _fmt_pct(h.get("pnl_pct")),
                str(h.get("days_held", "")),
            ]
        )
    lines.append(_format_text_table(headers, rows))

    lines.append("")
    lines.append("4) Trades Not Taken (Skipped Trades / Constraints)")
    skipped = snapshot.get("skipped_trades", [])
    if not skipped:
        lines.append("   - None")
    else:
        for skipped_trade in skipped:
            ticker = skipped_trade.get("ticker", "")
            action = skipped_trade.get("action", "")
            reason = skipped_trade.get("reason", "")
            details = []
            if "original_weight" in skipped_trade:
                details.append(
                    f"original={_fmt_pct(skipped_trade.get('original_weight'))}"
                )
            if "capped_weight" in skipped_trade:
                details.append(
                    f"capped={_fmt_pct(skipped_trade.get('capped_weight'))}"
                )
            if "limited_weight" in skipped_trade:
                details.append(
                    f"limited={_fmt_pct(skipped_trade.get('limited_weight'))}"
                )
            if "excess" in skipped_trade:
                details.append(f"excess={_fmt_pct(skipped_trade.get('excess'))}")
            detail_str = f" | {' | '.join(details)}" if details else ""
            lines.append(
                f"   - {ticker} | action={action}{detail_str} | reason={reason}"
            )

    lines.append("")
    lines.append("5) Summary / allocation")
    lines.append(f"   - Risk-on: {_fmt_pct(risk_on_pct)}")
    lines.append(f"   - Cash: {_fmt_pct(cash_pct)}")

    if cash_pct > WEIGHT_TOLERANCE and cash_reason:
        lines.append(f"   - Cash rationale: {cash_reason}")

    for sleeve, pct in sleeve_splits.items():
        lines.append(f"   - {sleeve}: {_fmt_pct(pct)}")
    
    # Map risk levels by ticker so Proposed Trades can show entry/stop/take
    risk_levels = snapshot.get("risk_levels", []) or []
    risk_map = {r.get("ticker"): r for r in risk_levels if r.get("ticker")}

    lines.append("")
    lines.append("6) Proposed Trades / Next Rebalance")
    proposed = snapshot.get("proposed_trades", [])
    if not proposed:
        lines.append("   - No proposed trades.")
    else:
            for trade in proposed:
              line = (
                  f"   - {trade.get('action', '')} {trade.get('ticker', '')} | sleeve={trade.get('sleeve', '')} "
                  f"| current={_fmt_pct(trade.get('current_weight'))} | target={_fmt_pct(trade.get('target_weight'))} "
                  f"| delta={_fmt_pct(trade.get('delta_weight'))}"
              )
              if trade.get("est_shares") is not None:
                  line += f" | est_shares={trade.get('est_shares')}"
              if trade.get("est_notional") is not None:
                  line += f" | est_notional={_fmt_money(trade.get('est_notional'))}"

              r = risk_map.get(trade.get("ticker", ""), {}) if risk_map else {}
              line += (
                  f" | entry={_fmt_money(r.get('entry_price'))}"
                  f" | target={_fmt_money(r.get('take_profit'))}"
                  f" | exit={_fmt_money(r.get('stop_loss'))}"
              )

              lines.append(line)

    lines.append("")
    lines.append("7) Sleeve allocation summary")
    lines.append(f"   - Risk-on: {_fmt_pct(risk_on_pct)}")
    lines.append(f"   - Cash: {_fmt_pct(cash_pct)}")
    for sleeve, pct in sleeve_splits.items():
        lines.append(f"   - {sleeve}: {_fmt_pct(pct)}")

    lines.append("")
    lines.append("8) Risk & exit levels")
    risk_levels = snapshot.get("risk_levels", [])
    if not risk_levels:
        lines.append("   - None")
    else:
        for level in risk_levels:
            lines.append(
                "   - {ticker} | entry={entry} | stop={stop} | take={take}".format(
                    ticker=level.get("ticker", ""),
                    entry=_fmt_money(level.get("entry_price")),
                    stop=_fmt_money(level.get("stop_loss")),
                    take=_fmt_money(level.get("take_profit")),
                )
            )

    lines.append("")
    lines.append("9) Alpha attribution vs benchmark")
    alpha = snapshot.get("alpha_attribution")

    def _fmt_float(value: float | None) -> str:
        try:
            return f"{float(value):.2f}"
        except Exception:
            return "n/a"

    if not alpha or alpha.get("n_days", 0) < 20:
        lines.append(
            "   - Alpha attribution unavailable (insufficient data or benchmark fetch failed)."
        )
    else:
        lines.append(
            "   - Since inception: Port {port}, SPY {bench}, Excess {excess}".format(
                port=_fmt_pct(alpha.get("port_cum_return")),
                bench=_fmt_pct(alpha.get("bench_cum_return")),
                excess=_fmt_pct(alpha.get("excess_cum_return")),
            )
        )
        lines.append(
            "   - Beta (63d): {beta}, Alpha (ann., 63d): {alpha}".format(
                beta=_fmt_float(alpha.get("beta_63d")),
                alpha=_fmt_pct(alpha.get("alpha_ann_63d")),
            )
        )
        lines.append(
            "   - Tracking Error (ann.): {te}, Information Ratio: {ir}".format(
                te=_fmt_pct(alpha.get("tracking_error_ann")),
                ir=_fmt_float(alpha.get("info_ratio")),
            )
        )
        lines.append(
            "   - Max Drawdown: Port {port}, SPY {bench}".format(
                port=_fmt_pct(alpha.get("mdd_port")),
                bench=_fmt_pct(alpha.get("mdd_bench")),
            )
        )
        lines.append(f"   - n_days used: {alpha.get('n_days', 0)}")

    lines.append("")
    lines.append("10) Recent trades history")
    lines.append("   - See Proposed Trades / Next Rebalance above.")

    lines.append("")
    lines.append("11) Equity curves and diagnostics")
    recon = snapshot.get("reconciliation", {})
    lines.append("   Account Reconciliation (MODEL vs BROKER)")
    lines.append(
        f"   - Model Starting Equity: {_fmt_money(recon.get('model_start_equity'))}"
    )
    lines.append(
        f"   - Model Current Equity: {_fmt_money(recon.get('model_current_equity'))}"
    )
    lines.append(
        f"   - Broker Current Equity: {_fmt_money(recon.get('broker_equity'))}"
    )
    lines.append(f"   - Difference: {_fmt_money(recon.get('difference'))}")
    if recon.get("note"):
        lines.append(f"   - Note: {recon.get('note')}")

    watchlist = snapshot.get("watchlist", [])
    lines.append("   Watchlist (NO TRADES YET)")
    if not watchlist:
        lines.append("   - None")
    else:
        for item in watchlist:
            lines.append(f"   - {item.get('ticker', '')}: {item.get('reason', '')}")

    return subject, "\n".join(lines)


def _equity_series_from_df(df: pd.DataFrame) -> pd.Series:
    df = _safe_df(df)
    if df.empty or "equity" not in df.columns:
        return pd.Series(dtype=float)
    series = pd.Series(
        df["equity"].values,
        index=pd.to_datetime(df["date"]) if "date" in df.columns else df.index,
    )
    return series.dropna().sort_index()


def _series_date_range(series: pd.Series) -> str:
    series = pd.Series(series).dropna()
    if series.empty:
        return "empty"
    idx = pd.to_datetime(series.index)
    return f"{idx.min().date()} -> {idx.max().date()}"


def _load_equity_history(path: str) -> pd.Series:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return pd.Series(dtype=float)
    df = pd.read_csv(path)
    if df.empty or "date" not in df.columns or "equity" not in df.columns:
        return pd.Series(dtype=float)
    df["date"] = pd.to_datetime(df["date"])
    return pd.Series(df["equity"].values, index=df["date"]).dropna().sort_index()


def _load_portfolio_fixture(path: str) -> pd.Series:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return pd.Series(dtype=float)
    df = pd.read_csv(path)
    if df.empty or "date" not in df.columns or "equity" not in df.columns:
        return pd.Series(dtype=float)
    df["date"] = pd.to_datetime(df["date"])
    return pd.Series(df["equity"].values, index=df["date"]).dropna().sort_index()


def _append_equity_history(
    path: str, report_date: pd.Timestamp, equity: float
) -> pd.Series:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    report_date = pd.to_datetime(report_date).normalize()
    rows = []
    if os.path.exists(path) and os.path.getsize(path) > 0:
        df = pd.read_csv(path)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"]).dt.normalize()
            rows = df.to_dict(orient="records")
            if (df["date"] == report_date).any():
                return (
                    pd.Series(df["equity"].values, index=df["date"])
                    .dropna()
                    .sort_index()
                )
    rows.append({"date": report_date.strftime("%Y-%m-%d"), "equity": float(equity)})
    out_df = pd.DataFrame(rows)
    out_df.to_csv(path, index=False)
    out_df["date"] = pd.to_datetime(out_df["date"])
    return (
        pd.Series(out_df["equity"].values, index=out_df["date"]).dropna().sort_index()
    )


def compute_portfolio_equity_series(
    sleeve_equity_map: dict[str, pd.DataFrame],
    sleeve_allocations: dict[str, float],
    cash_weight: float,
    base_equity: float = DEFAULT_PORTFOLIO_BASE_EQUITY,
) -> pd.Series:
    returns_map = {}
    for sleeve_name, alloc in sleeve_allocations.items():
        if alloc <= WEIGHT_TOLERANCE:
            continue
        series = _equity_series_from_df(
            sleeve_equity_map.get(sleeve_name, pd.DataFrame())
        )
        if series.empty:
            continue
        returns_map[sleeve_name] = series.pct_change(fill_method=None).dropna()
    if not returns_map:
        return pd.Series(dtype=float)
    aligned = pd.concat(returns_map, axis=1, join="inner").dropna()
    if aligned.empty:
        return pd.Series(dtype=float)
    weights = pd.Series({k: sleeve_allocations.get(k, 0.0) for k in aligned.columns})
    portfolio_ret = aligned.mul(weights, axis=1).sum(axis=1)
    portfolio_equity = base_equity * (1.0 + portfolio_ret).cumprod()
    if cash_weight > WEIGHT_TOLERANCE:
        portfolio_equity = portfolio_equity * (1.0 + 0.0)
    return portfolio_equity


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
            atr_map[ticker] = (
                float(past["atr"].iloc[-1]) if pd.notna(past["atr"].iloc[-1]) else None
            )
    return atr_map


def _format_df_for_email(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in df.columns:
        col_lower = str(col).lower()
        if "%" in col_lower or "pct" in col_lower or "percent" in col_lower:
            df[col] = df[col].apply(_fmt_pct)
        elif "date" in col_lower:
            df[col] = df[col].apply(_fmt_date)
        elif any(
            key in col_lower
            for key in [
                "p&l",
                "pnl",
                "equity",
                "notional",
                "allocated",
                "price",
                "value",
                "capital",
                "cash",
                "difference",
                "model",
                "broker",
                "amount",
            ]
        ):
            df[col] = df[col].apply(_fmt_money)
        elif any(
            key in col_lower
            for key in ["return", "weight", "delta", "change", "gross", "net"]
        ):
            df[col] = df[col].apply(_fmt_pct)
        elif "shares" in col_lower:
            df[col] = df[col].apply(_fmt_number)
    return df


def html_table(
    df: pd.DataFrame, title: str, max_rows: int = 25, empty_message: str = "No data"
) -> str:
    df = _safe_df(df).copy()
    df = df.where(pd.notnull(df), "—")
    if df.empty:
        return f"<h3>{title}</h3><p><em>{empty_message}</em></p>"
    df = _format_df_for_email(df)
    return f"<h3>{title}</h3>" + df.head(max_rows).to_html(
        index=False, border=0, classes="tbl", justify="left"
    )


def filter_sleeve2_cash_proxy(trades: pd.DataFrame) -> pd.DataFrame:
    """Remove SGOV 'cash_proxy_fund_entries' rows from trades."""
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
    logger.info("[SLEEVE 1] Preparing data...")
    signals = s1_prepare_data()
    logger.info("[SLEEVE 1] Running backtest...")
    return s1_backtest(signals)


def run_sleeve_trend():
    logger.info("[SLEEVE TREND] Preparing data...")
    signals = st_prepare_data()
    logger.info("[SLEEVE TREND] Running backtest...")
    equity_df, trades_df = st_backtest(signals)
    return equity_df, trades_df, signals


def run_sleeve_2():
    if s2_run_backtest_details is not None:
        logger.info("[SLEEVE 2] Running run_backtest_with_details()...")
        return s2_run_backtest_details(period="1y", interval="1d")
    if s2_run_backtest is not None:
        logger.info("[SLEEVE 2] Running run_backtest()...")
        equity_df, trades_df = s2_run_backtest(period="1y", interval="1d")
        return {"equity_df": equity_df, "trades_df": trades_df}
    if s2_prepare_data is not None and s2_backtest is not None:
        logger.info("[SLEEVE 2] Preparing data...")
        signals = s2_prepare_data()
        logger.info("[SLEEVE 2] Running backtest...")
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
    target_weights: pd.DataFrame | None = None,
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
    latest_equity = (
        float(equity_df["equity"].iloc[-1])
        if "equity" in equity_df.columns
        else 10000.0
    )
    start_equity = (
        float(equity_df["equity"].iloc[0]) if "equity" in equity_df.columns else 10000.0
    )
    if not trades_df.empty and "ticker" in trades_df.columns:
        real_trades = trades_df.copy()
        if "reason_exit" in real_trades.columns:
            real_trades = real_trades[
                ~(
                    (real_trades.get("ticker", "") == "SGOV")
                    & (real_trades.get("reason_exit", "") == "cash_proxy_fund_entries")
                )
            ].copy()
        if not real_trades.empty and "entry_date" in real_trades.columns:
            real_trades["entry_date"] = pd.to_datetime(
                real_trades["entry_date"], errors="coerce"
            )
            latest_trades = real_trades.nlargest(5, "entry_date")
            for _, row in latest_trades.iterrows():
                ticker = row.get("ticker", "")
                shares = row.get("shares", 0)
                entry_price = row.get("entry_price", 0)
                if ticker and shares > 0 and entry_price > 0:
                    notional = shares * entry_price
                    weight = notional / latest_equity if latest_equity > 0 else 0
                    positions.append(
                        {
                            "ticker": ticker,
                            "target_weight": weight,
                            "reason": row.get("reason_exit", "signal"),
                            "signal_strength": 1.0,
                        }
                    )
        # Also handle engine-style trades (which have "date" + "weight_to" instead
        # of "entry_date" + "shares").  This allows Sleeve 2 engine trades to
        # register as active positions for allocation purposes.
        if (
            not real_trades.empty
            and "weight_to" in real_trades.columns
            and not positions
        ):
            real_trades_sorted = real_trades.copy()
            if "date" in real_trades_sorted.columns:
                real_trades_sorted["date"] = pd.to_datetime(
                    real_trades_sorted["date"], errors="coerce"
                )
                real_trades_sorted = real_trades_sorted.sort_values(
                    "date", ascending=False
                )
            latest_engine_trades = real_trades_sorted.head(5)
            for _, row in latest_engine_trades.iterrows():
                ticker = row.get("ticker", "")
                w = abs(row.get("weight_to", 0.0))
                if ticker and w > 1e-6:
                    positions.append(
                        {
                            "ticker": ticker,
                            "target_weight": w,
                            "reason": "engine_signal",
                            "signal_strength": 1.0,
                        }
                    )

    if not positions and target_weights is not None and not target_weights.empty:
        target_last = target_weights.iloc[-1]
        for ticker, weight in target_last.items():
            if abs(weight) > WEIGHT_TOLERANCE:
                positions.append(
                    {
                        "ticker": ticker,
                        "target_weight": float(weight),
                        "reason": "target_weights",
                        "signal_strength": 1.0,
                    }
                )

    is_active = len(positions) > 0
    if is_active and start_equity > 0:
        sleeve_return = (latest_equity / start_equity) - 1.0
        strength = min(1.0, base_strength * max(0.5, min(1.5, 1.0 + sleeve_return)))
    else:
        strength = 0.0
    notes = (
        f"Active: {len(positions)} positions, equity ${latest_equity:,.0f}"
        if is_active
        else "Inactive"
    )
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


def resolve_portfolio_equity_series(
    sleeve_equity_map: dict[str, pd.DataFrame],
    alloc_result: AllocationResult,
    report_date: pd.Timestamp,
    portfolio_equity: float,
    offline_fixture: bool,
    base_equity: float = DEFAULT_PORTFOLIO_BASE_EQUITY,
    portfolio_stats: dict | None = None,
    st_equity: pd.DataFrame | None = None,
    s2_equity: pd.DataFrame | None = None,
    st_signals: pd.DataFrame | None = None,
    s2_details: dict | None = None,
) -> pd.Series:
    history_path = os.path.join(OUTPUT_DIR, "equity_history.csv")
    history_series = _load_equity_history(history_path)
    if not history_series.empty:
        return history_series

    ledger_path = os.path.join("paper", "ledger.csv")
    if os.path.exists(ledger_path) and os.path.getsize(ledger_path) > 0:
        ledger = pd.read_csv(ledger_path)
        if (
            not ledger.empty
            and "date" in ledger.columns
            and "total_equity" in ledger.columns
        ):
            ledger["date"] = pd.to_datetime(ledger["date"])
            ledger = ledger.sort_values("date")
            ledger_daily = ledger.groupby("date", as_index=True)["total_equity"].last()
            ledger_series = ledger_daily.dropna().sort_index()
            if not ledger_series.empty:
                return ledger_series

    derived = compute_portfolio_equity_series(
        sleeve_equity_map=sleeve_equity_map,
        sleeve_allocations=alloc_result.sleeve_allocations,
        cash_weight=alloc_result.cash_weight,
        base_equity=base_equity,
    )
    if not derived.empty:
        return derived

    if offline_fixture:
        return pd.Series(dtype=float)

    return _append_equity_history(history_path, report_date, portfolio_equity)


def compute_portfolio_equity_df(
    sleeve_equity_map: dict[str, pd.DataFrame],
    sleeve_allocations: dict[str, float],
    base_equity: float,
) -> pd.DataFrame:
    """Compute portfolio equity series from sleeve equity curves and allocations."""
    series = []
    for sleeve_name, alloc in sleeve_allocations.items():
        if alloc <= WEIGHT_TOLERANCE:
            continue
        equity_df = _safe_df(sleeve_equity_map.get(sleeve_name, pd.DataFrame()))
        if equity_df.empty or "equity" not in equity_df.columns:
            continue
        df = equity_df.copy()
        df["date"] = pd.to_datetime(df.get("date", df.index))
        df = df.sort_values("date")
        start = float(df["equity"].iloc[0])
        if start <= 0:
            continue
        df["return"] = (df["equity"] / start) - 1.0
        series.append(
            df[["date", "return"]].rename(columns={"return": f"{sleeve_name}_return"})
        )

    if not series:
        return pd.DataFrame(columns=["date", "portfolio_equity"])

    merged = series[0]
    for s in series[1:]:
        merged = merged.merge(s, on="date", how="outer")

    merged = merged.sort_values("date").reset_index(drop=True)
    for col in merged.columns:
        if col.endswith("_return"):
            merged[col] = merged[col].ffill().fillna(0.0)

    merged["portfolio_return"] = 0.0
    for sleeve_name, alloc in sleeve_allocations.items():
        col = f"{sleeve_name}_return"
        if col in merged.columns:
            merged["portfolio_return"] += alloc * merged[col]

    merged["portfolio_equity"] = base_equity * (1.0 + merged["portfolio_return"])
    return merged[["date", "portfolio_equity"]]


def compute_performance_summary(
    report_date: pd.Timestamp,
    sleeve_equity_map: dict[str, pd.DataFrame],
    sleeve_allocations: dict[str, float],
    base_equity: float,
) -> dict:
    series = compute_portfolio_equity_df(
        sleeve_equity_map=sleeve_equity_map,
        sleeve_allocations=sleeve_allocations,
        base_equity=base_equity,
    )
    if series.empty:
        return {
            "total_return": None,
            "wtd": None,
            "mtd": None,
            "ytd": None,
        }

    series["date"] = pd.to_datetime(series["date"])
    series = series.sort_values("date")

    def _equity_asof(target_date: pd.Timestamp) -> float:
        eligible = series[series["date"] <= target_date]
        if eligible.empty:
            return float(series["portfolio_equity"].iloc[0])
        return float(eligible["portfolio_equity"].iloc[-1])

    asof_date = pd.to_datetime(report_date)
    asof_equity = _equity_asof(asof_date)
    inception_equity = float(series["portfolio_equity"].iloc[0])

    week_start = (asof_date - pd.Timedelta(days=asof_date.weekday())).normalize()
    month_start = asof_date.replace(day=1).normalize()
    year_start = asof_date.replace(month=1, day=1).normalize()

    wtd_equity = _equity_asof(week_start)
    mtd_equity = _equity_asof(month_start)
    ytd_equity = _equity_asof(year_start)

    return {
        "total_return": (
            (asof_equity / inception_equity) - 1.0 if inception_equity > 0 else None
        ),
        "wtd": (asof_equity / wtd_equity) - 1.0 if wtd_equity > 0 else None,
        "mtd": (asof_equity / mtd_equity) - 1.0 if mtd_equity > 0 else None,
        "ytd": (asof_equity / ytd_equity) - 1.0 if ytd_equity > 0 else None,
    }


def build_proposed_trades(
    alloc_result: AllocationResult,
    previous_weights: pd.DataFrame | None,
    price_map: dict,
    model_equity: float,
) -> list[dict]:
    weights_df = _safe_df(alloc_result.combined_weights)
    weights_df = weights_df[weights_df["ticker"] != CASH_TICKER].copy()
    if weights_df.empty:
        return []

    prev_map = {}
    if previous_weights is not None and not previous_weights.empty:
        prev_map = previous_weights.set_index("ticker")["target_weight"].to_dict()

    proposed = []
    for _, row in weights_df.iterrows():
        ticker = row["ticker"]
        target_weight = float(row["target_weight"])
        current_weight = float(prev_map.get(ticker, target_weight))
        delta_weight = target_weight - current_weight

        if abs(delta_weight) <= WEIGHT_TOLERANCE:
            continue

        action = "HOLD"
        if (
            abs(current_weight) <= WEIGHT_TOLERANCE
            and abs(target_weight) > WEIGHT_TOLERANCE
        ):
            action = "BUY" if target_weight > 0 else "SELL"
        elif (
            abs(target_weight) <= WEIGHT_TOLERANCE
            and abs(current_weight) > WEIGHT_TOLERANCE
        ):
            action = "SELL"
        elif current_weight * target_weight >= 0:
            action = (
                "INCREASE" if abs(target_weight) > abs(current_weight) else "DECREASE"
            )
        else:
            action = "SELL"

        exec_px = _compute_execution_price(price_map, ticker)
        shares = None
        notional = None
        if exec_px and model_equity > 0:
            notional = abs(delta_weight) * model_equity
            shares = round(notional / exec_px, 2) if exec_px > 0 else None

        proposed.append(
            {
                "ticker": ticker,
                "action": action,
                "sleeve": row.get("sleeve_name", ""),
                "current_weight": current_weight,
                "target_weight": target_weight,
                "delta_weight": delta_weight,
                "est_shares": shares,
                "est_notional": notional,
            }
        )

    return proposed


def derive_actual_sleeve_allocations(
    alloc_result: AllocationResult,
) -> dict[str, float]:
    allocations = {name: 0.0 for name in alloc_result.sleeve_allocations.keys()}
    weights_df = _safe_df(alloc_result.combined_weights)
    if weights_df.empty:
        return allocations

    weights_df = weights_df[weights_df["ticker"] != CASH_TICKER].copy()
    for _, row in weights_df.iterrows():
        sleeve_names = str(row.get("sleeve_name", "")).split(",")
        sleeve_names = [name.strip() for name in sleeve_names if name.strip()]
        if not sleeve_names:
            continue
        share = float(row.get("target_weight", 0.0)) / len(sleeve_names)
        for name in sleeve_names:
            allocations[name] = allocations.get(name, 0.0) + share
    return allocations


# ============================================================
# Daily snapshot builder
# ============================================================
def build_daily_snapshot(
    report_date: pd.Timestamp,
    alloc_result: AllocationResult,
    portfolio_stats: dict,
    st_equity: pd.DataFrame,
    s2_equity: pd.DataFrame,
    st_signals: pd.DataFrame,
    s2_details: dict,
) -> dict:
    weights_df = _safe_df(alloc_result.combined_weights)
    weights_df = weights_df[weights_df["ticker"] != CASH_TICKER].copy()
    weights_df = weights_df[weights_df["target_weight"].abs() > WEIGHT_TOLERANCE]
    tickers = (
        sorted(weights_df["ticker"].unique().tolist()) if not weights_df.empty else []
    )
    prices = pd.DataFrame()
    if tickers:
        prices = download_prices(tickers, period="6mo", interval="1d")
        if prices.empty or prices[["open", "high", "low", "close"]].isna().all().all():
            logger.error(
                "Downloaded price data is empty or all-NaN; aborting snapshot generation."
            )
            raise RuntimeError(
                "Downloaded price data is empty or all-NaN; aborting snapshot generation."
            )
    # --- Paper trading signals snapshot (daily immutable file) ---
    # Prefer a YYYY-MM-DD string you already use in the report.
    # If you already have something like report_date_str / asof_date_str / today_str, use that here.
    signals_path = None
    if weights_df.empty:
        logger.warning(
            "[PAPER] No target weights available; skipping signals snapshot."
        )
    else:
        run_date_str = report_date.strftime("%Y-%m-%d")
        signals_path = write_signals_snapshot(
            df_targets=weights_df,
            run_date=run_date_str,
            out_dir="signals",
            sleeve_col="sleeve",  # if column exists; otherwise writer will default to "core"
        )
        logger.info("[PAPER] Wrote signals snapshot: %s", signals_path)
    price_map = _build_price_map(prices, report_date)
    atr_map = _build_atr_map(prices, report_date)
    entry_map = {}
    if (
        s2_details
        and s2_details.get("weights_df") is not None
        and not s2_details.get("weights_df").empty
    ):
        weights_history = s2_details.get("weights_df")
        prices_wide = s2_details.get("prices_wide")
        entries = infer_latest_entries(weights_history)
        entries = attach_entry_prices(entries, prices_wide)
        for _, row in entries.iterrows():
            entry_map[row["ticker"]] = {
                "entry_date": _fmt_date(pd.to_datetime(row["entry_date"])),
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
        entry_date = (
            entry_info.get("entry_date")
            if entry_info
            else report_date.strftime("%Y-%m-%d")
        )

        entry_date = (
            entry_info.get("entry_date") if entry_info else _fmt_date(report_date)
        )
        entry_px = (
            entry_info.get("entry_price")
            if entry_info and entry_info.get("entry_price")
            else last_px
        )
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
        days_held = (
            (report_date - pd.to_datetime(entry_date)).days if entry_date else None
        )

        days_held = (
            (report_date - pd.to_datetime(entry_date)).days
            if entry_date and entry_date != "n/a"
            else None
        )

        holdings.append(
            {
                "ticker": ticker,
                "direction": direction,
                "entry_date": entry_date,
                "entry_price": entry_px,
                "last_price": last_px,
                "pnl_dollars": pnl_dollars,
                "pnl_pct": pnl_pct,
                "days_held": days_held,
            }
        )
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
        risk_levels.append(
            {
                "ticker": ticker,
                "entry_price": entry_px,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
            }
        )
    # Trades for today
    prev_weights = {}
    if (
        s2_details
        and s2_details.get("weights_df") is not None
        and not s2_details.get("weights_df").empty
    ):
        hist = s2_details.get("weights_df")
        if len(hist) > 1:
            prev_row = hist.iloc[-2]
            prev_weights = prev_row.to_dict()
    new_weights = {
        row["ticker"]: float(row["target_weight"]) for _, row in weights_df.iterrows()
    }
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
            orders.append(
                {
                    "action": action,
                    "ticker": ticker,
                    "target_weight": weight,
                    "execution_price": exec_px,
                    "shares": shares,
                    "notional": notional,
                }
            )

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
        near_long = st_latest[
            (st_latest["signal_long"])
            & (st_latest["delta_long"] >= 0)
            & (st_latest["delta_long"] <= 5)
        ]
        near_short = st_latest[
            (st_latest["signal_short"])
            & (st_latest["delta_short"] >= 0)
            & (st_latest["delta_short"] <= 5)
        ]
        for _, row in pd.concat([near_long, near_short]).head(5).iterrows():
            threshold = (
                trend_cfg.LONG_THRESHOLD
                if row["signal_long"]
                else trend_cfg.SHORT_THRESHOLD
            )
            reason = f"trend score={row['final_signal']:.1f} vs {threshold} threshold"
            watchlist.append({"ticker": row["ticker"], "reason": reason})
    s2_signals = s2_details.get("signals") if s2_details else pd.DataFrame()
    if s2_signals is not None and not s2_signals.empty:
        s2_latest = s2_signals[s2_signals["date"] == s2_signals["date"].max()].copy()
        s2_latest = s2_latest[~s2_latest["ticker"].isin(held)]
        s2_latest["delta_score"] = S2_LONG_THRESHOLD - s2_latest["score_long"]
        near_long = s2_latest[
            (s2_latest["delta_score"] >= 0) & (s2_latest["delta_score"] <= 5)
        ]
        near_short = s2_latest[s2_latest["z_pe"] >= (Z_EXTREME_SHORT - 0.25)]
        for _, row in near_long.head(5).iterrows():
            reason = f"score={row['score_long']:.1f} vs {S2_LONG_THRESHOLD} threshold"
            watchlist.append({"ticker": row["ticker"], "reason": reason})
        for _, row in near_short.head(5).iterrows():
            reason = f"z_pe={row['z_pe']:.2f} vs {Z_EXTREME_SHORT} short threshold"
            watchlist.append({"ticker": row["ticker"], "reason": reason})
    watchlist = watchlist[:10]
    broker_equity = os.environ.get("BROKER_EQUITY")
    broker_equity_val = (
        float(broker_equity) if broker_equity not in (None, "") else None
    )
    reconciliation = {
        "model_start_equity": DEFAULT_PORTFOLIO_BASE_EQUITY,
        "model_current_equity": portfolio_stats.get("equity"),
        "broker_equity": broker_equity_val,
        "difference": (
            (portfolio_stats.get("equity") - broker_equity_val)
            if broker_equity_val
            else None
        ),
        "note": (
            "slippage/fees/timing"
            if broker_equity_val
            else "broker equity placeholder (set BROKER_EQUITY)"
        ),
    }
    allocations = {
        "risk_on": 1.0 - alloc_result.cash_weight,
        "cash": alloc_result.cash_weight,
        "sleeves": alloc_result.sleeve_allocations,
        "cash_reason": getattr(alloc_result, "cash_reason", None),
    }
    performance_diagnostics = {
        "current_equity": portfolio_stats.get("equity"),
        "day_return": portfolio_stats.get("day_return"),
        "cumulative_return": portfolio_stats.get("cumulative_return"),
    }

    previous_weights_df = None
    if (
        s2_details
        and s2_details.get("weights_df") is not None
        and not s2_details.get("weights_df").empty
    ):
        s2_weights_hist = s2_details.get("weights_df")
        prev_row = s2_weights_hist.iloc[-1]
        previous_weights_df = pd.DataFrame(
            {
                "ticker": prev_row.index,
                "target_weight": prev_row.values,
            }
        )

    s2_no_picks = False
    if (
        s2_details
        and s2_details.get("target_weights") is not None
        and not s2_details.get("target_weights").empty
    ):
        s2_last_weights = s2_details.get("target_weights").iloc[-1]
        s2_no_picks = s2_last_weights.abs().sum() <= WEIGHT_TOLERANCE

    proposed_trades = build_proposed_trades(
        alloc_result=alloc_result,
        previous_weights=previous_weights_df,
        price_map=price_map,
        model_equity=model_equity,
    )

    performance_summary = compute_performance_summary(
        report_date=report_date,
        sleeve_equity_map={
            "sleeve_trend": st_equity,
            "sleeve_2": s2_equity,
        },
        sleeve_allocations=alloc_result.sleeve_allocations,
        base_equity=DEFAULT_PORTFOLIO_BASE_EQUITY,
    )

    return {
        "asof": report_date,
        "allocations": allocations,
        "performance_summary": performance_summary,
        "orders": orders,
        "risk_levels": risk_levels,
        "holdings": holdings,
        "watchlist": watchlist,
        "reconciliation": reconciliation,
        "proposed_trades": proposed_trades,
        "performance_summary": performance_summary,
        "performance_diagnostics": performance_diagnostics,
        "skipped_trades": alloc_result.skipped_trades if alloc_result else [],
        "s2_no_picks": s2_no_picks,
    }


# ============================================================
# Report builder (FIXED)
# ============================================================
def build_html_report(
    report_date: pd.Timestamp | str,
    st_equity,
    st_trades,
    s2_equity,
    s2_trades,
    alloc_result: AllocationResult = None,
    alpha_stats: dict | None = None,
    proposed_trades: list[dict] | None = None,
    performance_summary: dict | None = None,
    s2_no_picks: bool = False,
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

    report_date_fmt = _fmt_date(report_date)

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
                    rows.append(
                        {
                            "Sleeve": f"Sleeve Trend — Momentum ({trend_alloc:.2%})",
                            "Allocated": _fmt_money(alloc_notional),
                            "Equity": _fmt_money(contrib_equity),
                            "Day Return": _fmt_pct(st_day_ret),
                        }
                    )
                else:
                    rows.append(
                        {
                            "Sleeve": f"Sleeve Trend — Momentum ({trend_alloc:.2%})",
                            "Allocated": _fmt_money(alloc_notional),
                            "Equity": "—",
                            "Day Return": "—",
                        }
                    )
            else:
                rows.append(
                    {
                        "Sleeve": f"Sleeve Trend — Momentum ({trend_alloc:.2%})",
                        "Allocated": _fmt_money(alloc_notional),
                        "Equity": "—",
                        "Day Return": "—",
                    }
                )
        else:
            rows.append(
                {
                    "Sleeve": "Sleeve Trend — Momentum (inactive)",
                    "Allocated": "—",
                    "Equity": "—",
                    "Day Return": "—",
                }
            )
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
                    rows.append(
                        {
                            "Sleeve": f"Sleeve 2 — Valuation ({val_alloc:.2%})",
                            "Allocated": _fmt_money(alloc_notional),
                            "Equity": _fmt_money(contrib_equity),
                            "Day Return": _fmt_pct(s2_day_ret),
                        }
                    )
                else:
                    rows.append(
                        {
                            "Sleeve": f"Sleeve 2 — Valuation ({val_alloc:.2%})",
                            "Allocated": _fmt_money(alloc_notional),
                            "Equity": "—",
                            "Day Return": "—",
                        }
                    )
            else:
                rows.append(
                    {
                        "Sleeve": f"Sleeve 2 — Valuation ({val_alloc:.2%})",
                        "Allocated": _fmt_money(alloc_notional),
                        "Equity": "—",
                        "Day Return": "—",
                    }
                )
        else:
            rows.append(
                {
                    "Sleeve": "Sleeve 2 — Valuation (inactive)",
                    "Allocated": "—",
                    "Equity": "—",
                    "Day Return": "—",
                }
            )
            sleeve2_label = (
                "Sleeve 2 — Valuation (no eligible picks)"
                if s2_no_picks
                else "Sleeve 2 — Valuation (inactive)"
            )
            rows.append(
                {
                    "Sleeve": sleeve2_label,
                    "Allocated": "—",
                    "Equity": "—",
                    "Day Return": "—",
                }
            )

        # CASH row
        if cash_alloc > WEIGHT_TOLERANCE:
            cash_notional = BASE_EQUITY * cash_alloc
            rows.append(
                {
                    "Sleeve": f"CASH ({cash_alloc:.2%})",
                    "Allocated": _fmt_money(cash_notional),
                    "Equity": _fmt_money(cash_notional),  # Cash doesn't grow
                    "Day Return": _fmt_pct(0),
                }
            )
        summary_df = pd.DataFrame(rows)
        # TOTAL row - THE AUTHORITATIVE PORTFOLIO EQUITY
        # Computed from weighted sleeve returns, NOT by summing rows above
        total_row = pd.DataFrame(
            [
                {
                    "Sleeve": f"TOTAL — Portfolio ({_fmt_money(BASE_EQUITY)})",
                    "Allocated": _fmt_money(BASE_EQUITY),
                    "Equity": _fmt_money(portfolio_stats["equity"]),
                    "Day Return": _fmt_pct(portfolio_stats["day_return"]),
                }
            ]
        )
        summary_df = pd.concat([summary_df, total_row], ignore_index=True)
        alloc_summary = allocation_summary_df(alloc_result)
        holdings = holdings_snapshot_df(alloc_result)
        skipped_df = (
            pd.DataFrame(alloc_result.skipped_trades)
            if alloc_result.skipped_trades
            else pd.DataFrame()
        )
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
            {
                "Sleeve": "Sleeve Trend — Momentum (80%)",
                "Allocated": _fmt_money(BASE_EQUITY * 0.80),
                "Equity": "—",
                "Day Return": "—",
            },
            {
                "Sleeve": "Sleeve 2 — Valuation (20%)",
                "Allocated": _fmt_money(BASE_EQUITY * 0.20),
                "Equity": "—",
                "Day Return": "—",
            },
        ]
        total_row = {
            "Sleeve": f"TOTAL — Portfolio ({_fmt_money(BASE_EQUITY)})",
            "Allocated": _fmt_money(BASE_EQUITY),
            "Equity": _fmt_money(portfolio_stats["equity"]),
            "Day Return": _fmt_pct(portfolio_stats["day_return"]),
        }
        summary_df = pd.concat(
            [pd.DataFrame(rows), pd.DataFrame([total_row])], ignore_index=True
        )
        alloc_summary, holdings, skipped_df = None, None, pd.DataFrame()
    # Build exit log
    exit_log_rows = []
    for trades_df, sleeve_name in [(st_trades, "Trend"), (s2_trades, "Valuation")]:
        filtered = (
            filter_sleeve2_cash_proxy(_safe_df(trades_df))
            if sleeve_name == "Valuation"
            else _safe_df(trades_df)
        )
        if not filtered.empty and "reason_exit" in filtered.columns:
            for _, row in filtered.tail(10).iterrows():
                exit_log_rows.append(
                    {
                        "Ticker": row.get("ticker", ""),
                        "Sleeve": sleeve_name,
                        "Exit Reason": row.get("reason_exit", ""),
                        "Days Held": row.get("hold_days", ""),
                        "P&L": _fmt_money(row.get("pnl", 0)),
                    }
                )
    # CSS
    css = (
        "body { font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Arial; color:#111827; }\n"
        " .wrap { max-width: 960px; margin: 0 auto; padding: 16px; }\n"
        " .card { background:#f9fafb; border:1px solid #e5e7eb; border-radius: 10px; padding: 12px; margin: 12px 0; }\n"
        " h2 { margin-bottom: 4px; }\n"
        " h3 { margin-top: 16px; }\n"
        " .tbl { width:100%; border-collapse: collapse; font-size: 13px; }\n"
        " .tbl th { text-align:left; border-bottom:1px solid #e5e7eb; padding:6px; }\n"
        " .tbl td { border-bottom:1px solid #f3f4f6; padding:6px; }\n"
        " .muted { color:#6b7280; font-size:12px; }\n"
    )
    performance_rows = [
        {"Metric": "Current Equity", "Value": _fmt_money(portfolio_stats["equity"])},
        {"Metric": "Day Return", "Value": _fmt_pct(portfolio_stats["day_return"])},
        {
            "Metric": "Cumulative Return",
            "Value": _fmt_pct(portfolio_stats["cumulative_return"]),
        },
    ]
    performance_html = html_table(
        pd.DataFrame(performance_rows), "Performance Summary (Portfolio)", 10
    )
    performance_section = f'<div class="card">{performance_html}</div>'

    def _fmt_float(value: float | None) -> str:
        try:
            return f"{float(value):.2f}"
        except Exception:
            return "n/a"

    if alpha_stats and alpha_stats.get("n_days", 0) >= 20:
        alpha_rows = [
            {
                "Metric": "Since inception",
                "Value": "Port {port}, SPY {bench}, Excess {excess}".format(
                    port=_fmt_pct(alpha_stats.get("port_cum_return")),
                    bench=_fmt_pct(alpha_stats.get("bench_cum_return")),
                    excess=_fmt_pct(alpha_stats.get("excess_cum_return")),
                ),
            },
            {
                "Metric": "Beta (63d), Alpha (ann., 63d)",
                "Value": "{beta}, {alpha}".format(
                    beta=_fmt_float(alpha_stats.get("beta_63d")),
                    alpha=_fmt_pct(alpha_stats.get("alpha_ann_63d")),
                ),
            },
            {
                "Metric": "Tracking Error (ann.), Information Ratio",
                "Value": "{te}, {ir}".format(
                    te=_fmt_pct(alpha_stats.get("tracking_error_ann")),
                    ir=_fmt_float(alpha_stats.get("info_ratio")),
                ),
            },
            {
                "Metric": "Max Drawdown: Port %, SPY %",
                "Value": "Port {port}, SPY {bench}".format(
                    port=_fmt_pct(alpha_stats.get("mdd_port")),
                    bench=_fmt_pct(alpha_stats.get("mdd_bench")),
                ),
            },
            {"Metric": "n_days used", "Value": str(alpha_stats.get("n_days", 0))},
        ]
        alpha_html = html_table(
            pd.DataFrame(alpha_rows), "Alpha Attribution vs SPY", 10
        )
        alpha_section = f'<div class="card">{alpha_html}</div>'
    else:
        alpha_section = (
            '<div class="card">'
            "<h3>Alpha Attribution vs SPY</h3>"
            "<p><em>Alpha attribution unavailable (insufficient data or benchmark fetch failed).</em></p>"
            "</div>"
        )

    # Build sections
    perf_rows = []
    if performance_summary:
        perf_rows = [
            {
                "Metric": "Total Return (Since Inception)",
                "Return": _fmt_pct(performance_summary.get("total_return")),
            },
            {
                "Metric": "Week-to-Date",
                "Return": _fmt_pct(performance_summary.get("wtd")),
            },
            {
                "Metric": "Month-to-Date",
                "Return": _fmt_pct(performance_summary.get("mtd")),
            },
            {
                "Metric": "Year-to-Date",
                "Return": _fmt_pct(performance_summary.get("ytd")),
            },
        ]
    perf_section = (
        f'<div class="card">{html_table(pd.DataFrame(perf_rows), "Performance Summary")}</div>'
        if perf_rows
        else ""
    )

    proposed_df = pd.DataFrame(proposed_trades or [])
    if not proposed_df.empty:
        proposed_df = proposed_df.rename(
            columns={
                "ticker": "Ticker",
                "action": "Action",
                "sleeve": "Sleeve",
                "current_weight": "Current Weight",
                "target_weight": "Target Weight",
                "delta_weight": "Delta Weight",
                "est_shares": "Est. Shares",
                "est_notional": "Est. Notional",
            }
        )
    proposed_html = html_table(
        proposed_df,
        "Proposed Trades / Next Rebalance",
        25,
        "No proposed trades.",
    )
    proposed_section = f'<div class="card">{proposed_html}</div>'

    alloc_html = (
        alloc_summary.to_html(index=False, border=0, classes="tbl", justify="left")
        if alloc_summary is not None
        else ""
    )
    alloc_section = (
        f'<div class="card"><h3>Sleeve Allocation (Dynamic)</h3>{alloc_html}</div>'
        if alloc_html
        else ""
    )

    holdings_html = (
        html_table(holdings, "Holdings Snapshot", 20)
        if holdings is not None and not holdings.empty
        else ""
    )
    holdings_section = (
        f'<div class="card">{holdings_html}</div>' if holdings_html else ""
    )

    skipped_html = (
        html_table(skipped_df, "Skipped Trades (Constraint Hits)", 10)
        if not skipped_df.empty
        else ""
    )
    skipped_section = f'<div class="card">{skipped_html}</div>' if skipped_html else ""

    exit_html = (
        html_table(pd.DataFrame(exit_log_rows), "Exit Log", 15) if exit_log_rows else ""
    )
    exit_section = f'<div class="card">{exit_html}</div>' if exit_html else ""

    st_trades_html = html_table(
        filter_sleeve2_cash_proxy(st_trades), "Recent Trades — Sleeve Trend", 15
    )
    s2_trades_html = html_table(
        filter_sleeve2_cash_proxy(s2_trades),
        "Recent Trades — Sleeve 2",
        15,
        "No eligible picks for Sleeve 2.",
    )

    st_equity_html = html_table(
        _safe_df(st_equity).tail(10), "Equity — Sleeve Trend (last 10 days)", 10
    )
    s2_equity_html = html_table(
        _safe_df(s2_equity).tail(10), "Equity — Sleeve 2 (last 10 days)", 10
    )

    return f"""
    <html>
    <head><style>{css}</style></head>
    <body>
      <div class="wrap">
        <h2>Daily Quant Report</h2>
        <div class="muted">{report_date_fmt}</div>
        {perf_section}
        {proposed_section}
        <div class="card">{html_table(summary_df, "Portfolio Snapshot")}</div>
        {performance_section}
        {alpha_section}
        {alloc_section}
        {holdings_section}
        <div class="card">
          {st_trades_html}
          {s2_trades_html}
        </div>
        {exit_section}
        {skipped_section}
        <div class="card">
          {st_equity_html}
          {s2_equity_html}
        </div>
        <div class="muted">
          Automated daily report. Portfolio TOTAL computed from weighted sleeve returns
          (not sum of rows).
        </div>
      </div>
    </body>
    </html>
    """


# ============================================================
# Main
# ============================================================
def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    offline_fixture = os.getenv("OFFLINE_FIXTURE", "").lower() in {"1", "true", "yes"}
    fixture_date = os.getenv("OFFLINE_FIXTURE_DATE", "2000-01-01")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = fixture_date if offline_fixture else dt.date.today().strftime("%Y-%m-%d")
    portfolio_fixture = (
        _load_portfolio_fixture("tests/fixtures/portfolio_equity.csv")
        if offline_fixture
        else pd.Series(dtype=float)
    )
    if offline_fixture and not portfolio_fixture.empty:
        today = portfolio_fixture.index.max().strftime("%Y-%m-%d")
    # ── Run sleeves ───────────────────────────────────────────────
    if offline_fixture:
        logger.warning(
            "[OFFLINE] Fixture mode enabled; skipping sleeve runs and live data fetches."
        )
        _, _ = pd.DataFrame(), pd.DataFrame()
        st_equity, st_trades, st_signals = (
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
        )
        s2_details = {}
        s2_equity, s2_trades = pd.DataFrame(), pd.DataFrame()
    else:
        try:
            _, _ = run_sleeve_1()
        except Exception as e:
            logger.warning("[WARN] Sleeve 1 failed: %s", e)
            _, _ = pd.DataFrame(), pd.DataFrame()
        try:
            st_equity, st_trades, st_signals = run_sleeve_trend()
        except Exception as e:
            logger.warning("[WARN] Sleeve Trend failed: %s", e)
            st_equity, st_trades, st_signals = (
                pd.DataFrame(),
                pd.DataFrame(),
                pd.DataFrame(),
            )
        try:
            s2_details = run_sleeve_2()
            s2_equity = s2_details.get("equity_df", pd.DataFrame())
            s2_trades = s2_details.get("trades_df", pd.DataFrame())
        except Exception as e:
            logger.warning("[WARN] Sleeve 2 failed: %s", e)
            s2_details = {}
            s2_equity, s2_trades = pd.DataFrame(), pd.DataFrame()
    # ── Sleeve health checks ─────────────────────────────────────
    # Validate each sleeve BEFORE allocation.  Invalid sleeves get
    # their weight routed to CASH, never to another sleeve.
    trend_valid, trend_reason = _sleeve_is_valid(st_equity)
    s2_valid, s2_reason = _sleeve_is_valid(s2_equity)
    if not trend_valid:
        logger.warning("sleeve_trend inactive: %s -> routed to CASH", trend_reason)
    if not s2_valid:
        logger.warning("sleeve_2 inactive: %s -> routed to CASH", s2_reason)
    # ── Extract sleeve outputs for dynamic allocation ─────────────
    trend_output = extract_sleeve_output(st_equity, st_trades, "sleeve_trend", 1.0)
    val_output = extract_sleeve_output(s2_equity, s2_trades, "sleeve_2", 1.0)
    val_output = extract_sleeve_output(
        s2_equity,
        s2_trades,
        "sleeve_2",
        1.0,
        target_weights=s2_details.get("target_weights") if s2_details else None,
    )

    # ── Run dynamic allocation ────────────────────────────────────
    risk_off = os.getenv("RISK_OFF", "").lower() in ("1", "true", "yes", "y")
    allocator = PortfolioAllocator(risk_off=risk_off)
    alloc_result = allocator.allocate([trend_output, val_output])
    alloc_result.sleeve_allocations = derive_actual_sleeve_allocations(alloc_result)

    # ── SAFE ALLOCATION POLICY ────────────────────────────────────
    # If a sleeve is invalid, force its allocation to 0 and route
    # the freed weight to CASH (never to another sleeve).
    patched = False
    freed_weight = 0.0
    if (
        not trend_valid
        and alloc_result.sleeve_allocations.get("sleeve_trend", 0.0) > WEIGHT_TOLERANCE
    ):
        freed_weight += alloc_result.sleeve_allocations["sleeve_trend"]
        alloc_result.sleeve_allocations["sleeve_trend"] = 0.0
        patched = True
    if (
        not s2_valid
        and alloc_result.sleeve_allocations.get("sleeve_2", 0.0) > WEIGHT_TOLERANCE
    ):
        freed_weight += alloc_result.sleeve_allocations["sleeve_2"]
        alloc_result.sleeve_allocations["sleeve_2"] = 0.0
        patched = True
    if patched:
        alloc_result.cash_weight = alloc_result.cash_weight + freed_weight
        # Re-normalise total_weight (should be 1.0)
        alloc_result.total_weight = (
            sum(alloc_result.sleeve_allocations.values()) + alloc_result.cash_weight
        )
        logger.info(
            "[ALLOCATION] Freed %.1f%% from inactive sleeve(s) -> CASH",
            freed_weight * 100,
        )
    # ── Validate allocation ───────────────────────────────────────
    errors = validate_allocation_result(alloc_result)
    if errors:
        logger.warning("[WARN] Allocation validation errors: %s", errors)
    # ── Log allocation summary ────────────────────────────────────
    logger.info("\n[ALLOCATION] Sleeve allocations:")
    for sleeve, pct in alloc_result.sleeve_allocations.items():
        logger.info("  %s: %.1f%%", sleeve, pct * 100)
    logger.info("  CASH: %.1f%%", alloc_result.cash_weight * 100)
    logger.info("  Total weight: %.4f", alloc_result.total_weight)
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
    report_date = (
        s2_details.get("asof")
        if s2_details.get("asof") is not None
        else _asof_date_from_df(st_equity)
    )
    report_date = report_date or pd.Timestamp(
        fixture_date if offline_fixture else dt.date.today()
    )
    if offline_fixture and not portfolio_fixture.empty:
        report_date = portfolio_fixture.index.max()
    else:
        _ = resolve_portfolio_equity_series(
            sleeve_equity_map=sleeve_equity_map,
            alloc_result=alloc_result,
            report_date=report_date,
            portfolio_equity=portfolio_stats.get(
                "equity", DEFAULT_PORTFOLIO_BASE_EQUITY
            ),
            offline_fixture=offline_fixture,
            base_equity=DEFAULT_PORTFOLIO_BASE_EQUITY,
        )
    if offline_fixture and not portfolio_fixture.empty:
        portfolio_equity_for_alpha = portfolio_fixture
    else:
        portfolio_equity_for_alpha = resolve_portfolio_equity_series(
            sleeve_equity_map=sleeve_equity_map,
            alloc_result=alloc_result,
            report_date=report_date,
            portfolio_equity=portfolio_stats.get(
                "equity", DEFAULT_PORTFOLIO_BASE_EQUITY
            ),
            offline_fixture=offline_fixture,
            base_equity=DEFAULT_PORTFOLIO_BASE_EQUITY,
        )
    _ = resolve_portfolio_equity_series(
        sleeve_equity_map=sleeve_equity_map,
        alloc_result=alloc_result,
        report_date=report_date,
        portfolio_equity=portfolio_stats.get("equity", DEFAULT_PORTFOLIO_BASE_EQUITY),
        offline_fixture=offline_fixture,
        base_equity=DEFAULT_PORTFOLIO_BASE_EQUITY,
        portfolio_stats=portfolio_stats,
        st_equity=st_equity,
        s2_equity=s2_equity,
        st_signals=st_signals,
        s2_details=s2_details,
    )
    bench_prices_for_alpha = load_benchmark_prices(
        ticker="SPY",
        start=(
            portfolio_equity_for_alpha.index.min()
            if not portfolio_equity_for_alpha.empty
            else None
        ),
        end=(
            portfolio_equity_for_alpha.index.max()
            if not portfolio_equity_for_alpha.empty
            else None
        ),
        offline_fixture=offline_fixture,
    )
    alpha_stats = None
    logger.info(
        "[ALPHA] Data readiness - portfolio_equity_for_alpha: %s rows (%s), bench_prices_for_alpha: %s rows (%s)",
        len(portfolio_equity_for_alpha),
        _series_date_range(portfolio_equity_for_alpha),
        len(bench_prices_for_alpha),
        _series_date_range(bench_prices_for_alpha),
    )
    if portfolio_equity_for_alpha.empty or bench_prices_for_alpha.empty:
        logger.warning(
            "[WARN] Alpha attribution skipped (missing portfolio or benchmark history)."
        )
    else:
        alpha_stats = calc_alpha_stats(
            portfolio_equity_for_alpha, bench_prices_for_alpha, window=63
        )
    try:
        daily_snapshot = build_daily_snapshot(
            report_date=report_date,
            alloc_result=alloc_result,
            portfolio_stats=portfolio_stats,
            st_equity=st_equity,
            s2_equity=s2_equity,
            st_signals=st_signals,
            s2_details=s2_details,
        )
    except RuntimeError as e:
        logger.error("[ERROR] %s", e)
        sys.exit(0)
    daily_snapshot["alpha_attribution"] = alpha_stats

    # --- Paper trading execution + report ---
    # Execute using the same immutable daily snapshot written for this report date.
    trade_date_str = report_date.strftime("%Y-%m-%d")
    signals_path_exec = os.path.join("signals", f"{trade_date_str}.json")
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
                force=False,
            )
            logger.info(
                "[PAPER] Executed paper trading for %s using signals %s",
                trade_date_str,
                signals_path_exec,
            )
        except Exception as e:
            msg = repr(e)
            # If already executed, don't fail the email — just render from ledger/trades
            if "Ledger already contains run_date" in msg:
                logger.info(
                    "[PAPER] Already executed for %s; rendering report from ledger.",
                    trade_date_str,
                )
            else:
                logger.warning("[PAPER][WARN] Paper execution failed: %s", msg)
        # Always attempt to render the paper section if ledger/trades exist
        try:
            paper_html = build_paper_report_html(
                run_date=trade_date_str,
                ledger_path="paper/ledger.csv",
                trades_path="paper/trades.csv",
                benchmark_ticker="SPY",
                reconciliation=paper_summary,
            )
        except Exception as e:
            logger.warning("[PAPER][WARN] Paper report HTML build failed: %s", repr(e))
    else:
        logger.warning(
            "[PAPER][WARN] Missing signals for execution: %s", signals_path_exec
        )
    # ── Build daily snapshot/email ────────────────────────────────
    subject, email_body = create_pm_first_trade_email(daily_snapshot)
    # Append paper trading summary to plain-text email (if available)
    if paper_summary:
        email_body += (
            f"\n\n---\nPaper Trading Execution ({paper_summary['date']}): "
            f"equity=${paper_summary['total_equity']:.2f}, "
            f"cash=${paper_summary['cash']:.2f}, "
            f"trades={paper_summary['num_trades']}, "
            f"turnover=${paper_summary['turnover_notional']:.2f}\n"
        )
        email_body += "\nPost-Trade Reconciliation\n"
        email_body += (
            f"   - Equity: ${paper_summary['total_equity']:.2f} | "
            f"Cash: ${paper_summary['cash']:.2f} | "
            f"Cash Weight: {100.0 * paper_summary.get('achieved_cash_weight', 0.0):.2f}% | "
            f"Target Cash Weight: {100.0 * paper_summary.get('target_cash_weight', 0.0):.2f}%\n"
        )
        scaled = paper_summary.get("scaled_tickers", []) or []
        if scaled:
            email_body += f"   - Cash-constrained tickers: {', '.join(scaled)}\n"
        else:
            email_body += "   - Cash-constrained tickers: None\n"
        for row in paper_summary.get("position_reconciliation", []) or []:
            flag = " [CASH LIMITED]" if row.get("cash_limited") else ""
            email_body += (
                f"   - {row.get('ticker','')}: "
                f"target={100.0 * float(row.get('target_weight', 0.0)):.2f}% "
                f"achieved={100.0 * float(row.get('achieved_weight', 0.0)):.2f}% "
                f"delta={100.0 * float(row.get('delta_weight', 0.0)):+.2f}%"
                f"{flag}\n"
            )
    # Write the plain-text email body to disk
    email_path = os.path.join(OUTPUT_DIR, f"trade_rundown_{today}.txt")
    with open(email_path, "w", encoding="utf-8") as f:
        f.write(email_body)
    logger.info("[OK] Daily trade email written: %s", email_path)
    # ── Build report ──────────────────────────────────────────────
    html = build_html_report(
        report_date=report_date,
        st_equity=st_equity,
        st_trades=st_trades,
        s2_equity=s2_equity,
        s2_trades=s2_trades,
        alloc_result=alloc_result,
        proposed_trades=daily_snapshot.get("proposed_trades"),
        performance_summary=daily_snapshot.get("performance_summary"),
        s2_no_picks=daily_snapshot.get("s2_no_picks", False),
    )
    # Append paper trading HTML section (if available)
    if paper_html:
        html = html + "<hr/>" + paper_html

    out_path = os.path.join(OUTPUT_DIR, f"quant_report_{today}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("[OK] HTML report written: %s", out_path)
    logger.info("\n[EMAIL PREVIEW]\n")
    logger.info("%s", email_body)
    if send_email:
        try:
            send_email(subject=subject, body_html=html, body_text=email_body)
            logger.info("[OK] Email sent")
        except Exception as e:
            logger.warning("[WARN] Email not sent: %s", e)
    else:
        logger.warning("[WARN] send_email not found — HTML generated only")


if __name__ == "__main__":
    main()
