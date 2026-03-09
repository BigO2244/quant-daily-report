"""
Alpha Stack — SEC EDGAR XBRL Client
======================================
Provides PIT-safe access to company fundamentals via the SEC EDGAR
full-text XBRL API (https://data.sec.gov/api/xbrl/companyfacts/).

Key properties:
  - Every reported fact includes `filed` (date accepted by SEC), which is
    the correct PIT gate.  We NEVER use `end` (period-end) as a filter —
    a company with a December fiscal year end files its 10-K in February or
    March; using the period-end date would introduce look-ahead bias of
    60-90 days.
  - Raw facts are cached as Parquet on disk; only fetched once per CIK.
  - CIK ↔ ticker mapping fetched from the SEC bulk tickers file once per
    session and cached in memory.

Rate limits
  SEC requires: requests ≤ 10/second; User-Agent header with contact info.
  We enforce a 120ms minimum gap between requests via a module-level lock.

XBRL concepts tracked (US-GAAP):
  - EarningsPerShareDiluted          → EPS (preferred) / EarningsPerShareBasic
  - StockholdersEquity               → book value (equity)
  - CommonStockSharesOutstanding     → shares outstanding
  - NetCashProvidedByUsedInOperatingActivities  → operating cash flow
  - PaymentsToAcquirePropertyPlantAndEquipment  → capex
  - Assets                           → total assets
  - Liabilities                      → total liabilities
  - NetIncomeLoss                    → net income

All units normalised to USD (assets, equity, cash flows) or USD/share (EPS).
"""

from __future__ import annotations

import json
import logging
import os
import time
import warnings
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level rate limiter (shared across all EdgarClient instances)
# ---------------------------------------------------------------------------
_last_request_ts: float = 0.0
_MIN_INTERVAL_S: float = 0.12   # 120 ms → < 10 req/s


