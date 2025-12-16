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
# CONFIG (shared by report & backtest)
# =========================

# Position/risk sizing (backtest.py imports these)
MAX_RISK_PCT_PER_TRADE = float(os.environ.get("MAX_RISK_PCT_PER_TRADE", "0.01"))  # 1% risk per trade
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

    u["ticker"] = u["ticker"].astype(str).str.strip().str.upper()
    u = u.dropna(subset=["ticker"])

    if "sector" not in u.columns:
        u["sector"] = "Other"
    u["sector"] = u["sector"].fillna("Other").astype(str)

    # De-duplicate tickers (keep first sector label if duplicates exist)
    u = u.drop_duplicates(subset=["ticker"], keep="first")

    return u[["ticker", "sector"]]


def load_universe(path: str = "data/universe.csv") -> List[str]:
    """Convenience wrapper: returns tickers list."""
    return load_universe_df(path)["ticker"].tolist()


# Module-level universe used by scripts/workflows
TICKERS = load_universe()


# =========================
# Data functions
# =========================

def _yahoo_symbol(t: str) -> str:
    """Best-effort normalization for Yahoo symbols (e.g., BRK.B -> BRK-B)."""
    return t.strip().upper().replace(".", "-")


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
                        threads=False if IN_CI else True,  # yfinance supports this param
                        timeout=30,
                    )
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    # small backoff for sqlite lock / transient network errors
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


def fetch_factor_data(tickers: List[str]) -> pd.DataFrame:
    """
    Pull a small set of fundamental-ish fields from yfinance.
    Robustness: if a ticker fails, we keep it with NaNs (later filled).
    """
    rows = []
    for t in tickers:
        row = {"ticker": t.strip().upper()}
        try:
            info = yf.Ticker(_yahoo_symbol(t)).get_info()
            row.update(
                {
                    "trailingPE": info.get("trailingPE"),
                    "forwardPE": info.get("forwardPE"),
                    "profitMargins": info.get("profitMargins"),
                    "operatingMargins": info.get("operatingMargins"),
                    "returnOnEquity": info.get("returnOnEquity"),
                    "revenueGrowth": info.get("revenueGrowth"),
                    "earningsGrowth": info.get("earningsGrowth"),
                    "debtToEquity": info.get("debtToEquity"),
                    "beta": info.get("beta"),
                }
            )
        except Exception:
            pass
        rows.append(row)

    return pd.DataFrame(rows)


def _rank_to_0_100(series: pd.Series, higher_is_better: bool = True) -> pd.Series:
    s = series.astype(float)
    if not higher_is_better:
        s = -s
    ranked = s.rank(method="average", na_option="keep")
    max_val = ranked.max()
    if pd.isna(max_val) or max_val == 0:
        return ranked * 0
    return 100.0 * ranked / max_val


