from __future__ import annotations

import time
from pathlib import Path
import pandas as pd


def _cache_path() -> Path:
    Path("data/cache").mkdir(parents=True, exist_ok=True)
    return Path("data/cache/valuation_snapshot.csv")


def fetch_valuation_snapshot(
    tickers: list[str], force_refresh: bool = False, sleep_s: float = 0.2
) -> pd.DataFrame:
    """
    Fetch a snapshot of valuation metadata for tickers:
      - industry
      - forward_pe
      - trailing_pe

    Uses yfinance .info (snapshot, not historical). Cached to data/cache/valuation_snapshot.csv.
    """
    cache = _cache_path()
    tickers = sorted({t.upper().strip() for t in tickers if t and t.upper().strip()})

    if cache.exists() and not force_refresh:
        try:
            df = pd.read_csv(cache)
            if set(["ticker", "industry", "forward_pe", "trailing_pe"]).issubset(
                df.columns
            ):
                df["ticker"] = df["ticker"].astype(str).str.upper()
                # Return only requested tickers if present
                return df[df["ticker"].isin(tickers)].copy()
        except Exception:
            pass

    # Import lazily so sleeve_1 never depends on it
    import yfinance as yf

    rows = []
    for t in tickers:
        try:
            info = yf.Ticker(t).info or {}
            industry = info.get("industry") or info.get(
                "sector"
            )  # fallback to sector if industry missing
            fpe = info.get("forwardPE")
            tpe = info.get("trailingPE")
            rows.append(
                {
                    "ticker": t,
                    "industry": industry,
                    "forward_pe": fpe,
                    "trailing_pe": tpe,
                }
            )
        except Exception:
            rows.append(
                {"ticker": t, "industry": None, "forward_pe": None, "trailing_pe": None}
            )
        time.sleep(sleep_s)

    out = pd.DataFrame(rows)
    out.to_csv(cache, index=False)
    return out
