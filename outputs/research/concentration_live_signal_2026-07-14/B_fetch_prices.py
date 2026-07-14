"""B: build the forward-return price panel for the recorded live-signal tickers.

Single consistent source = yfinance auto_adjust close (total-return proxy: adjusts
splits+dividends), 2026-01-15 -> 2026-07-14 (data available through 2026-07-13, the
last close before 'today' 2026-07-14). Using one source avoids SEP/yfinance splice
artifacts. Cross-validated against the SEP cache (closeadj) on the overlap window.
RESEARCH_ONLY / read-only on all operational artifacts.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

OUT = Path("outputs/research/concentration_live_signal_2026-07-14")
SEP = Path("data/research_cache/sharadar_sep")
PANEL_PATH = OUT / "B_price_panel.parquet"

df = pd.read_csv(OUT / "A_recorded_signals_panel.csv")
tickers = sorted(df["ticker"].astype(str).str.upper().unique())
print(f"{len(tickers)} recorded tickers")

import yfinance as yf
raw = yf.download(tickers + ["SPY"], start="2026-01-15", end="2026-07-14",
                  progress=False, auto_adjust=True)
close = raw["Close"].copy()
close.index = pd.to_datetime(close.index)
close = close.sort_index()
print("yf panel", close.shape, close.index.min().date(), close.index.max().date())
missing = [t for t in tickers if t not in close.columns or close[t].notna().sum() == 0]
print("missing/empty from yf:", missing)

# ---- cross-validate vs SEP closeadj on overlap (returns correlation) ----
def sep_close(t):
    p = SEP / f"{t}.csv"
    if not p.exists():
        return None
    s = pd.read_csv(p)
    if "closeadj" not in s or "date" not in s:
        return None
    s = s[["date", "closeadj"]].copy()
    s["date"] = pd.to_datetime(s["date"])
    return s.set_index("date")["closeadj"].sort_index()

corrs = []
for t in tickers:
    sc = sep_close(t)
    if sc is None or t not in close.columns:
        continue
    lo, hi = pd.Timestamp("2026-02-01"), pd.Timestamp("2026-06-09")
    a = close[t].loc[lo:hi].pct_change().dropna()
    b = sc.loc[lo:hi].pct_change().dropna()
    j = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(j) > 20:
        corrs.append(j.iloc[:, 0].corr(j.iloc[:, 1]))
corrs = np.array(corrs)
print(f"SEP-vs-yf daily-return corr over overlap: n={len(corrs)} mean={corrs.mean():.4f} "
      f"median={np.median(corrs):.4f} min={corrs.min():.4f} frac>0.99={np.mean(corrs>0.99):.2f}")

# ---- save long panel ----
long = close.reset_index().melt(id_vars=close.index.name or "index", var_name="ticker", value_name="close")
long.columns = ["date", "ticker", "close"]
long = long.dropna(subset=["close"])
long["date"] = pd.to_datetime(long["date"])
long.to_parquet(PANEL_PATH, index=False)
print("saved", PANEL_PATH, long.shape)
