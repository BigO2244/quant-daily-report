"""
regime_engine.py
----------------
Top-level orchestrator for the regime pipeline.

Wires together:
    FredClient          → macro data
    build_indicators()  → compute all indicator values
    classify()          → assign dimension and composite regime states
    RegimeAllocator     → produce smoothed sleeve weights

Usage (from your daily runner):

    from regime.regime_engine import RegimeEngine
    import yfinance as yf

    engine = RegimeEngine()

    # Supply your existing price data (same format as download_prices() output)
    spy_prices      = download_prices(["SPY"], period="5y")[lambda df: df["ticker"]=="SPY"]
    vix_prices      = download_prices(["^VIX"], period="5y")[lambda df: df["ticker"]=="^VIX"]
    universe_prices = download_prices(universe_tickers, period="5y")

    result = engine.run(spy_prices, vix_prices, universe_prices)

    # result.weights   : daily smoothed sleeve weights (DataFrame)
    # result.states    : daily regime states per dimension (DataFrame)
    # result.indicators: raw + EWM indicator values (DataFrame) — for diagnostics

For live daily use, call engine.run() once per day and consume result.weights.iloc[-1]
as today's target allocation.
"""

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from regime.fred_client import FredClient
from regime.regime_indicators import build_indicators
from regime.regime_classifier import classify
from regime.regime_allocator import RegimeAllocator
from regime.regime_config import FRED_SERIES

logger = logging.getLogger(__name__)

# ── Required FRED series for the regime engine ──────────────────────────────
_REQUIRED_MACRO_SERIES = {"T10Y2Y", "BAMLH0A0HYM2"}


