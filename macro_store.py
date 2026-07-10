"""
macro_store.py  —  Caerus / Alpha Stack
Point-in-time correct interface to FRED macro data.

ALL macro reads in Caerus should go through this class.
The .get() method enforces the PIT gate: it never returns data
observed after as_of_date, eliminating look-ahead bias.

Parquet files are produced by fred_ingestion.py (one per series,
stored in data/macro/{SERIES_ID}.parquet with columns: date, value, series_id).

Usage:
    from macro_store import MacroStore

    ms = MacroStore('data/macro')

    # Single point — PIT correct (last observation on or before date)
    curve = ms.get('T10Y2Y', '2022-06-30')

    # Time series — PIT correct across a date range
    series = ms.get_series('BAMLH0A0HYM2', '2020-01-01', '2022-12-31')

    # Latest available observation (no PIT gate)
    hy = ms.get_latest('BAMLH0A0HYM2')

    # Multiple series as a wide DataFrame as of one date
    snap = ms.get_many(['T10Y2Y', 'BAMLH0A0HYM2'], '2022-06-30')

    # Wide DataFrame over a date range (for regime engine)
    macro_df = ms.get_frame(['T10Y2Y', 'BAMLH0A0HYM2'],
                            '2005-01-01', '2022-12-31')
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional


class MacroStore:
    """
    Point-in-time correct interface to Caerus FRED macro data.

    Key guarantee: every value returned was publicly observable
    on or before the as_of_date passed to the query. No future data
    ever leaks into a historical query.

    Parameters
    ----------
    store_path : str or Path
        Directory containing per-series parquet files produced by
        fred_ingestion.py (e.g. 'data/macro').
    """

    REQUIRED_COLS = {'date', 'value', 'series_id'}

    def __init__(self, store_path: str | Path = 'data/macro'):
        self._path  = Path(store_path)
        self._cache: dict[str, pd.Series] = {}

        if not self._path.exists():
            raise FileNotFoundError(
                f"MacroStore path not found: {self._path.resolve()}\n"
                f"Run fred_ingestion.py first."
            )

    # ── Core read interface ───────────────────────────────────────────

    def get(
        self,
        series_id:  str,
        as_of_date: str | pd.Timestamp,
    ) -> float | None:
        """
        Return the last observed value of series_id on or before as_of_date.

        Parameters
        ----------
        series_id  : FRED series ID (e.g. 'T10Y2Y')
        as_of_date : PIT gate — only observations on/before this date are used

        Returns
        -------
        Most recent observed value, or None if no data available.
        """
        s = self._load(series_id)
        if s is None:
            return None

        cutoff   = pd.Timestamp(as_of_date)
        eligible = s[s.index <= cutoff].dropna()

        if eligible.empty:
            return None

        return float(eligible.iloc[-1])

    def get_series(
        self,
        series_id:  str,
        start:      str | pd.Timestamp,
        end:        str | pd.Timestamp,
        freq:       str = 'B',
        fill:       bool = True,
    ) -> pd.Series:
        """
        Return a PIT-correct time series of series_id between start and end.

        Each date reflects the last observation available on or before that date
        — i.e. what a live system would have seen on that day. Gaps (e.g. weekends,
        holidays, monthly series) are forward-filled by default.

        Parameters
        ----------
        freq : Pandas date frequency. 'B' = business days (default).
               Use 'M' or 'Q' for lower-frequency series.
        fill : If True (default), forward-fill NaN gaps after alignment.
               Set to False to retain NaN at dates with no prior observation.

        Returns
        -------
        pd.Series indexed by date, name=series_id. NaN where no data yet available.
        """
        s = self._load(series_id)
        if s is None:
            return pd.Series(dtype=float, name=series_id)

        start_ts = pd.Timestamp(start)
        end_ts   = pd.Timestamp(end)
        dates    = pd.date_range(start_ts, end_ts, freq=freq)

        # Reindex to target dates, forward-fill from prior observations
        # First: restrict to data on or before end_ts to avoid future leakage
        s_pit = s[s.index <= end_ts]

        # Reindex to business daily grid, then forward-fill from source
        # This correctly handles monthly and daily series (T10Y2Y,
        # BAMLH0A0HYM2) alike
        aligned = s_pit.reindex(
            s_pit.index.union(dates)
        ).sort_index()

        if fill:
            aligned = aligned.ffill()

        result = aligned.reindex(dates)

        # Enforce PIT: dates before the first observation must be NaN
        if len(s_pit) > 0:
            first_obs = s_pit.dropna().index.min()
            result = result.where(result.index >= first_obs, other=np.nan)

        result.name = series_id
        return result

    def get_latest(self, series_id: str) -> float | None:
        """
        Return the most recent non-null observation for series_id.
        No PIT gate — uses the full available history.
        """
        s = self._load(series_id)
        if s is None:
            return None
        s = s.dropna()
        return float(s.iloc[-1]) if not s.empty else None

    def get_many(
        self,
        series_ids: list[str],
        as_of_date: str | pd.Timestamp,
    ) -> pd.Series:
        """
        Return a cross-sectional snapshot of multiple series as of as_of_date.

        Returns
        -------
        pd.Series indexed by series_id. NaN for series with no data.
        """
        return pd.Series(
            {sid: self.get(sid, as_of_date) for sid in series_ids},
            name=str(as_of_date),
        )

    def get_frame(
        self,
        series_ids: list[str],
        start:      str | pd.Timestamp,
        end:        str | pd.Timestamp,
        freq:       str = 'B',
        fill:       bool = True,
    ) -> pd.DataFrame:
        """
        Return a wide DataFrame with one column per series over the date range.

        Designed for bulk consumption by the regime engine. All series are
        aligned to the same business-day DatetimeIndex; gaps are forward-filled.

        Returns
        -------
        pd.DataFrame indexed by date, one column per series_id.
        """
        frames = {
            sid: self.get_series(sid, start, end, freq=freq, fill=fill)
            for sid in series_ids
        }
        return pd.DataFrame(frames)

    # ── Availability helpers ──────────────────────────────────────────

    def available_series(self) -> list[str]:
        """Return list of all FRED series IDs available in the store."""
        return sorted(
            p.stem for p in self._path.glob('*.parquet')
            if not p.stem.startswith('_')
        )

    def coverage(self, series_id: str) -> dict:
        """
        Return date coverage info for a series.
        Returns {'first': date, 'last': date, 'n_obs': int, 'n_null': int}.
        """
        s = self._load(series_id)
        if s is None:
            return {'first': None, 'last': None, 'n_obs': 0, 'n_null': 0}
        return {
            'first':  s.dropna().index.min().date() if not s.dropna().empty else None,
            'last':   s.dropna().index.max().date() if not s.dropna().empty else None,
            'n_obs':  int(s.notna().sum()),
            'n_null': int(s.isna().sum()),
        }

    def coverage_report(self) -> pd.DataFrame:
        """
        Return a DataFrame summarising coverage for all available series.
        Useful for quickly verifying a successful fred_ingestion.py run.
        """
        rows = []
        for sid in self.available_series():
            cov = self.coverage(sid)
            rows.append({'series_id': sid, **cov})
        return pd.DataFrame(rows).set_index('series_id')

    # ── Internal ──────────────────────────────────────────────────────

    def _load(self, series_id: str) -> pd.Series | None:
        """
        Load series parquet into an in-memory cache.
        Returns a pd.Series indexed by date, or None if not found.
        """
        sid = series_id.upper()

        if sid in self._cache:
            return self._cache[sid]

        p = self._path / f'{sid}.parquet'
        if not p.exists():
            return None

        try:
            df = pd.read_parquet(p)
        except Exception as e:
            print(f"[MacroStore] Failed to read {p}: {e}")
            return None

        missing = self.REQUIRED_COLS - set(df.columns)
        if missing:
            raise ValueError(
                f"[MacroStore] {sid}.parquet missing required columns: {missing}\n"
                f"Re-run fred_ingestion.py to regenerate."
            )

        df['date']  = pd.to_datetime(df['date'], errors='coerce')
        df['value'] = pd.to_numeric(df['value'],  errors='coerce')
        df = df.dropna(subset=['date']).sort_values('date')

        s = df.set_index('date')['value']
        s.name = sid

        self._cache[sid] = s
        return s

    def clear_cache(self):
        """Free in-memory cache. Call between large batch runs."""
        self._cache.clear()

    def __repr__(self) -> str:
        loaded = len(self._cache)
        avail  = len(self.available_series())
        return f"MacroStore(path='{self._path}', series_available={avail}, loaded={loaded})"
