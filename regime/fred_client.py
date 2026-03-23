"""
fred_client.py
--------------
Thin wrapper around the FRED API for fetching macro time series.

Used by RegimeEngine to pull yield curve, HY spreads, and fed funds data.

Requires:
    FRED_API_KEY environment variable to be set.
    pip install requests

Caching:
    Responses are cached in outputs/regime_validation/.fred_cache/ as parquet
    files (one per series).  Pass force_refresh=True to bypass the cache.
"""

import logging
import os
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
DEFAULT_CACHE_DIR = Path("outputs/regime_validation/.fred_cache")


class FredClient:
    """
    Fetches FRED series observations and returns them as DataFrames.

    Parameters
    ----------
    api_key : str, optional
        FRED API key.  Defaults to the FRED_API_KEY environment variable.
    cache_dir : Path, optional
        Directory for parquet cache files.  Defaults to
        outputs/regime_validation/.fred_cache/
    """

    def __init__(
        self,
        api_key: str | None = None,
        cache_dir: Path | None = None,
    ):
        self._api_key = api_key or os.environ.get("FRED_API_KEY", "")
        if not self._api_key:
            raise EnvironmentError(
                "FRED_API_KEY is not set. "
                "Export it before running: export FRED_API_KEY=your_key"
            )
        self._cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def fetch_many(
        self,
        series_map: dict[str, str],
        start: str = "2005-01-01",
        end: str | None = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        Fetch multiple FRED series and return a single wide DataFrame.

        Parameters
        ----------
        series_map : dict
            Keys   = FRED series IDs  (e.g. "T10Y2Y")
            Values = output column names (e.g. "yield_curve_2s10s")
        start : str
            Start date in YYYY-MM-DD format.
        end : str, optional
            End date.  Defaults to today.
        force_refresh : bool
            If True, bypass cache and re-fetch from FRED.

        Returns
        -------
        pd.DataFrame
            DatetimeIndex, one column per series (named by the values of series_map).
            Missing observations are NaN.
        """
        frames = []
        for series_id, col_name in series_map.items():
            s = self.fetch_series(
                series_id=series_id,
                col_name=col_name,
                start=start,
                end=end,
                force_refresh=force_refresh,
            )
            frames.append(s)

        if not frames:
            return pd.DataFrame()

        combined = pd.concat(frames, axis=1).sort_index()
        combined = combined.replace(".", float("nan"))  # FRED uses "." for missing
        combined = combined.apply(pd.to_numeric, errors="coerce")
        return combined

    def fetch_series(
        self,
        series_id: str,
        col_name: str | None = None,
        start: str = "2005-01-01",
        end: str | None = None,
        force_refresh: bool = False,
    ) -> pd.Series:
        """
        Fetch a single FRED series.

        Returns a pd.Series indexed by date, named col_name (or series_id).
        """
        col_name = col_name or series_id
        cache_path = self._cache_dir / f"{series_id}.parquet"

        if not force_refresh and cache_path.exists():
            try:
                cached = pd.read_parquet(cache_path)
                s = cached.iloc[:, 0]
                s.name = col_name
                logger.info("FRED cache hit: %s (%d rows)", series_id, len(s))
                return s
            except Exception as e:
                logger.warning("FRED cache read failed for %s: %s — refetching", series_id, e)

        logger.info("Fetching FRED series: %s (start=%s)", series_id, start)
        params = {
            "series_id":        series_id,
            "api_key":          self._api_key,
            "file_type":        "json",
            "observation_start": start,
            "sort_order":       "asc",
        }
        if end:
            params["observation_end"] = end

        try:
            resp = requests.get(FRED_BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise RuntimeError(f"FRED API request failed for {series_id}: {e}") from e

        observations = data.get("observations", [])
        if not observations:
            logger.warning("FRED returned 0 observations for %s", series_id)
            return pd.Series(name=col_name, dtype=float)

        df = pd.DataFrame(observations)[["date", "value"]]
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df["value"] = df["value"].replace(".", float("nan"))
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        s = df["value"].rename(col_name)

        # Cache as parquet
        try:
            s.to_frame().to_parquet(cache_path)
            logger.info("FRED cached: %s → %s (%d rows)", series_id, cache_path, len(s))
        except Exception as e:
            logger.warning("FRED cache write failed for %s: %s", series_id, e)

        return s
