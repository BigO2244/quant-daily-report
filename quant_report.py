import os
import smtplib
import tempfile
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

import numpy as np
import pandas as pd
import yfinance as yf

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
        print(f"⚠️ Skipped {len(failed)} tickers with no/invalid price data:")
        failed_unique = sorted(set(failed))
        print(failed_unique[:50], "..." if len(failed_unique) > 50 else "")

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

def send_email(subject: str, body_html: str) -> None:
    """Send HTML email using SMTP credentials from env vars."""
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    to_addr = os.environ.get("REPORT_TO_EMAIL", "")

    if not (host and user and password and to_addr):
        raise RuntimeError("Missing SMTP env vars (SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/REPORT_TO_EMAIL).")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.attach(MIMEText(body_html, "html"))

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(user, [to_addr], msg.as_string())