def build_factor_scores(factor_df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert raw factor_df into 0–100 scores and an overall factor_score per ticker.
    Fills missing numeric values with column medians.
    """
    df = factor_df.copy()
    df["ticker"] = df["ticker"].astype(str).str.upper()

    # Fill numeric NaNs with medians so ranking works
    df = df.fillna(df.median(numeric_only=True))

    # Quality
    df["quality_score"] = (
        0.4 * _rank_to_0_100(df["profitMargins"], True)
        + 0.4 * _rank_to_0_100(df["operatingMargins"], True)
        + 0.2 * _rank_to_0_100(df["returnOnEquity"], True)
    )

    # Growth
    df["growth_score"] = (
        0.5 * _rank_to_0_100(df["revenueGrowth"], True)
        + 0.5 * _rank_to_0_100(df["earningsGrowth"], True)
    )

    # Stability / valuation-ish (lower is better for debt, beta, PE)
    pe = df["trailingPE"].fillna(df["forwardPE"])
    df["stability_score"] = (
        0.4 * _rank_to_0_100(df["debtToEquity"], False)
        + 0.3 * _rank_to_0_100(df["beta"], False)
        + 0.3 * _rank_to_0_100(pe, False)
    )

    df["factor_score"] = (
        0.4 * df["quality_score"] + 0.4 * df["growth_score"] + 0.2 * df["stability_score"]
    )

    return df[["ticker", "quality_score", "growth_score", "stability_score", "factor_score"]]


def compute_full_signals(prices: pd.DataFrame, factor_scores: pd.DataFrame) -> pd.DataFrame:
    """
    Build momentum + volume + factor signals into a single final_signal (0–100 scale-ish).
    Returns one row per ticker-date with columns needed by backtest & report.
    """
    df = prices.sort_values(["ticker", "date"]).copy()

    # Returns (multi-horizon)
    df["return_5d"] = df.groupby("ticker")["close"].pct_change(5)
    df["return_10d"] = df.groupby("ticker")["close"].pct_change(10)
    df["return_20d"] = df.groupby("ticker")["close"].pct_change(20)

    def rank_to_score(series: pd.Series) -> pd.Series:
        r = series.rank(method="average", na_option="keep")
        max_val = r.max()
        if pd.isna(max_val) or max_val == 0:
            return r * 0
        return 100.0 * r / max_val

    # Momentum scores (cross-sectional by date)
    df["score_5d"] = df.groupby("date")["return_5d"].transform(rank_to_score)
    df["score_20d"] = df.groupby("date")["return_20d"].transform(rank_to_score)
    df["score_rs"] = df.groupby("date")["return_20d"].transform(rank_to_score)

    df["momentum_score_v2"] = 0.4 * df["score_5d"] + 0.4 * df["score_20d"] + 0.2 * df["score_rs"]

    # Merge factor scores
    fs = factor_scores.copy()
    fs["ticker"] = fs["ticker"].astype(str).str.upper()
    df = df.merge(fs, on="ticker", how="left")
    df["factor_score"] = df["factor_score"].fillna(df["factor_score"].mean())

    # Volume features
    df["avg_vol_20"] = df.groupby("ticker")["volume"].transform(lambda x: x.rolling(20).mean())
    df["rvol"] = df["volume"] / df["avg_vol_20"]
    df["vol_trend_5"] = df.groupby("ticker")["volume"].transform(lambda x: x.diff(5))

    df["price_change"] = df.groupby("ticker")["close"].diff()
    df["up_down_volume"] = np.where(df["price_change"] > 0, df["volume"], -df["volume"])

    df["vwap_proxy"] = (df["high"] + df["low"] + df["close"]) / 3.0
    df["vwap_distance"] = (df["close"] - df["vwap_proxy"]) / df["vwap_proxy"]

    df["score_rvol"] = df.groupby("date")["rvol"].transform(rank_to_score)
    df["score_voltrend"] = df.groupby("date")["vol_trend_5"].transform(rank_to_score)
    df["score_updown"] = df.groupby("date")["up_down_volume"].transform(rank_to_score)
    df["score_vwap_dist"] = df.groupby("date")["vwap_distance"].transform(rank_to_score)

    df["volume_score"] = (
        0.4 * df["score_rvol"]
        + 0.2 * df["score_voltrend"]
        + 0.2 * df["score_updown"]
        + 0.2 * df["score_vwap_dist"]
    )

    df["final_signal"] = 0.4 * df["factor_score"] + 0.35 * df["momentum_score_v2"] + 0.25 * df["volume_score"]

    # Sector comes from data/universe.csv (optional)
    try:
        u = load_universe_df()
        df = df.merge(u, on="ticker", how="left")
        df["sector"] = df["sector"].fillna("Other")
    except Exception:
        df["sector"] = "Other"

    return df


def add_atr(prices: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """Add ATR proxy (rolling mean of true range) per ticker."""
    df = prices.sort_values(["ticker", "date"]).copy()
    df["true_range"] = df["high"] - df["low"]
    df["atr"] = df.groupby("ticker")["true_range"].transform(lambda x: x.rolling(window).mean())
    return df


# =========================
# Report generation + email
# =========================

def generate_morning_quant_report(signals_df: pd.DataFrame, n_ideas: int = 5) -> str:
    """
    Build a simple morning text report from latest day in signals_df.
    signals_df should include: date, ticker, sector, final_signal, factor_score, momentum_score_v2, volume_score
    """
    if signals_df is None or signals_df.empty:
        return "No signals available."

    df = signals_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    latest_date = df["date"].max()
    today = df[df["date"] == latest_date].copy()

    breadth = (today["final_signal"] >= 60).mean() * 100 if len(today) else 0

    top = today.sort_values("final_signal", ascending=False).head(n_ideas)

    lines = []
    lines.append("=== AI Analyst Market Note ===")
    lines.append(f"Date: {latest_date.date()}")
    lines.append(f"Regime: {'Moderately Bullish' if breadth >= 50 else 'Mixed'}")
    lines.append(f"Breadth: {breadth:.1f}% of tickers strong (final_signal ≥ 60)")
    lines.append("")

    if "sector" in today.columns:
        lines.append("=== Sector Themes ===")
        sect = (
            today.groupby("sector")
            .agg(
                avgFinalSignal=("final_signal", "mean"),
                avgFactor=("factor_score", "mean"),
                avgMomentum=("momentum_score_v2", "mean"),
                avgVolume=("volume_score", "mean"),
                n=("ticker", "count"),
            )
            .sort_values("avgFinalSignal", ascending=False)
        )
        for s, r in sect.iterrows():
            lines.append(
                f"- {s}: avg FinalSignal {r['avgFinalSignal']:.1f}, Factor {r['avgFactor']:.1f}, "
                f"Momentum {r['avgMomentum']:.1f}, Volume {r['avgVolume']:.1f} ({int(r['n'])} names)"
            )
        lines.append("")

    lines.append("=== High-Conviction Setups (Top FinalSignals) ===")
    for _, row in top.iterrows():
        lines.append(
            f"- {row['ticker']} ({row.get('sector','Other')}): FinalSignal {row['final_signal']:.1f} "
            f"[Factor {row['factor_score']:.1f}, Momentum {row['momentum_score_v2']:.1f}, Volume {row['volume_score']:.1f}]"
        )

    return "\n".join(lines)


def send_report_via_email(report_text: str) -> None:
    """
    Send the report via Gmail SMTP using app password.
    Requires env vars:
      SENDER_EMAIL, SENDER_APP_PASSWORD, RECIPIENT_EMAIL
    """
    sender = os.environ.get("SENDER_EMAIL")
    app_password = os.environ.get("SENDER_APP_PASSWORD")
    recipient = os.environ.get("RECIPIENT_EMAIL", sender)

    if not sender or not app_password or not recipient:
        raise ValueError("Missing SENDER_EMAIL, SENDER_APP_PASSWORD, or RECIPIENT_EMAIL env vars.")

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = "Morning Quant Report"
    msg.attach(MIMEText(report_text, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, app_password)
        server.sendmail(sender, [recipient], msg.as_string())


def main() -> None:
    prices = download_prices(TICKERS, period="1y", interval="1d")
    factor_df = fetch_factor_data(TICKERS)
    factor_scores = build_factor_scores(factor_df)
    signals = compute_full_signals(prices, factor_scores)

    report = generate_morning_quant_report(signals, n_ideas=int(os.environ.get("N_IDEAS", "5")))
    print(report)

    # Email optional (workflow may run with or without)
    if os.environ.get("SEND_EMAIL", "1") == "1":
        send_report_via_email(report)


if __name__ == "__main__":
    main()
