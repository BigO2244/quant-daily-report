"""
datastore.py  —  Caerus / Alpha Stack
Point-in-time correct interface to EDGAR fundamental data.

ALL fundamental reads in Caerus must go through this class.
The .get() method enforces the PIT gate: it never returns data
filed after as_of_date, eliminating look-ahead bias.

Usage:
    from data.datastore import DataStore

    ds = DataStore('data/fundamental')

    # Single value — PIT correct
    roe = ds.get('AAPL', 'NetIncomeLoss', '2022-06-30')

    # Time series — PIT correct across a date range
    series = ds.get_series('AAPL', 'NetIncomeLoss', '2020-01-01', '2022-12-31')

    # Trailing 4-quarter sum (TTM)
    ttm = ds.get_ttm('AAPL', 'NetIncomeLoss', '2022-06-30')

    # Cross-sectional snapshot for a list of tickers
    snap = ds.get_cross_section(['AAPL', 'MSFT', 'NVDA'], 'NetIncomeLoss', '2022-06-30')
"""

import pandas as pd
import numpy as np
from pathlib import Path
from functools import lru_cache
from typing import Optional


# ── Revenue tag fallback chain ────────────────────────────────────────
# EDGAR uses different revenue tags across companies and time periods.
# DataStore tries them in order and returns the first non-null result.
REVENUE_TAGS = [
    'RevenueFromContractWithCustomerExcludingAssessedTax',
    'SalesRevenueNet',
    'Revenues',
]

# ── Equity tag fallback chain ─────────────────────────────────────────
EQUITY_TAGS = [
    'StockholdersEquity',
    'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
]

# ── Long-term debt fallback chain ─────────────────────────────────────
DEBT_TAGS = [
    'LongTermDebt',
    'LongTermDebtNoncurrent',
]

# ── Capex fallback chain ──────────────────────────────────────────────
CAPEX_TAGS = [
    'PaymentsToAcquirePropertyPlantAndEquipment',
    'CapitalExpendituresIncurredButNotYetPaid',
]

# Annual forms only — for signals that need full-year figures
ANNUAL_FORMS  = {'10-K'}
# Quarterly forms — for signals that need quarterly granularity
QUARTER_FORMS = {'10-Q'}
# Both
ALL_FORMS     = {'10-K', '10-Q'}


