"""Fetch liquid-ETF factor proxy prices and cache them for the attribution engine.

Proxy set (pragmatic, liquid, documented in ATTRIBUTION_REPORT.md):
  market  = SPY
  momentum= MTUM - SPY
  size    = IWM  - SPY
  value   = IVE  - IVW   (large-value minus large-growth)
  quality = QUAL - SPY
  lowvol  = USMV - SPY

Single source = yfinance auto_adjust close (splits+dividends adjusted), matching the
name price panel (B_price_panel.parquet) built the same way. RESEARCH_ONLY / read-only
on all operational artifacts. Re-runnable; overwrites the cache parquet in place.
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd

OUT = Path("outputs/research/attribution_2026-07-14")
CACHE = OUT / "factor_prices.parquet"
ETFS = ["SPY", "MTUM", "IWM", "IVE", "IVW", "QUAL", "USMV"]
# ^VIX level cached alongside for a single consistent regime taxonomy (LOW/ELEVATED/
# HIGH/CRISIS via canonical thresholds 20/30/40 from sleeves/sleeve_trend/config.py).
VIX_SYMBOL = "^VIX"
# Widen the pull window so any --start/--end inside the strategy history is covered.
START, END = "2026-01-15", "2026-07-14"


def main() -> None:
    import yfinance as yf
    raw = yf.download(ETFS + [VIX_SYMBOL], start=START, end=END, progress=False,
                      auto_adjust=True)
    close = raw["Close"].copy()
    close.index = pd.to_datetime(close.index)
    close = close.sort_index()
    # normalise ^VIX column name to VIX
    if VIX_SYMBOL in close.columns:
        close = close.rename(columns={VIX_SYMBOL: "VIX"})
    nulls = close.isna().sum().to_dict()
    print("factor ETF panel", close.shape, close.index.min().date(), close.index.max().date())
    print("nulls per col:", nulls)
    missing = [e for e in ETFS if e not in close.columns or close[e].notna().sum() == 0]
    if missing:
        raise SystemExit(f"missing factor ETFs from yfinance: {missing}")
    long = close.reset_index().melt(id_vars=[close.index.name or "index"],
                                    var_name="ticker", value_name="close")
    long.columns = ["date", "ticker", "close"]
    long = long.dropna(subset=["close"])
    long["date"] = pd.to_datetime(long["date"])
    OUT.mkdir(parents=True, exist_ok=True)
    long.to_parquet(CACHE, index=False)
    print("saved", CACHE, long.shape)


if __name__ == "__main__":
    main()
