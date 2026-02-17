from __future__ import annotations

from pathlib import Path

import pandas as pd

IWB_HOLDINGS_URL = "https://www.ishares.com/us/products/239707/ishares-russell-1000-etf/1467271812596.ajax?fileType=csv&fileName=IWB_holdings&dataType=fund"
SECTOR_ETFS = ["XLC", "XLY", "XLP", "XLE", "XLF", "XLV", "XLI", "XLB", "XLRE", "XLK", "XLU"]
THEMATIC_ETFS = ["SMH", "IGV", "HACK", "CIBR", "XSD", "SOXX", "BOTZ", "ARKQ", "ICLN", "LIT", "FINX", "CLOU"]
BANNED_ETF_TOKENS = ("3X", "2X", "ULTRA", "BEAR", "BULL", "SHORT", "INVERSE")


def download_iwb_holdings(cache_path: str = "data/cache/iwb_holdings.csv") -> pd.DataFrame:
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return pd.read_csv(path)

    df = pd.read_csv(IWB_HOLDINGS_URL, skiprows=9)
    df = df.rename(columns={"Ticker": "ticker"})
    df = df[df["ticker"].notna()].copy()
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    df.to_csv(path, index=False)
    return df


def build_growth_universe() -> list[str]:
    iwb = download_iwb_holdings()
    r1k = sorted(set(iwb["ticker"].dropna().astype(str)))
    universe = sorted(set(r1k + SECTOR_ETFS + THEMATIC_ETFS))
    return [t for t in universe if is_allowed_etf_symbol(t)]


def is_allowed_etf_symbol(symbol: str) -> bool:
    s = str(symbol or "").upper().strip()
    if any(tok in s for tok in BANNED_ETF_TOKENS):
        return False
    leveraged_suffixes = ("2X", "3X", "XS", "XL")
    if s.endswith(leveraged_suffixes) and len(s) <= 5:
        return False
    return True


def liquidity_filter(prices: pd.DataFrame, min_dollar_volume: float = 20_000_000.0) -> pd.DataFrame:
    """Require avg dollar volume >= $20M; fallback keeps rows with missing volume."""
    if prices is None or prices.empty:
        return pd.DataFrame(columns=["ticker", "eligible", "reason"])
    df = prices.copy()
    req = {"ticker", "close", "volume"}
    if not req.issubset(df.columns):
        return pd.DataFrame({"ticker": df.get("ticker", []), "eligible": True, "reason": "fallback_missing_columns"})

    grouped = df.groupby("ticker", as_index=False).agg(avg_dollar_volume=("close", lambda x: 0.0), avg_close=("close", "mean"), avg_volume=("volume", "mean"))
    grouped["avg_dollar_volume"] = grouped["avg_close"] * grouped["avg_volume"]
    grouped["eligible"] = grouped["avg_dollar_volume"] >= float(min_dollar_volume)
    grouped["reason"] = grouped["eligible"].map(lambda x: "ok" if x else "below_adv_threshold")
    return grouped[["ticker", "eligible", "reason", "avg_dollar_volume"]]