class DataStore:
    """
    Point-in-time correct interface to Caerus EDGAR fundamental data.

    Key guarantee: every value returned was publicly available
    on or before the as_of_date passed to the query. No future data
    ever leaks into a historical query.

    Parameters
    ----------
    store_path : str or Path
        Directory containing per-ticker parquet files produced by
        edgar_ingestion.py (e.g. 'data/fundamental').
    """

    REQUIRED_COLS = {'ticker', 'filed_date', 'period_end', 'tag', 'value'}

    def __init__(self, store_path: str | Path):
        self._path  = Path(store_path)
        self._cache: dict[str, pd.DataFrame] = {}

        if not self._path.exists():
            raise FileNotFoundError(
                f"DataStore path not found: {self._path.resolve()}\n"
                f"Run edgar_ingestion.py first."
            )

    # ── Core read interface ───────────────────────────────────────────

    def get(
        self,
        ticker:      str,
        tag:         str,
        as_of_date:  str | pd.Timestamp,
        forms:       set[str] = ALL_FORMS,
        annual_only: bool = False,
    ) -> float | None:
        """
        Return the most recent value of `tag` for `ticker` that was
        filed on or before `as_of_date`.

        Parameters
        ----------
        ticker      : Stock ticker (e.g. 'AAPL')
        tag         : XBRL tag name (e.g. 'NetIncomeLoss')
        as_of_date  : PIT gate — only filings on/before this date are used
        forms       : Set of form types to include. Default: 10-K and 10-Q.
        annual_only : If True, restrict to 10-K filings only.

        Returns
        -------
        Most recent filed value, or None if no data available.
        """
        if annual_only:
            forms = ANNUAL_FORMS

        df = self._load(ticker)
        if df is None:
            return None

        cutoff   = pd.Timestamp(as_of_date)
        eligible = df[
            (df['tag']         == tag)         &
            (df['filed_date'] <= cutoff)        &
            (df['form'].isin(forms))
        ].sort_values('filed_date')

        if eligible.empty:
            return None

        val = eligible.iloc[-1]['value']
        return float(val) if pd.notna(val) else None

    def get_with_fallback(
        self,
        ticker:     str,
        tags:       list[str],
        as_of_date: str | pd.Timestamp,
        forms:      set[str] = ALL_FORMS,
    ) -> tuple[float | None, str | None]:
        """
        Try a list of tags in order, return the first non-null result.
        Useful for fields like Revenue that have multiple XBRL tags.

        Returns (value, tag_used) or (None, None) if all tags fail.
        """
        for tag in tags:
            val = self.get(ticker, tag, as_of_date, forms=forms)
            if val is not None:
                return val, tag
        return None, None

    def get_series(
        self,
        ticker:     str,
        tag:        str,
        start:      str | pd.Timestamp,
        end:        str | pd.Timestamp,
        freq:       str = 'B',
        forms:      set[str] = ALL_FORMS,
        annual_only:bool = False,
    ) -> pd.Series:
        """
        Return a PIT-correct daily time series of `tag` between start and end.

        Each date in the series reflects the most recently filed value
        that was available as of that date — i.e. what a live system
        would have known on that day.

        Parameters
        ----------
        freq : Pandas date frequency. 'B' = business days (default).
               Use 'Q' or 'A' for lower-frequency series.

        Returns
        -------
        pd.Series indexed by date, name=tag. NaN where no data yet available.
        """
        if annual_only:
            forms = ANNUAL_FORMS

        df = self._load(ticker)
        if df is None:
            return pd.Series(dtype=float, name=tag)

        cutoff_start = pd.Timestamp(start)
        cutoff_end   = pd.Timestamp(end)
        dates        = pd.date_range(cutoff_start, cutoff_end, freq=freq)

        subset = df[
            (df['tag']   == tag) &
            (df['form'].isin(forms))
        ].sort_values('filed_date')

        if subset.empty:
            return pd.Series(np.nan, index=dates, name=tag)

        # For each date, find the last filing on or before that date
        filed_dates = subset['filed_date'].values
        values      = subset['value'].values

        result = []
        for d in dates:
            mask = filed_dates <= np.datetime64(d)
            if mask.any():
                val = values[mask][-1]
                result.append(float(val) if pd.notna(val) else np.nan)
            else:
                result.append(np.nan)

        return pd.Series(result, index=dates, name=tag)

    def get_ttm(
        self,
        ticker:     str,
        tag:        str,
        as_of_date: str | pd.Timestamp,
    ) -> float | None:
        """
        Return trailing twelve month (TTM) sum of `tag` as of `as_of_date`.

        Sums the 4 most recent quarterly (10-Q) filings filed on or
        before as_of_date. Falls back to the most recent annual (10-K)
        if fewer than 4 quarters are available.

        Used for: NetIncomeLoss, Revenues, GrossProfit, OperatingCashFlow, etc.
        """
        df = self._load(ticker)
        if df is None:
            return None

        cutoff = pd.Timestamp(as_of_date)

        quarterly = df[
            (df['tag']         == tag)    &
            (df['filed_date'] <= cutoff)  &
            (df['form']        == '10-Q')
        ].copy()

        # Each 10-Q filing contains both a single-quarter value AND a YTD
        # cumulative value for the same period_end. Keep only single-quarter
        # rows by filtering on period span of 60-120 days.
        if 'period_start' in quarterly.columns and not quarterly.empty:
            quarterly['_span'] = (
                quarterly['period_end'] - quarterly['period_start']
            ).dt.days
            quarterly = quarterly[quarterly['_span'].between(60, 120)]
            quarterly = quarterly.drop(columns=['_span'])

        # Deduplicate: keep most recently filed row per period_end, then take last 4
        quarterly = (
            quarterly
            .sort_values('filed_date')
            .drop_duplicates(subset=['period_end'], keep='last')
            .sort_values('period_end')
            .tail(4)
        )

        if len(quarterly) == 4:
            vals = quarterly['value'].dropna()
            return float(vals.sum()) if len(vals) == 4 else None

        # Fallback: annual
        annual = df[
            (df['tag']         == tag)    &
            (df['filed_date'] <= cutoff)  &
            (df['form']        == '10-K')
        ].sort_values('filed_date')

        if annual.empty:
            return None

        val = annual.iloc[-1]['value']
        return float(val) if pd.notna(val) else None

    def get_quarterly_series(
        self,
        ticker:     str,
        tag:        str,
        as_of_date: str | pd.Timestamp,
        n_quarters: int = 8,
    ) -> list[float]:
        """
        Return the last n_quarters of quarterly values filed on or
        before as_of_date, oldest first.

        Used for: gross margin stability, revenue growth consistency,
        accruals ratio series. Returns empty list if insufficient data.
        """
        df = self._load(ticker)
        if df is None:
            return []

        cutoff = pd.Timestamp(as_of_date)

        quarterly = df[
            (df['tag']         == tag)    &
            (df['filed_date'] <= cutoff)  &
            (df['form']        == '10-Q')
        ].sort_values('filed_date').tail(n_quarters)

        if quarterly.empty:
            return []

        vals = quarterly['value'].dropna().tolist()
        return [float(v) for v in vals]

    def get_cross_section(
        self,
        tickers:    list[str],
        tag:        str,
        as_of_date: str | pd.Timestamp,
        forms:      set[str] = ALL_FORMS,
        ttm:        bool = False,
    ) -> pd.Series:
        """
        Return a cross-sectional snapshot of `tag` for multiple tickers
        as of `as_of_date`.

        Parameters
        ----------
        ttm : If True, return TTM sum instead of most recent filing value.

        Returns
        -------
        pd.Series indexed by ticker. NaN for tickers with no data.
        """
        results = {}
        for ticker in tickers:
            if ttm:
                results[ticker] = self.get_ttm(ticker, tag, as_of_date)
            else:
                results[ticker] = self.get(ticker, tag, as_of_date, forms=forms)
        return pd.Series(results, name=tag)

    # ── Derived metrics ───────────────────────────────────────────────

    def get_roe(
        self,
        ticker:     str,
        as_of_date: str | pd.Timestamp,
    ) -> float | None:
        """
        Return on Equity = TTM Net Income / Most Recent Equity.
        PIT-correct: both numerator and denominator gated on as_of_date.
        """
        net_income = self.get_ttm(ticker, 'NetIncomeLoss', as_of_date)
        equity, _  = self.get_with_fallback(ticker, EQUITY_TAGS, as_of_date)

        if net_income is None or equity is None or equity == 0:
            return None
        return net_income / equity

    def get_gross_margin(
        self,
        ticker:     str,
        as_of_date: str | pd.Timestamp,
    ) -> float | None:
        """
        Gross Margin = GrossProfit / Revenue (TTM).
        Returns value between -1 and 1 (or outside for extreme cases).
        """
        gross_profit = self.get_ttm(ticker, 'GrossProfit', as_of_date)
        revenue      = self.get_revenue_ttm(ticker, as_of_date)

        if gross_profit is None or revenue is None or revenue == 0:
            return None
        return gross_profit / revenue

    def get_accruals_ratio(
        self,
        ticker:     str,
        as_of_date: str | pd.Timestamp,
    ) -> float | None:
        """
        Accruals Ratio = (TTM Net Income - TTM Operating Cash Flow) / Total Assets.

        Low accruals = high earnings quality (cash-backed earnings).
        High accruals = earnings may be manufactured via accounting.

        Winsorized at [-0.5, 0.5] to handle extreme values.
        """
        net_income   = self.get_ttm(ticker, 'NetIncomeLoss', as_of_date)
        op_cash_flow = self.get_ttm(
            ticker, 'NetCashProvidedByUsedInOperatingActivities', as_of_date
        )
        total_assets = self.get(ticker, 'Assets', as_of_date)

        if net_income is None or op_cash_flow is None:
            return None
        if total_assets is None or total_assets <= 0:
            return None

        ratio = (net_income - op_cash_flow) / total_assets
        # Winsorize at ±0.5
        return float(np.clip(ratio, -0.5, 0.5))

    def get_debt_equity_ratio(
        self,
        ticker:     str,
        as_of_date: str | pd.Timestamp,
    ) -> float | None:
        """
        Debt/Equity = Long-Term Debt / Stockholders Equity.
        Returns None for Financials where this metric is not meaningful
        (caller should apply sector filter).
        """
        debt, _   = self.get_with_fallback(ticker, DEBT_TAGS, as_of_date)
        equity, _ = self.get_with_fallback(ticker, EQUITY_TAGS, as_of_date)

        if debt is None or equity is None or equity == 0:
            return None
        return debt / equity

    def get_revenue_ttm(
        self,
        ticker:     str,
        as_of_date: str | pd.Timestamp,
    ) -> float | None:
        """Revenue TTM using fallback chain across EDGAR tag variants."""
        for tag in REVENUE_TAGS:
            val = self.get_ttm(ticker, tag, as_of_date)
            if val is not None:
                return val
        return None

    # ── Availability checks ───────────────────────────────────────────

    def available_tags(self, ticker: str) -> list[str]:
        """Return list of XBRL tags available for ticker."""
        df = self._load(ticker)
        if df is None:
            return []
        return sorted(df['tag'].unique().tolist())

    def coverage_report(self, tickers: list[str], tag: str, as_of_date: str) -> dict:
        """
        Quick coverage check: how many tickers have data for `tag` as of date.
        Returns {'available': [...], 'missing': [...]}.
        """
        available = []
        missing   = []
        for t in tickers:
            val = self.get(t, tag, as_of_date)
            if val is not None:
                available.append(t)
            else:
                missing.append(t)
        return {'available': available, 'missing': missing}

    # ── Internal ──────────────────────────────────────────────────────

    def _load(self, ticker: str) -> pd.DataFrame | None:
        """
        Load ticker parquet into memory cache.
        Validates required columns on first load.
        """
        ticker = ticker.upper()

        if ticker in self._cache:
            return self._cache[ticker]

        p = self._path / f'{ticker}.parquet'
        if not p.exists():
            return None

        try:
            df = pd.read_parquet(p)
        except Exception as e:
            print(f"[DataStore] Failed to read {p}: {e}")
            return None

        missing = self.REQUIRED_COLS - set(df.columns)
        if missing:
            raise ValueError(
                f"[DataStore] {ticker}.parquet missing required columns: {missing}\n"
                f"Re-run edgar_ingestion.py to regenerate."
            )

        df['filed_date']  = pd.to_datetime(df['filed_date'],  errors='coerce')
        df['period_end']  = pd.to_datetime(df['period_end'],  errors='coerce')
        df['value']       = pd.to_numeric(df['value'],         errors='coerce')

        # Drop rows with unusable filed_date
        df = df.dropna(subset=['filed_date'])

        self._cache[ticker] = df
        return df

    def clear_cache(self):
        """Free in-memory cache. Call between large batch runs."""
        self._cache.clear()

    def __repr__(self) -> str:
        n_loaded = len(self._cache)
        return f"DataStore(path='{self._path}', tickers_loaded={n_loaded})"