class MacroStoreFredAdapter:
    """
    Drop-in replacement for FredClient that reads from MacroStore parquet files.

    Provides the same fetch_many() interface as FredClient but reads from
    data/macro/{SERIES_ID}.parquet (produced by fred_ingestion.py) instead of
    making live FRED API calls. This eliminates network dependency at runtime.
    """

    def __init__(self, store_path: str | Path = "data/macro"):
        from macro_store import MacroStore
        self._store = MacroStore(store_path)

    def fetch_many(
        self,
        series_map: dict[str, str],
        start: str = "2005-01-01",
        end: str | None = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        Fetch multiple series from parquet and return a wide DataFrame.

        Parameters
        ----------
        series_map : dict  {series_id: col_name}
            Keys   = FRED series IDs  (e.g. "T10Y2Y")
            Values = output column names (e.g. "yield_curve_2s10s")
        start : str  — start date in YYYY-MM-DD format
        end   : str  — end date (defaults to today)
        force_refresh : bool  — ignored (parquet files always current after ingestion)

        Returns
        -------
        pd.DataFrame  — DatetimeIndex, one column per series (named by col_name values)
        """
        if force_refresh:
            logger.info("[MACRO_STORE] force_refresh ignored — reading from parquet")

        end_str = end or date.today().isoformat()
        series_ids = list(series_map.keys())

        raw = self._store.get_frame(series_ids, start=start, end=end_str, freq="B", fill=True)

        # Rename columns from series_id → col_name
        rename_map = {sid: series_map[sid] for sid in series_ids if sid in raw.columns}
        raw = raw.rename(columns=rename_map)

        logger.info(
            "[MACRO_STORE] Loaded macro frame: rows=%d cols=%s",
            len(raw),
            list(raw.columns),
        )
        return raw


def _macro_store_available(store_path: str = "data/macro") -> bool:
    """Return True if all required macro series parquet files are present."""
    p = Path(store_path)
    if not p.exists():
        return False
    return all((p / f"{sid}.parquet").exists() for sid in _REQUIRED_MACRO_SERIES)


@dataclass
class RegimeResult:
    """Full output of a RegimeEngine.run() call."""
    weights:    pd.DataFrame   # daily smoothed sleeve weights
    states:     pd.DataFrame   # daily dimension + composite regime states
    indicators: pd.DataFrame   # raw + EWM indicator values (diagnostics)

    @property
    def today(self) -> dict:
        """Convenience: today's (latest) composite regime and weights as a dict."""
        latest_weights = self.weights.iloc[-1]
        latest_state   = self.states.iloc[-1]
        return {
            "date":             self.weights.index[-1],
            "composite_regime": latest_state["composite_regime"],
            "trend_state":      latest_state["trend_state"],
            "volatility_state": latest_state["volatility_state"],
            "breadth_state":    latest_state["breadth_state"],
            "macro_state":      latest_state["macro_state"],
            "weights":          latest_weights.drop(
                                    ["composite_regime", "regime_changed"],
                                    errors="ignore"
                                ).to_dict(),
        }


class RegimeEngine:
    """
    Orchestrates the full regime pipeline.

    Parameters
    ----------
    fred_client : FredClient, optional
        Provide a pre-configured client (e.g., for testing).
        If None, one is created using FRED_API_KEY from env.
    allocator : RegimeAllocator, optional
        Provide a custom allocator. If None, default config is used.
    macro_start : str
        How far back to pull FRED macro data.
        Should be at least as far back as your price history.
    """

    def __init__(
        self,
        fred_client: FredClient | None = None,
        allocator: RegimeAllocator | None = None,
        macro_start: str = "2005-01-01",
        macro_store_path: str = "data/macro",
    ):
        if fred_client is not None:
            # Explicit injection — used in tests or custom configurations
            self._fred = fred_client
        elif _macro_store_available(macro_store_path):
            # Parquet files present — use offline store (no API key needed)
            logger.info(
                "[REGIME] Using MacroStoreFredAdapter from %s (no FRED API call needed)",
                macro_store_path,
            )
            self._fred = MacroStoreFredAdapter(macro_store_path)
        else:
            # Fall back to live FRED API (requires FRED_API_KEY in env)
            logger.info(
                "[REGIME] MacroStore parquet files not found at %s — falling back to FredClient",
                macro_store_path,
            )
            self._fred = FredClient()

        self._alloc  = allocator or RegimeAllocator()
        self._macro_start = macro_start

    def run(
        self,
        spy_prices: pd.DataFrame,
        vix_prices: pd.DataFrame,
        universe_prices: pd.DataFrame,
        force_fred_refresh: bool = False,
    ) -> RegimeResult:
        """
        Run the full regime pipeline and return a RegimeResult.

        Parameters
        ----------
        spy_prices : DataFrame with [date, close] or [date, ticker, close] for SPY
        vix_prices : DataFrame with [date, close] or [date, ticker, close] for ^VIX
        universe_prices : Long-form DataFrame [date, ticker, close] for full universe
        force_fred_refresh : bool — bypass FRED cache and re-fetch from API

        Returns
        -------
        RegimeResult
        """
        logger.info("RegimeEngine.run() starting")

        # 1. Fetch macro data from FRED
        fred_col_map = {v: k for k, v in FRED_SERIES.items()}  # series_id → col_name
        fred_data = self._fred.fetch_many(
            series_map=fred_col_map,
            start=self._macro_start,
            force_refresh=force_fred_refresh,
        )
        logger.info("FRED data: %d rows, columns: %s", len(fred_data), list(fred_data.columns))

        # 2. Normalize price inputs (handle both wide and long-form SPY/VIX)
        spy_clean = _extract_single_ticker(spy_prices, "SPY")
        vix_clean = _extract_single_ticker(vix_prices, "^VIX")

        # 3. Build indicator table
        indicators = build_indicators(
            spy_prices=spy_clean,
            vix_prices=vix_clean,
            universe_prices=universe_prices,
            fred_data=fred_data,
        )

        # 4. Classify regime states
        states = classify(indicators)

        # 5. Compute smoothed sleeve weights
        weights = self._alloc.compute_weights(states["composite_regime"])

        logger.info(
            "RegimeEngine.run() complete — latest regime: %s",
            states["composite_regime"].iloc[-1],
        )

        return RegimeResult(
            weights=weights,
            states=states,
            indicators=indicators,
        )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _extract_single_ticker(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """
    If df is long-form (has 'ticker' column), filter to the given ticker.
    Otherwise return as-is (assumed to already be single-ticker).
    """
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    if "ticker" in df.columns:
        df = df[df["ticker"].str.upper() == ticker.upper()].drop(columns=["ticker"])
    return df
