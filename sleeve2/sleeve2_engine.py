# sleeve2/sleeve2_engine.py
"""Sleeve 2 core logic.

Key conventions:
- Universe is loaded from data/universe.csv and normalized to:
    columns: ticker, sector
- Prices are downloaded from yfinance and returned as a *wide* Close price frame:
    index: date, columns: ticker, values: close
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Dict, List

import numpy as np
import pandas as pd
import yfinance as yf


# ===== Column definitions (data contract) =====
TICKER_COL = "ticker"
BUCKET_COL = "sector"  # Sleeve 2 uses sector as the "bucket"


# ===== Config =====
UNIVERSE_PATH = "data/universe.csv"

# Diversification / sizing
TOP_LONGS = 3
MAX_PER_BUCKET = 1  # at most 1 name per sector in V1

# P/E stress rules (accelerates exits / forces Treasury)
PE_Z_STRESS = 2.0         # "significant deviations" threshold
MOM_LOOKBACK = 20         # momentum lookback (trading days)
MOM_MIN = 0.00            # require non-negative momentum unless deeply cheap
CHEAP_Z = -1.0            # allow negative mom if very cheap

# Robustness for CI (GitHub Actions)
IN_CI = os.getenv("CI", "").lower() == "true" or bool(os.getenv("GITHUB_ACTIONS"))
if IN_CI:
    # Put yfinance timezone cache in a per-run temp folder to avoid sqlite lock collisions
    yf.set_tz_cache_location(tempfile.mkdtemp(prefix="yf_tz_cache_"))


# =========================
# Universe helpers
# =========================
def load_universe(path: str = UNIVERSE_PATH) -> pd.DataFrame:
    """Load and normalize the universe.

    Required columns:
      - ticker
      - sector   (Sleeve 2 bucket)

    Returns a df with columns: ticker, sector
    """
    df = pd.read_csv(path)

    if TICKER_COL not in df.columns:
        raise AssertionError(f"Universe missing '{TICKER_COL}' column. Found: {list(df.columns)}")
    if BUCKET_COL not in df.columns:
        raise AssertionError(f"Universe missing '{BUCKET_COL}' column. Found: {list(df.columns)}")

    df[TICKER_COL] = df[TICKER_COL].astype(str).str.upper().str.strip()
    df[BUCKET_COL] = df[BUCKET_COL].astype(str).str.strip()

    df = df.dropna(subset=[TICKER_COL, BUCKET_COL]).drop_duplicates(subset=[TICKER_COL])
    df = df[[TICKER_COL, BUCKET_COL]].reset_index(drop=True)
    return df


# =========================
# Market / fundamentals data
# =========================
def download_prices(tickers: List[str], start: str = "2020-01-01") -> pd.DataFrame:
    """Download close prices as a wide dataframe.

    Returns:
      close: DataFrame indexed by date, columns are tickers.
    """
    tickers = [t.strip().upper() for t in tickers if str(t).strip()]
    if not tickers:
        raise ValueError("No tickers provided")

    # In CI, avoid any concurrency and add a retry for transient sqlite locks / timeouts.
    attempts = 3 if IN_CI else 1
    last_err: Exception | None = None

    for k in range(attempts):
        try:
            px = yf.download(
                tickers=tickers,
                start=start,
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=False if IN_CI else True,
                timeout=30,
            )
            last_err = None
            break
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (k + 1))

    if last_err is not None:
        raise RuntimeError(f"yfinance download failed: {repr(last_err)}")

    # Normalize output to a simple wide frame of close prices
    if isinstance(px.columns, pd.MultiIndex):
        closes: Dict[str, pd.Series] = {}
        for t in tickers:
            if (t, "Close") in px.columns:
                closes[t] = px[(t, "Close")]
        close = pd.DataFrame(closes)
    else:
        # single ticker case
        close = px[["Close"]].rename(columns={"Close": tickers[0]})

    close = close.dropna(how="all")
    if close.empty:
        raise RuntimeError("No price data returned from yfinance.")

    return close


def fetch_trailing_pe_snapshot(tickers: List[str]) -> pd.Series:
    """V1 uses a *snapshot* trailing P/E (not historical)."""
    pe: Dict[str, float] = {}
    for t in tickers:
        t = str(t).strip().upper()
        try:
            info = yf.Ticker(t).info
            val = info.get("trailingPE", np.nan)
            pe[t] = float(val) if val is not None else np.nan
        except Exception:
            pe[t] = np.nan
    return pd.Series(pe, name="trailingPE").astype(float)


# =========================
# Signals / selection
# =========================
def compute_bucket_zscores(univ: pd.DataFrame, pe: pd.Series) -> pd.DataFrame:
    """Return df with ticker, sector, pe, pe_z (within sector)."""
    df = univ.copy()
    df["pe"] = df[TICKER_COL].map(pe).astype(float)

    def _z(x: pd.Series) -> pd.Series:
        mu = x.mean(skipna=True)
        sd = x.std(skipna=True)
        if sd == 0 or np.isnan(sd):
            return pd.Series(np.zeros(len(x)), index=x.index)
        return (x - mu) / sd

    df["pe_z"] = df.groupby(BUCKET_COL)["pe"].transform(_z)
    return df


def compute_momentum(close: pd.DataFrame, lookback: int = MOM_LOOKBACK) -> pd.Series:
    """Simple lookback momentum using pct_change."""
    if close.shape[0] <= lookback:
        raise ValueError(f"Not enough price history for momentum lookback={lookback}")
    mom = close.pct_change(lookback, fill_method=None).iloc[-1]
    mom.name = "momentum"
    return mom.astype(float)


def build_equity_candidates(pe_z_df: pd.DataFrame, mom: pd.Series) -> pd.DataFrame:
    """Combine value (low pe_z) + momentum (high) into a ranked candidate list."""
    df = pe_z_df.copy()
    df["momentum"] = df[TICKER_COL].map(mom).astype(float)

    # score: cheaper (lower z) is better, higher momentum is better
    df["score"] = (-df["pe_z"].fillna(0.0)) + (df["momentum"].fillna(-1e9))

    # Guardrail: allow negative momentum only if very cheap
    ok = (df["momentum"] >= MOM_MIN) | (df["pe_z"] <= CHEAP_Z)
    df = df[ok].copy()

    df = df.sort_values(["score"], ascending=False).reset_index(drop=True)
    return df


def pick_positions(cands: pd.DataFrame, top_n: int = TOP_LONGS) -> List[str]:
    """Pick up to top_n tickers, max 1 per sector."""
    selected: List[str] = []
    used_buckets: set[str] = set()

    for _, row in cands.iterrows():
        t = row[TICKER_COL]
        b = row[BUCKET_COL]
        if b in used_buckets and MAX_PER_BUCKET <= 1:
            continue
        selected.append(t)
        used_buckets.add(b)
        if len(selected) >= top_n:
            break

    return selected


def build_target_weights(selected: List[str]) -> Dict[str, float]:
    """Equal-weight selected equities (V1)."""
    if not selected:
        return {}
    w = 1.0 / float(len(selected))
    return {t: w for t in selected}
