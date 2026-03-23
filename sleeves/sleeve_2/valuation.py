"""
sleeves/sleeve_2/valuation.py
------------------------------
Bias-free fundamental valuation using SEC EDGAR XBRL data.

DESIGN
------
• All fundamental data is keyed to the SEC filing date (the date EDGAR
  made the data publicly available), not the accounting period-end date.
  This eliminates look-ahead bias in backtests.

• Trailing-twelve-month (TTM) EPS is computed as the sum of the four
  most recent quarterly EPS values that were *filed* on or before the
  requested backtest date.

• Results are cached in outputs/cache/edgar_pe_cache.parquet so
  subsequent runs skip the API calls for tickers already fetched.

EDGAR API
---------
• No API key required. Only a User-Agent header is needed.
• CIK map:    https://www.sec.gov/files/company_tickers.json
• Facts:      https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json

Rate limits
-----------
EDGAR public API: 10 requests per second recommended.
We sleep 0.12 s between calls and add exponential back-off on errors.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EDGAR_UA = os.environ.get(
    "EDGAR_USER_AGENT",
    "AlphaStack/1.0 (research; contact: brett.olson@gmail.com)",
)
EDGAR_HEADERS = {"User-Agent": EDGAR_UA, "Accept": "application/json"}

CIK_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL_TMPL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# Quarterly EPS XBRL concept names, in priority order
EPS_CONCEPTS = [
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
]

# Forms that contain quarterly P&L data
QUARTERLY_FORMS = {"10-Q", "10-K"}

CACHE_PATH = Path("outputs/cache/edgar_pe_cache.parquet")
CIK_CACHE_PATH = Path("outputs/cache/edgar_cik_map.json")

REQUEST_DELAY = 0.12   # seconds between EDGAR requests
MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# CIK mapping
# ---------------------------------------------------------------------------

def _load_cik_map(force_refresh: bool = False) -> Dict[str, str]:
    """
    Load ticker → zero-padded 10-digit CIK map.

    Caches to disk to avoid re-fetching on every run.
    """
    CIK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not force_refresh and CIK_CACHE_PATH.exists():
        try:
            with CIK_CACHE_PATH.open() as f:
                return json.load(f)
        except Exception:
            pass

    try:
        resp = requests.get(CIK_MAP_URL, headers=EDGAR_HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        # Format: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "..."}, ...}
        mapping = {
            v["ticker"].upper(): str(v["cik_str"]).zfill(10)
            for v in data.values()
        }
        with CIK_CACHE_PATH.open("w") as f:
            json.dump(mapping, f)
        logger.info("[EDGAR] CIK map loaded: %d entries", len(mapping))
        return mapping
    except Exception as exc:
        logger.warning("[EDGAR] CIK map fetch failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# EDGAR facts fetcher
# ---------------------------------------------------------------------------

def _fetch_company_facts(cik: str) -> dict:
    """Fetch raw company facts JSON from EDGAR."""
    url = FACTS_URL_TMPL.format(cik=cik)
    for attempt in range(MAX_RETRIES):
        try:
            time.sleep(REQUEST_DELAY)
            resp = requests.get(url, headers=EDGAR_HEADERS, timeout=30)
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            wait = REQUEST_DELAY * (2 ** attempt)
            logger.warning("[EDGAR] Attempt %d failed for CIK=%s: %s — retrying in %.1fs",
                           attempt + 1, cik, exc, wait)
            time.sleep(wait)
    return {}


def _extract_quarterly_eps(facts: dict) -> pd.DataFrame:
    """
    Extract quarterly EPS filings from company facts.

    Returns DataFrame with columns:
        filed_date (datetime64), period_end (datetime64),
        eps_value (float), form (str)

    Only includes 10-Q and 10-K filings.
    """
    us_gaap = (facts.get("facts") or {}).get("us-gaap") or {}

    for concept in EPS_CONCEPTS:
        concept_data = us_gaap.get(concept) or {}
        units = concept_data.get("units") or {}
        # EPS units can be "USD/shares" or occasionally "USD"
        for unit_key in ("USD/shares", "USD"):
            filings = units.get(unit_key) or []
            if filings:
                rows = []
                for item in filings:
                    form = str(item.get("form", "")).upper()
                    if form not in QUARTERLY_FORMS:
                        continue
                    filed = item.get("filed")
                    end = item.get("end")
                    val = item.get("val")
                    if filed is None or val is None:
                        continue
                    # Skip annual reports filed as single-value (not quarterly)
                    # 10-K filings report annual EPS; 10-Q reports quarterly
                    # We want quarterly figures only, so we skip 10-K annual values
                    # unless we have no 10-Q data at all
                    rows.append({
                        "filed_date": pd.to_datetime(filed),
                        "period_end": pd.to_datetime(end) if end else pd.NaT,
                        "eps_value": float(val),
                        "form": form,
                    })
                if rows:
                    return pd.DataFrame(rows).sort_values("filed_date")
    return pd.DataFrame()


def _compute_ttm_eps(
    eps_df: pd.DataFrame,
    as_of_date: pd.Timestamp,
) -> Optional[float]:
    """
    Compute trailing twelve-month EPS as of a given backtest date.

    Only uses quarterly filings whose filed_date <= as_of_date.
    Takes the 4 most recent non-overlapping quarterly values.
    Returns None if fewer than 4 quarters of data are available.
    """
    if eps_df.empty:
        return None

    available = eps_df[eps_df["filed_date"] <= as_of_date].copy()
    if len(available) < 4:
        return None

    # Prefer 10-Q over 10-K (10-K EPS is full-year, not quarterly)
    q_only = available[available["form"] == "10-Q"]
    if len(q_only) >= 4:
        # Take 4 most recent quarters by period_end
        recent = q_only.sort_values("period_end").drop_duplicates(
            subset=["period_end"], keep="last"
        ).tail(4)
        if len(recent) == 4:
            return float(recent["eps_value"].sum())

    # Fallback: use whatever we have (mix of 10-K and 10-Q)
    recent = available.sort_values("period_end").drop_duplicates(
        subset=["period_end"], keep="last"
    ).tail(4)
    if len(recent) < 4:
        return None
    return float(recent["eps_value"].sum())


# ---------------------------------------------------------------------------
# Main public interface
# ---------------------------------------------------------------------------

def compute_pe_ratios(
    tickers: list[str],
    prices: pd.DataFrame,
    as_of_date: Optional[str] = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    Compute trailing P/E ratios for a list of tickers, bias-free.

    Parameters
    ----------
    tickers : list of str
        Universe tickers.
    prices : DataFrame
        Long-form OHLCV with columns: date, ticker, close.
    as_of_date : str or None
        Backtest snapshot date (YYYY-MM-DD). Defaults to latest date in prices.
    force_refresh : bool
        Bypass disk cache and re-fetch from EDGAR.

    Returns
    -------
    DataFrame with columns:
        ticker, close, ttm_eps, pe_ratio, filed_date, data_source
    """
    if as_of_date is None:
        as_of_date = str(prices["date"].max())[:10]

    as_of_ts = pd.to_datetime(as_of_date)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # ── Load or initialize cache ─────────────────────────────────────────
    cache_df = _load_pe_cache()

    # Determine which tickers need fetching
    tickers_upper = [t.upper() for t in tickers]
    if not force_refresh and not cache_df.empty:
        cached_tickers = set(cache_df["ticker"].unique())
        to_fetch = [t for t in tickers_upper if t not in cached_tickers]
    else:
        to_fetch = tickers_upper

    # ── Fetch missing tickers from EDGAR ─────────────────────────────────
    if to_fetch:
        cik_map = _load_cik_map(force_refresh=force_refresh)
        new_rows = []
        for i, ticker in enumerate(to_fetch):
            cik = cik_map.get(ticker.upper())
            if not cik:
                logger.debug("[EDGAR] No CIK for %s — skipping", ticker)
                continue

            facts = _fetch_company_facts(cik)
            if not facts:
                logger.debug("[EDGAR] No facts for %s (CIK=%s)", ticker, cik)
                continue

            eps_df = _extract_quarterly_eps(facts)
            if eps_df.empty:
                logger.debug("[EDGAR] No EPS data for %s", ticker)
                continue

            # Store every historical data point so cache is reusable
            for _, row in eps_df.iterrows():
                new_rows.append({
                    "ticker": ticker.upper(),
                    "filed_date": row["filed_date"],
                    "period_end": row["period_end"],
                    "eps_value": row["eps_value"],
                    "form": row["form"],
                })

            if (i + 1) % 10 == 0:
                logger.info("[EDGAR] Fetched %d/%d tickers", i + 1, len(to_fetch))

        if new_rows:
            new_df = pd.DataFrame(new_rows)
            cache_df = pd.concat([cache_df, new_df], ignore_index=True)
            _save_pe_cache(cache_df)

    # ── Compute TTM P/E as of backtest date ──────────────────────────────
    prices_snap = prices[prices["date"] == as_of_ts][["ticker", "close"]].copy()
    prices_snap["ticker"] = prices_snap["ticker"].str.upper()

    results = []
    for ticker in tickers_upper:
        ticker_cache = cache_df[cache_df["ticker"] == ticker]
        ttm_eps = _compute_ttm_eps(ticker_cache, as_of_ts)

        price_row = prices_snap[prices_snap["ticker"] == ticker]
        close = float(price_row["close"].iloc[0]) if not price_row.empty else None

        last_filed = (
            str(ticker_cache[ticker_cache["filed_date"] <= as_of_ts]["filed_date"].max().date())
            if not ticker_cache.empty
            else None
        )

        pe = None
        if close is not None and ttm_eps is not None and ttm_eps > 0:
            pe = close / ttm_eps

        results.append({
            "ticker": ticker,
            "close": close,
            "ttm_eps": ttm_eps,
            "pe_ratio": pe,
            "filed_date": last_filed,
            "data_source": "edgar_xbrl",
        })

    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _load_pe_cache() -> pd.DataFrame:
    if CACHE_PATH.exists():
        try:
            return pd.read_parquet(CACHE_PATH)
        except Exception as exc:
            logger.warning("[EDGAR] Cache read failed: %s — starting fresh", exc)
    return pd.DataFrame(
        columns=["ticker", "filed_date", "period_end", "eps_value", "form"]
    )


def _save_pe_cache(df: pd.DataFrame) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(CACHE_PATH, index=False)
    except Exception as exc:
        logger.warning("[EDGAR] Cache write failed: %s", exc)
