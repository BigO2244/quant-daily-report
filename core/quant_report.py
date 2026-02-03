import logging
import os
import smtplib
import tempfile
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

IN_CI = os.getenv("CI", "").lower() == "true" or bool(os.getenv("GITHUB_ACTIONS"))

if IN_CI:
    # Put yfinance timezone cache in a per-run temp folder to avoid sqlite lock collisions
    yf.set_tz_cache_location(tempfile.mkdtemp(prefix="yf_tz_cache_"))

# =========================
# Config
# =========================

TICKERS = []  # optional override; if empty, load from data/universe.csv

MAX_RISK_PCT_PER_TRADE = float(os.environ.get("MAX_RISK_PCT_PER_TRADE", "0.01"))  # 1% equity risk per trade
MAX_POSITION_PCT = float(os.environ.get("MAX_POSITION_PCT", "0.10"))              # 10% max notional per position
DATE_FORMAT = os.environ.get("DATE_FORMAT", "US")
DISPLAY_DECIMALS = int(os.environ.get("DISPLAY_DECIMALS", "2"))

# =========================
# Universe helpers
# =========================

def load_universe_df(path: str = "data/universe.csv") -> pd.DataFrame:
    """Load data/universe.csv with at least a 'ticker' column and (optional) 'sector' column."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Universe file not found: {path}")

    u = pd.read_csv(path)
    if "ticker" not in u.columns:
        raise ValueError("data/universe.csv must have a 'ticker' column")

    u["ticker"] = u["ticker"].astype(str).str.upper().str.strip()
    u = u.dropna(subset=["ticker"]).drop_duplicates(subset=["ticker"])
    return u.reset_index(drop=True)

# Auto-load tickers from universe if not explicitly overridden above
if not TICKERS:
    _u = load_universe_df()
    TICKERS = _u['ticker'].tolist()

def _yahoo_symbol(t: str) -> str:
    """Hook to map internal symbols to Yahoo symbols if needed."""
    return str(t).strip().upper()

# =========================
# Market data
# =========================

def download_prices(tickers: List[str], period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    """
    Download OHLCV data per ticker from yfinance and return a long-form table:
    columns: date, ticker, open, high, low, close, volume

    Robustness: skips tickers that return empty data or missing columns.
    """
    data: List[pd.DataFrame] = []
    failed: List[str] = []

    for t in tickers:
        try:
            yt = _yahoo_symbol(t)

            # In CI, avoid any concurrency and add a quick retry for transient sqlite locks
            attempts = 3 if IN_CI else 1
            last_err = None

            for i in range(attempts):
                try:
                    df = yf.download(
                        yt,
                        period=period,
                        interval=interval,
                        progress=False,
                        auto_adjust=False,
                        threads=False if IN_CI else True,
                        timeout=30,
                    )
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    time.sleep(1.5 * (i + 1))

            if last_err is not None:
                failed.append(t)
                continue

            if df is None or df.empty:
                failed.append(t)
                continue

            # If yfinance returns MultiIndex columns, flatten them
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]

            df = df.reset_index()

            # Normalize column names
            df = df.rename(
                columns={
                    "Date": "date",
                    "Open": "open",
                    "High": "high",
                    "Low": "low",
                    "Close": "close",
                    "Adj Close": "adj_close",
                    "Volume": "volume",
                }
            )

            # Some feeds return lowercase already; handle that too
            df = df.rename(columns={c: c.lower() for c in df.columns})

            required = {"date", "open", "high", "low", "close", "volume"}
            if not required.issubset(set(df.columns)):
                failed.append(t)
                continue

            df["ticker"] = t.strip().upper()
            df = df[["date", "ticker", "open", "high", "low", "close", "volume"]]
            data.append(df)

        except Exception:
            failed.append(t)
            continue

    if not data:
        raise RuntimeError("No price data downloaded. Check data/universe.csv and yfinance availability.")

    if failed:
        logger.warning("⚠️ Skipped %s tickers with no/invalid price data:", len(failed))
        failed_unique = sorted(set(failed))
        logger.warning("%s %s", failed_unique[:50], "..." if len(failed_unique) > 50 else "")

    prices = pd.concat(data, ignore_index=True)
    prices["date"] = pd.to_datetime(prices["date"])
    return prices

# =========================
# Factors / signals (placeholders for your existing pipeline)
# =========================

def fetch_factor_data(prices: pd.DataFrame) -> pd.DataFrame:
    """Stub – keep your existing factor pipeline here if you already have one."""
    # Example: compute daily returns
    out = prices.copy()
    out["ret"] = out.groupby("ticker")["close"].pct_change(fill_method=None)
    return out


def build_factor_scores(factor_df: pd.DataFrame) -> pd.DataFrame:
    """Stub – keep your existing factor scoring logic here."""
    return factor_df


def compute_full_signals(scored_df: pd.DataFrame) -> pd.DataFrame:
    """Stub – keep your existing signal combining logic here."""
    return scored_df


def add_atr(prices: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    """Compute ATR on long-form OHLC data."""
    df = prices.copy()
    df = df.sort_values(["ticker", "date"])
    high = df["high"]
    low = df["low"]
    close = df.groupby("ticker")["close"].shift(1)

    tr = np.maximum(high - low, np.maximum((high - close).abs(), (low - close).abs()))
    df["atr"] = tr.groupby(df["ticker"]).rolling(window).mean().reset_index(level=0, drop=True)
    return df

# =========================
# Email
# =========================


def _fmt_money(value: Optional[float]) -> str:
    if isinstance(value, str) and value.strip().startswith("$"):
        return value
    try:
        return f"${float(value):,.{DISPLAY_DECIMALS}f}"
    except Exception:
        return "n/a"


def _fmt_pct(value: Optional[float]) -> str:
    if isinstance(value, str) and "%" in value:
        return value
    try:
        return f"{float(value) * 100:.{DISPLAY_DECIMALS}f}%"
    except Exception:
        return "n/a"


def _fmt_date(value: Optional[object]) -> str:
    try:
        dt_value = pd.to_datetime(value)
        if DATE_FORMAT.upper() == "US":
            return dt_value.strftime("%m/%d/%Y")
        return dt_value.strftime("%Y-%m-%d")
    except Exception:
        return "n/a"

def _fmt_float(value: Optional[float]) -> str:
    try:
        return f"{float(value):.2f}"
    except Exception:
        return "n/a"


def _format_table(headers: List[str], rows: List[List[str]]) -> str:
    if not rows:
        return "(none)"

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    def _fmt_row(cells: List[str]) -> str:
        return " | ".join(str(cell).ljust(col_widths[i]) for i, cell in enumerate(cells))

    lines = [_fmt_row(headers), "-+-".join("-" * w for w in col_widths)]
    for row in rows:
        lines.append(_fmt_row(row))
    return "\n".join(lines)


def create_trade_email(snapshot: dict) -> Tuple[str, str]:
    """
    Create the plain-text Daily Trade Rundown email body.

    snapshot keys:
        asof, allocations, orders, risk_levels, holdings, watchlist, reconciliation
    """
    asof = snapshot.get("asof")
    asof_str = _fmt_date(asof) if asof is not None else "n/a"
    subject = f"Daily Trade Rundown — {asof_str}"

    allocations = snapshot.get("allocations", {})
    cash_pct = allocations.get("cash", 0.0)
    risk_on_pct = allocations.get("risk_on", 1.0 - cash_pct)
    sleeve_splits = allocations.get("sleeves", {})

    lines = []
    lines.append(subject)
    lines.append("")
    lines.append("0) Summary / allocation")
    lines.append(f"   - Risk-on: {_fmt_pct(risk_on_pct)}")
    lines.append(f"   - Cash: {_fmt_pct(cash_pct)}")
    for sleeve, pct in sleeve_splits.items():
        lines.append(f"   - {sleeve}: {_fmt_pct(pct)}")

    lines.append("")
    lines.append("1) Performance Summary (Portfolio)")
    perf = snapshot.get("performance_summary", {})
    if not perf:
        lines.append("   - None")
    else:
        lines.append(f"   - Total Return (Since Inception): {_fmt_pct(perf.get('total_return'))}")
        lines.append(f"   - Week-to-Date: {_fmt_pct(perf.get('wtd'))}")
        lines.append(f"   - Month-to-Date: {_fmt_pct(perf.get('mtd'))}")
        lines.append(f"   - Year-to-Date: {_fmt_pct(perf.get('ytd'))}")

    lines.append("")
    lines.append("2) Proposed Trades / Next Rebalance")
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
            lines.append(line)

    lines.append("")
    lines.append("3) Trades for Today (NEW ORDERS)")
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
    lines.append("4) Risk & Exit Levels (ACTIVE POSITIONS)")
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
    lines.append("5) Current Holdings (LIVE BOOK)")
    holdings = snapshot.get("holdings", [])
    headers = ["Ticker", "Dir", "Entry Date", "Entry Px", "Last Px", "P&L $", "P&L %", "Days Held"]
    rows = []
    for h in holdings:
        rows.append([
            h.get("ticker", ""),
            h.get("direction", ""),
            _fmt_date(h.get("entry_date", "")),
            _fmt_money(h.get("entry_price")),
            _fmt_money(h.get("last_price")),
            _fmt_money(h.get("pnl_dollars")),
            _fmt_pct(h.get("pnl_pct")),
            str(h.get("days_held", "")),
        ])
    lines.append(_format_table(headers, rows))

    lines.append("")
    lines.append("6) Watchlist (NO TRADES YET)")
    watchlist = snapshot.get("watchlist", [])
    if not watchlist:
        lines.append("   - None")
    else:
        for item in watchlist:
            lines.append(f"   - {item.get('ticker', '')}: {item.get('reason', '')}")

    lines.append("")
    lines.append("7) Account Reconciliation (MODEL vs BROKER)")
    recon = snapshot.get("reconciliation", {})
    lines.append(f"   - Model Starting Equity: {_fmt_money(recon.get('model_start_equity'))}")
    lines.append(f"   - Model Current Equity: {_fmt_money(recon.get('model_current_equity'))}")
    lines.append(f"   - Broker Current Equity: {_fmt_money(recon.get('broker_equity'))}")
    lines.append(f"   - Difference: {_fmt_money(recon.get('difference'))}")
    if recon.get("note"):
        lines.append(f"   - Note: {recon.get('note')}")

    lines.append("")
    lines.append("6) Performance Summary (Portfolio)")
    perf = snapshot.get("performance_summary", {})
    lines.append(f"   - Current Equity: {_fmt_money(perf.get('current_equity'))}")
    lines.append(f"   - Day Return: {_fmt_pct(perf.get('day_return'))}")
    lines.append(f"   - Cumulative Return: {_fmt_pct(perf.get('cumulative_return'))}")

    lines.append("")
    lines.append("7) Alpha Attribution vs SPY")
    alpha = snapshot.get("alpha_attribution")
    if not alpha or alpha.get("n_days", 0) < 20:
        lines.append("   - Alpha attribution unavailable (insufficient data or benchmark fetch failed).")
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

    return subject, "\n".join(lines)


def send_email(subject: str, body_html: Optional[str] = None, body_text: Optional[str] = None) -> None:
    """Send HTML/email using SMTP credentials from env vars."""
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    to_addr = os.environ.get("REPORT_TO_EMAIL", "")

    if not (host and user and password and to_addr):
        raise RuntimeError("Missing SMTP env vars (SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/REPORT_TO_EMAIL).")

    if body_html is None and body_text is None:
        raise ValueError("send_email requires body_html or body_text")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr

    if body_text:
        msg.attach(MIMEText(body_text, "plain"))
    if body_html:
        msg.attach(MIMEText(body_html, "html"))

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        refused = server.sendmail(user, [to_addr], msg.as_string())
        logger.info("[EMAIL DEBUG] refused=%s", refused)
        if refused:
            raise RuntimeError(f"SMTP refused recipients: {refused}")
        logger.info("[OK] Email accepted by SMTP server")