def _rate_limited_get(url: str, session) -> dict:
    """Fetch JSON from EDGAR with rate-limiting enforced."""
    global _last_request_ts
    elapsed = time.monotonic() - _last_request_ts
    if elapsed < _MIN_INTERVAL_S:
        time.sleep(_MIN_INTERVAL_S - elapsed)
    resp = session.get(url, timeout=30)
    _last_request_ts = time.monotonic()
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# XBRL concepts we care about
# ---------------------------------------------------------------------------
TRACKED_CONCEPTS: Dict[str, List[str]] = {
    # field_name → list of XBRL concept names (in priority order)
    "eps_diluted": [
        "EarningsPerShareDiluted",
        "EarningsPerShareBasic",
    ],
    "equity": [
        "StockholdersEquity",
        "StockholdersEquityAttributableToParent",
    ],
    "shares_outstanding": [
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
    ],
    "operating_cf": [
        "NetCashProvidedByUsedInOperatingActivities",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PurchaseOfPropertyPlantAndEquipmentNet",
    ],
    "net_income": [
        "NetIncomeLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
}

# The SEC EDGAR API base
_EDGAR_BASE = "https://data.sec.gov"
_TICKERS_URL = f"{_EDGAR_BASE}/files/company_tickers.json"
_FACTS_URL_TEMPLATE = f"{_EDGAR_BASE}/api/xbrl/companyfacts/CIK{{cik10}}.json"


class EdgarClient:
    """
    EDGAR XBRL client with disk-based Parquet caching.

    Parameters
    ----------
    cache_dir : Path or str
        Directory for Parquet caches.  Default: data/alpha_stack_cache/edgar/
    user_agent : str
        Required by SEC.  Format: "CompanyName contact@email.com"
    ttl_days : int
        Number of days before re-fetching raw company facts. Default: 7.
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        user_agent: str = "AlphaStackResearch alphastack@research.local",
        ttl_days: int = 7,
    ) -> None:
        self._cache_dir = Path(cache_dir) if cache_dir else _default_cache_dir()
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._user_agent = user_agent
        self._ttl_days = ttl_days

        # In-process caches
        self._cik_map: Optional[Dict[str, str]] = None       # ticker → zero-padded CIK
        self._facts_cache: Dict[str, pd.DataFrame] = {}      # cik10 → facts DataFrame
        self._warned_tickers: set = set()                     # tickers already warned about
        self._failed_ciks: set = set()                        # CIKs that returned 404 (negative cache)
        self._warned_failed_ciks: set = set()                 # CIKs already warned about failure

        try:
            import requests
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": self._user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Host": "data.sec.gov",
            })
            self._requests_available = True
        except ImportError:
            self._session = None
            self._requests_available = False
            logger.warning("[EDGAR] requests not installed; network access unavailable")

    # ------------------------------------------------------------------ #
    # Public interface                                                     #
    # ------------------------------------------------------------------ #

    def lookup_cik(self, ticker: str) -> Optional[str]:
        """
        Return 10-digit zero-padded CIK for ticker, or None.
        
        Tries multiple normalized variants:
        1. Exact match (uppercase)
        2. Strip trailing periods/hyphens
        3. Replace periods with hyphens and vice versa
        
        Warns only once per unmapped ticker to reduce log spam.
        """
        if self._cik_map is None:
            self._cik_map = self._load_cik_map()
        
        # Normalize to uppercase
        ticker_upper = ticker.upper()
        
        # Try exact match first
        if ticker_upper in self._cik_map:
            return self._cik_map[ticker_upper]
        
        # Try fallback variants
        variants = self._get_ticker_variants(ticker_upper)
        for variant in variants:
            if variant in self._cik_map:
                logger.debug("[EDGAR] Matched %s using variant %s", ticker, variant)
                return self._cik_map[variant]
        
        # Not found - warn once per ticker
        if ticker_upper not in self._warned_tickers:
            logger.warning("[EDGAR] Unknown ticker: %s (no CIK found)", ticker)
            self._warned_tickers.add(ticker_upper)
        
        return None

    @staticmethod
    def _get_ticker_variants(ticker: str) -> List[str]:
        """Generate normalized ticker variants for fallback lookup."""
        variants = []
        
        # Remove trailing punctuation
        stripped = ticker.rstrip('.-')
        if stripped != ticker:
            variants.append(stripped)
        
        # Replace periods with hyphens
        if '.' in ticker:
            variants.append(ticker.replace('.', '-'))
        
        # Replace hyphens with periods
        if '-' in ticker:
            variants.append(ticker.replace('-', '.'))
        
        # Remove all punctuation
        cleaned = ticker.replace('.', '').replace('-', '')
        if cleaned != ticker:
            variants.append(cleaned)
        
        return variants

    def get_mapping_coverage(self, tickers: List[str]) -> dict:
        """
        Return diagnostic info about ticker→CIK mapping coverage.
        
        Returns
        -------
        dict with keys:
            total: total tickers in input
            mapped: number with CIK found
            coverage_pct: percentage mapped
            unmapped: list of unmapped tickers
            map_size: total entries in CIK map
        """
        if self._cik_map is None:
            self._cik_map = self._load_cik_map()
        
        mapped_count = 0
        unmapped = []
        
        for ticker in tickers:
            if self.lookup_cik(ticker) is not None:
                mapped_count += 1
            else:
                unmapped.append(ticker)
        
        return {
            "total": len(tickers),
            "mapped": mapped_count,
            "coverage_pct": 100.0 * mapped_count / len(tickers) if tickers else 0.0,
            "unmapped": sorted(unmapped),
            "map_size": len(self._cik_map),
        }

    def get_company_facts(self, ticker: str) -> Optional[pd.DataFrame]:
        """
        Return a DataFrame of all XBRL facts for the given ticker.

        Columns: cik, ticker, concept, field_name, filed, end, form, val, units

        Returns None if the ticker is unknown or data cannot be fetched.
        PIT safety: always filter on `filed` date, never on `end` date.
        """
        cik10 = self.lookup_cik(ticker)
        if cik10 is None:
            # Warning already issued by lookup_cik (once per ticker)
            return None

        # Check negative cache: skip if this CIK previously returned 404
        if cik10 in self._failed_ciks:
            # Already warned once, don't spam logs
            return None

        if cik10 in self._facts_cache:
            return self._facts_cache[cik10]

        # Try disk cache first
        cached = self._load_parquet_cache(cik10)
        if cached is not None:
            self._facts_cache[cik10] = cached
            return cached

        # Fetch from SEC
        if not self._requests_available:
            if ticker.upper() not in self._warned_tickers:
                logger.warning("[EDGAR] requests not installed; cannot fetch %s", ticker)
                self._warned_tickers.add(ticker.upper())
            return None

        df = self._fetch_and_flatten(cik10, ticker)
        if df is not None:
            self._save_parquet_cache(cik10, df)
            self._facts_cache[cik10] = df
        return df

    def get_pit_value(
        self,
        ticker: str,
        field_name: str,
        as_of_date: str,
        form_types: tuple = ("10-K", "10-Q"),
        require_shares_denom: bool = False,
    ) -> Optional[float]:
        """
        Return the most-recently-filed value for `field_name` with
        `filed` date <= `as_of_date`.  Returns None if unavailable.

        PIT guarantee: only filings accepted by the SEC on or before
        `as_of_date` are considered.
        """
        df = self.get_company_facts(ticker)
        if df is None or df.empty:
            return None

        mask = (
            (df["field_name"] == field_name)
            & (df["form"].isin(form_types))
            & (df["filed"] <= pd.Timestamp(as_of_date))
        )
        subset = df[mask]
        if subset.empty:
            return None

        return float(subset.sort_values("filed").iloc[-1]["val"])

    def get_ttm_value(
        self,
        ticker: str,
        field_name: str,
        as_of_date: str,
        n_quarters: int = 4,
    ) -> Optional[float]:
        """
        Return trailing-twelve-months aggregate for quarterly flow fields
        (operating_cf, capex, net_income).  Sums the last `n_quarters`
        quarterly filings with `filed` <= `as_of_date`.

        Returns None if fewer than n_quarters periods are available.
        """
        df = self.get_company_facts(ticker)
        if df is None or df.empty:
            return None

        mask = (
            (df["field_name"] == field_name)
            & (df["form"] == "10-Q")
            & (df["filed"] <= pd.Timestamp(as_of_date))
        )
        quarterly = df[mask].copy()
        if len(quarterly) < n_quarters:
            # Fall back: try annual (10-K) for TTM
            annual_mask = (
                (df["field_name"] == field_name)
                & (df["form"] == "10-K")
                & (df["filed"] <= pd.Timestamp(as_of_date))
            )
            annual = df[annual_mask]
            if annual.empty:
                return None
            return float(annual.sort_values("filed").iloc[-1]["val"])

        # De-duplicate by period-end; take most recent filing per end-date
        quarterly = (
            quarterly.sort_values("filed")
            .drop_duplicates(subset="end", keep="last")
            .sort_values("end")
        )
        last_n = quarterly.tail(n_quarters)
        return float(last_n["val"].sum())

    def refresh_cache(self, ticker: str) -> bool:
        """Force re-fetch from SEC for `ticker`. Returns True on success."""
        cik10 = self.lookup_cik(ticker)
        if cik10 is None:
            return False
        self._facts_cache.pop(cik10, None)
        cache_path = self._parquet_path(cik10)
        if cache_path.exists():
            cache_path.unlink()
        df = self._fetch_and_flatten(cik10, ticker)
        if df is not None:
            self._save_parquet_cache(cik10, df)
            self._facts_cache[cik10] = df
            return True
        return False

    # ------------------------------------------------------------------ #
    # CIK map loading                                                     #
    # ------------------------------------------------------------------ #

    def _load_cik_map(self) -> Dict[str, str]:
        """
        Load ticker→CIK mapping from local cache or SEC.
        
        Caching strategy:
        1. Check sec_ticker_map.json (normalized, structured cache)
        2. Fall back to company_tickers.json (raw SEC format)
        3. Fetch from SEC if both missing or stale
        4. Use bundled default map as last resort
        
        Returns normalized map: uppercase ticker → zero-padded CIK
        """
        # Primary cache: structured normalized map
        primary_cache = self._cache_dir / "sec_ticker_map.json"
        
        # Try primary cache first
        if primary_cache.exists():
            age_days = (time.time() - primary_cache.stat().st_mtime) / 86400
            if age_days < self._ttl_days:
                try:
                    with open(primary_cache) as f:
                        cached_data = json.load(f)
                    logger.info("[EDGAR] Loaded CIK map from cache (%d tickers)", len(cached_data))
                    return cached_data
                except Exception as e:
                    logger.warning("[EDGAR] Failed to load primary cache: %s", e)
        
        # Fallback: try raw SEC cache
        raw_cache = self._cache_dir / "company_tickers.json"
        if raw_cache.exists():
            age_days = (time.time() - raw_cache.stat().st_mtime) / 86400
            if age_days < self._ttl_days:
                try:
                    with open(raw_cache) as f:
                        raw = json.load(f)
                    normalized = self._parse_tickers_json(raw)
                    # Save to primary cache for next time
                    self._save_normalized_map(primary_cache, normalized)
                    return normalized
                except Exception as e:
                    logger.warning("[EDGAR] Failed to load raw cache: %s", e)
        
        # No valid cache - fetch from SEC
        if not self._requests_available:
            logger.warning("[EDGAR] Cannot fetch CIK map (requests unavailable)")
            return self._load_bundled_default()

        try:
            logger.info("[EDGAR] Fetching company tickers from SEC (%s)", _TICKERS_URL)
            raw = _rate_limited_get(_TICKERS_URL, self._session)
            
            # Save raw format (backward compat)
            with open(raw_cache, "w") as f:
                json.dump(raw, f)
            
            # Parse and save normalized format
            normalized = self._parse_tickers_json(raw)
            self._save_normalized_map(primary_cache, normalized)
            
            logger.info("[EDGAR] CIK map loaded: %d tickers", len(normalized))
            return normalized
            
        except Exception as e:
            logger.error("[EDGAR] Failed to fetch tickers: %s", e)
            # Try stale caches
            for cache_path in [primary_cache, raw_cache]:
                if cache_path.exists():
                    logger.info("[EDGAR] Using stale cache: %s", cache_path.name)
                    try:
                        with open(cache_path) as f:
                            data = json.load(f)
                        if cache_path == primary_cache:
                            return data
                        else:
                            return self._parse_tickers_json(data)
                    except Exception:
                        pass
            # Last resort: bundled default
            return self._load_bundled_default()

    def _load_bundled_default(self) -> Dict[str, str]:
        """
        Load bundled default CIK map from package resources.
        
        This is a last-resort fallback when SEC API is unreachable and
        no local caches exist. Contains ~200 major US tickers.
        """
        default_path = Path(__file__).parent / "sec_ticker_map_default.json"
        if not default_path.exists():
            logger.warning("[EDGAR] Bundled default map not found: %s", default_path)
            return {}
        
        try:
            with open(default_path) as f:
                default_map = json.load(f)
            logger.info("[EDGAR] Loaded bundled default CIK map (%d tickers)", len(default_map))
            
            # Save to primary cache for persistence
            primary_cache = self._cache_dir / "sec_ticker_map.json"
            self._save_normalized_map(primary_cache, default_map)
            
            return default_map
        except Exception as e:
            logger.error("[EDGAR] Failed to load bundled default: %s", e)
            return {}

    @staticmethod
    def _save_normalized_map(path: Path, ticker_map: Dict[str, str]) -> None:
        """Save normalized ticker→CIK map to JSON."""
        try:
            with open(path, "w") as f:
                json.dump(ticker_map, f, indent=2, sort_keys=True)
            logger.debug("[EDGAR] Saved normalized map: %s", path.name)
        except Exception as e:
            logger.warning("[EDGAR] Failed to save normalized map: %s", e)

    @staticmethod
    def _parse_tickers_json(raw: dict) -> Dict[str, str]:
        """
        Parse SEC company_tickers.json into normalized ticker→CIK map.
        
        Normalization:
        - Uppercase all tickers
        - Zero-pad CIKs to 10 digits
        - Remove duplicates (last wins)
        """
        result: Dict[str, str] = {}
        for entry in raw.values():
            ticker = str(entry.get("ticker", "")).strip().upper()
            cik = str(entry.get("cik_str", "")).zfill(10)
            if ticker and cik:
                result[ticker] = cik
        return result

    # ------------------------------------------------------------------ #
    # Fetch + flatten XBRL facts                                          #
    # ------------------------------------------------------------------ #

    def _fetch_and_flatten(self, cik10: str, ticker: str) -> Optional[pd.DataFrame]:
        """
        Fetch and flatten company facts from SEC EDGAR API.
        
        On 404 errors, adds CIK to negative cache to prevent repeated retries.
        Warns once per failed CIK to reduce log spam.
        """
        url = _FACTS_URL_TEMPLATE.format(cik10=cik10)
        try:
            logger.info("[EDGAR] Fetching company facts for %s (CIK %s)", ticker, cik10)
            data = _rate_limited_get(url, self._session)
            return self._flatten_facts(data, cik10, ticker)
        except Exception as e:
            # Detect 404 errors (invalid CIK or no data available)
            is_404 = False
            if hasattr(e, 'response') and hasattr(e.response, 'status_code'):
                is_404 = e.response.status_code == 404
            elif '404' in str(e):
                is_404 = True
            
            if is_404:
                # Add to negative cache to prevent repeated retries
                self._failed_ciks.add(cik10)
                # Warn only once per CIK
                if cik10 not in self._warned_failed_ciks:
                    logger.warning(
                        "[EDGAR] CIK %s (ticker %s) returned 404 - no data available or invalid CIK. "
                        "This CIK will be skipped for the rest of this run.",
                        cik10, ticker
                    )
                    self._warned_failed_ciks.add(cik10)
            else:
                # Other errors - log normally
                logger.error("[EDGAR] Fetch failed for %s (CIK %s): %s", ticker, cik10, e)
            
            return None

    def _flatten_facts(self, raw: dict, cik10: str, ticker: str) -> pd.DataFrame:
        """
        Flatten raw EDGAR company facts JSON into a tidy DataFrame.

        Only extracts concepts listed in TRACKED_CONCEPTS.
        Filters to USD-denominated values only.
        """
        rows = []
        us_gaap = raw.get("facts", {}).get("us-gaap", {})

        for field_name, concept_names in TRACKED_CONCEPTS.items():
            for concept in concept_names:
                if concept not in us_gaap:
                    continue
                concept_data = us_gaap[concept]
                units = concept_data.get("units", {})

                # Prefer USD; for EPS use USD/shares
                for unit_label, filings in units.items():
                    if unit_label not in ("USD", "USD/shares"):
                        continue
                    for filing in filings:
                        filed = filing.get("filed")
                        end = filing.get("end")
                        form = filing.get("form", "")
                        val = filing.get("val")
                        if filed is None or val is None:
                            continue
                        rows.append({
                            "cik": cik10,
                            "ticker": ticker,
                            "concept": concept,
                            "field_name": field_name,
                            "filed": pd.Timestamp(filed),
                            "end": pd.Timestamp(end) if end else pd.NaT,
                            "form": form,
                            "val": float(val),
                            "units": unit_label,
                        })
                # Only use the first matching concept per field
                break

        if not rows:
            logger.warning("[EDGAR] No tracked concepts found for %s", ticker)
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        # Keep only standard SEC form types
        df = df[df["form"].isin(["10-K", "10-Q", "10-K/A", "10-Q/A"])].copy()
        df.sort_values(["field_name", "filed"], inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    # ------------------------------------------------------------------ #
    # Parquet cache helpers                                               #
    # ------------------------------------------------------------------ #

    def _parquet_path(self, cik10: str) -> Path:
        return self._cache_dir / f"facts_{cik10}.parquet"

    def _load_parquet_cache(self, cik10: str) -> Optional[pd.DataFrame]:
        path = self._parquet_path(cik10)
        if not path.exists():
            return None
        age_days = (time.time() - path.stat().st_mtime) / 86400
        if age_days > self._ttl_days:
            return None  # stale
        try:
            df = pd.read_parquet(path)
            df["filed"] = pd.to_datetime(df["filed"])
            df["end"] = pd.to_datetime(df["end"])
            logger.debug("[EDGAR] Loaded parquet cache for CIK %s", cik10)
            return df
        except Exception as e:
            logger.warning("[EDGAR] Failed to load cache for %s: %s", cik10, e)
            return None

    def _save_parquet_cache(self, cik10: str, df: pd.DataFrame) -> None:
        path = self._parquet_path(cik10)
        try:
            df.to_parquet(path, index=False)
            logger.debug("[EDGAR] Saved parquet cache for CIK %s", cik10)
        except Exception as e:
            logger.warning("[EDGAR] Failed to save cache: %s", e)


def _default_cache_dir() -> Path:
    """Return default EDGAR cache directory relative to repo root."""
    here = Path(__file__).resolve().parent  # alpha_stack/datastore/
    root = here.parent.parent               # repo root
    return root / "data" / "alpha_stack_cache" / "edgar"
