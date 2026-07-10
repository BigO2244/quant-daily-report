"""Flag-gated Alpaca market-data price source with yfinance fail-safe.

Selection contract (see the dispatchers in core/quant_report.py and
paper/paper_broker.py):

- ``CAERUS_PRICE_SOURCE`` unset or ``yfinance``  -> existing yfinance code
  paths run untouched (production default; byte-identical behavior).
- ``CAERUS_PRICE_SOURCE=alpaca``                 -> the functions in this
  module run, returning DataFrames with EXACTLY the same schema as the
  yfinance implementations they mirror.

Adjustment semantics
--------------------
Historical daily bars are requested with ``adjustment='all'`` (splits AND
dividends) so the resulting close series matches yfinance auto-adjust
semantics. Same-day open bars carry no future corporate actions, so the
adjustment mode is a no-op for the open-price fetcher.

Index symbols
-------------
Alpaca's stock data API cannot serve index symbols (anything starting with
``^``, e.g. ``^VIX``, ``^GSPC``). Those symbols are transparently routed to
the existing yfinance fetcher and merged into the result. ^VIX stays on
yfinance in this phase BY DESIGN; migrating VIX to FRED VIXCLS is a later
phase.

Fail-safe
---------
Any Alpaca error or empty result for requested symbols falls back to
yfinance for those symbols with a loud ``[PRICE_SOURCE] alpaca fallback``
warning. This module never silently returns less data than the yfinance
path would.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Sequence

import pandas as pd

logger = logging.getLogger(__name__)

PRICE_SOURCE_ENV = "CAERUS_PRICE_SOURCE"
PRICE_SOURCE_YFINANCE = "yfinance"
PRICE_SOURCE_ALPACA = "alpaca"

# Alpaca accepts large symbol lists per StockBarsRequest; batch conservatively.
ALPACA_SYMBOL_CHUNK_SIZE = 200

# Free-tier accounts cannot query SIP data from the most recent 15 minutes.
# Multi-day history uses the default feed but ends the query window slightly
# in the past to stay clear of that restriction; same-day/latest data uses
# feed='iex' explicitly (free-tier real-time).
_SIP_RECENCY_BUFFER_MINUTES = 16

DOWNLOAD_PRICE_COLUMNS = ["date", "ticker", "open", "high", "low", "close", "volume"]
OPEN_PRICE_COLUMNS = ["ticker", "open", "price_date"]


def resolve_price_source() -> str:
    """Return the active price source: 'yfinance' (default) or 'alpaca'.

    Any value other than 'alpaca' (including unset/empty/unknown) resolves to
    'yfinance' so production behavior is unchanged unless explicitly opted in.
    """
    raw = str(os.environ.get(PRICE_SOURCE_ENV, "") or "").strip().lower()
    if raw == PRICE_SOURCE_ALPACA:
        return PRICE_SOURCE_ALPACA
    return PRICE_SOURCE_YFINANCE


def is_index_symbol(ticker: object) -> bool:
    """True for index symbols Alpaca cannot serve (e.g. ^VIX, ^GSPC)."""
    return str(ticker).strip().startswith("^")


def _split_index_symbols(tickers: Sequence[str]) -> tuple[List[str], List[str]]:
    equities: List[str] = []
    indexes: List[str] = []
    for t in tickers:
        (indexes if is_index_symbol(t) else equities).append(t)
    return equities, indexes


def _chunk(symbols: Sequence[str], size: int = ALPACA_SYMBOL_CHUNK_SIZE) -> List[List[str]]:
    return [list(symbols[i : i + size]) for i in range(0, len(symbols), size)]


def _build_client():
    """Construct an Alpaca StockHistoricalDataClient from env credentials.

    Uses the same key pair as the broker (ALPACA_API_KEY_ID /
    ALPACA_API_SECRET_KEY); the market-data client is endpoint-agnostic, so
    paper vs. live base URLs are irrelevant here.
    """
    api_key = os.environ.get("ALPACA_API_KEY_ID", "").strip()
    secret_key = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    if not api_key or not secret_key:
        raise RuntimeError(
            "Alpaca data credentials missing: set ALPACA_API_KEY_ID and "
            "ALPACA_API_SECRET_KEY (same keys as the broker)."
        )
    from alpaca.data.historical import StockHistoricalDataClient

    return StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)


def _period_to_start(period: str, now: Optional[pd.Timestamp] = None) -> pd.Timestamp:
    """Translate a yfinance-style period string into a start timestamp."""
    now = now if now is not None else pd.Timestamp.now(tz=timezone.utc)
    p = str(period).strip().lower()
    if p == "max":
        return now - pd.DateOffset(years=30)
    if p == "ytd":
        return pd.Timestamp(year=now.year, month=1, day=1, tz=now.tz)
    m = re.fullmatch(r"(\d+)(d|wk|mo|y)", p)
    if not m:
        raise ValueError(f"Unsupported period for alpaca price source: {period!r}")
    n = int(m.group(1))
    unit = m.group(2)
    if unit == "d":
        return now - pd.Timedelta(days=n)
    if unit == "wk":
        return now - pd.Timedelta(weeks=n)
    if unit == "mo":
        return now - pd.DateOffset(months=n)
    return now - pd.DateOffset(years=n)


def _bar_timestamp_to_naive_date(ts: object) -> pd.Timestamp:
    """Normalize an Alpaca bar timestamp (tz-aware UTC) to a naive session date.

    Alpaca daily bars are stamped at the session boundary in UTC (04:00/05:00
    UTC == midnight America/New_York). Convert to the exchange timezone before
    truncating so the calendar date matches yfinance's tz-naive daily index.
    """
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("America/New_York").tz_localize(None)
    return t.normalize()


def _get_bars(client, symbols: List[str], *, start, end, feed: Optional[str]) -> Dict[str, list]:
    """Run one StockBarsRequest and return the {symbol: [bars]} payload."""
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    kwargs = dict(
        symbol_or_symbols=list(symbols),
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        # 'all' = splits + dividends, matching yfinance auto-adjust semantics.
        adjustment="all",
    )
    if feed is not None:
        kwargs["feed"] = feed
    resp = client.get_stock_bars(StockBarsRequest(**kwargs))
    data = getattr(resp, "data", None)
    if data is None and isinstance(resp, dict):
        data = resp
    return dict(data or {})


def _default_yf_download(tickers: List[str], period: str, interval: str) -> pd.DataFrame:
    # Lazy import to avoid a circular import (quant_report dispatches to us).
    from core.quant_report import _download_prices_yfinance

    return _download_prices_yfinance(tickers, period=period, interval=interval)


def _default_yf_open_prices(tickers: List[str], run_date: str) -> pd.DataFrame:
    from paper.paper_broker import _fetch_open_prices_yfinance_impl

    return _fetch_open_prices_yfinance_impl(tickers, run_date)


def _empty_download_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.Series(dtype="datetime64[ns]"),
            "ticker": pd.Series(dtype="object"),
            "open": pd.Series(dtype="float64"),
            "high": pd.Series(dtype="float64"),
            "low": pd.Series(dtype="float64"),
            "close": pd.Series(dtype="float64"),
            "volume": pd.Series(dtype="int64"),
        }
    )


def alpaca_download_prices(
    tickers: List[str],
    period: str = "1y",
    interval: str = "1d",
    *,
    client=None,
    yf_fallback: Optional[Callable[[List[str], str, str], pd.DataFrame]] = None,
    fallback_to_yfinance: bool = True,
) -> pd.DataFrame:
    """Alpaca-backed equivalent of core.quant_report.download_prices.

    Returns the identical long-form schema:
    columns [date, ticker, open, high, low, close, volume]; 'date' is a
    tz-naive datetime64[ns]; one row per (ticker, session).

    Index symbols ('^...') are always fetched via yfinance; Alpaca errors or
    per-symbol empty results fall back to yfinance for the affected symbols
    (unless fallback_to_yfinance=False, used by the shadow comparator so
    divergence is visible instead of papered over).
    """
    tickers = [str(t).strip().upper() for t in tickers if str(t).strip()]
    if not tickers:
        raise RuntimeError(
            "No price data downloaded. Check data/universe.csv and yfinance availability."
        )
    yf_fetch = yf_fallback or _default_yf_download

    equities, index_symbols = _split_index_symbols(tickers)

    frames: List[pd.DataFrame] = []
    fallback_symbols: List[str] = list(index_symbols)

    if interval != "1d" and equities:
        # Alpaca path only implements daily bars; anything else is served by
        # the existing yfinance implementation.
        logger.warning(
            "[PRICE_SOURCE] alpaca fallback -> yfinance for interval=%s (%d symbols); "
            "alpaca price source implements interval='1d' only",
            interval,
            len(equities),
        )
        fallback_symbols.extend(equities)
        equities = []

    if equities:
        try:
            now = pd.Timestamp.now(tz=timezone.utc)
            start = _period_to_start(period, now=now)
            # Default (SIP) feed for multi-day history; end the window before
            # the free-tier 15-minute SIP recency cutoff.
            end = now - pd.Timedelta(minutes=_SIP_RECENCY_BUFFER_MINUTES)
            cli = client if client is not None else _build_client()
            rows: List[dict] = []
            served: set = set()
            for batch in _chunk(equities):
                try:
                    data = _get_bars(cli, batch, start=start.to_pydatetime(), end=end.to_pydatetime(), feed=None)
                except Exception as exc:  # noqa: BLE001 - fail-safe by design
                    logger.warning(
                        "[PRICE_SOURCE] alpaca fallback -> yfinance for %d symbols "
                        "(batch request failed: %s)",
                        len(batch),
                        exc,
                    )
                    fallback_symbols.extend(batch)
                    continue
                for sym, bars in data.items():
                    sym_u = str(sym).strip().upper()
                    for bar in bars or []:
                        rows.append(
                            {
                                "date": _bar_timestamp_to_naive_date(bar.timestamp),
                                "ticker": sym_u,
                                "open": float(bar.open),
                                "high": float(bar.high),
                                "low": float(bar.low),
                                "close": float(bar.close),
                                "volume": int(bar.volume),
                            }
                        )
                    if bars:
                        served.add(sym_u)
                missing = [t for t in batch if t not in served]
                if missing:
                    logger.warning(
                        "[PRICE_SOURCE] alpaca fallback -> yfinance for %d symbols "
                        "with empty alpaca bars: %s%s",
                        len(missing),
                        sorted(missing)[:20],
                        "..." if len(missing) > 20 else "",
                    )
                    fallback_symbols.extend(missing)
            if rows:
                frames.append(pd.DataFrame(rows, columns=DOWNLOAD_PRICE_COLUMNS))
        except Exception as exc:  # noqa: BLE001 - fail-safe by design
            logger.warning(
                "[PRICE_SOURCE] alpaca fallback -> yfinance for %d symbols "
                "(alpaca client unavailable: %s)",
                len(equities),
                exc,
            )
            fallback_symbols.extend(equities)

    fallback_symbols = sorted(set(fallback_symbols))
    if fallback_symbols and fallback_to_yfinance:
        index_only = all(is_index_symbol(t) for t in fallback_symbols)
        if not index_only:
            logger.warning(
                "[PRICE_SOURCE] alpaca fallback -> yfinance fetch for symbols: %s%s",
                fallback_symbols[:20],
                "..." if len(fallback_symbols) > 20 else "",
            )
        else:
            logger.info(
                "[PRICE_SOURCE] index symbols routed to yfinance by design: %s",
                fallback_symbols,
            )
        try:
            yf_df = yf_fetch(fallback_symbols, period, interval)
            if yf_df is not None and not yf_df.empty:
                frames.append(yf_df[DOWNLOAD_PRICE_COLUMNS])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[PRICE_SOURCE] yfinance fallback fetch also failed for %s: %s",
                fallback_symbols[:20],
                exc,
            )

    if not frames:
        raise RuntimeError(
            "No price data downloaded. Check data/universe.csv and yfinance availability."
        )

    prices = pd.concat(frames, ignore_index=True)
    prices = prices.sort_values(["ticker", "date"], kind="stable").reset_index(drop=True)
    # Match the yfinance path's datetime64[ns] resolution exactly.
    prices["date"] = pd.to_datetime(prices["date"]).astype("datetime64[ns]")
    return prices[DOWNLOAD_PRICE_COLUMNS]


def alpaca_fetch_open_prices(
    tickers: List[str],
    run_date: str,
    *,
    client=None,
    yf_fallback: Optional[Callable[[List[str], str], pd.DataFrame]] = None,
    fallback_to_yfinance: bool = True,
) -> pd.DataFrame:
    """Alpaca-backed equivalent of paper.paper_broker.fetch_open_prices_yfinance.

    Returns the identical schema: columns [ticker, open, price_date] with
    'open' as float and 'price_date' as a 'YYYY-MM-DD' string, deduplicated by
    ticker.

    Uses feed='iex' explicitly: the open for run_date is same-day/latest data
    and IEX is the free-tier real-time feed (default SIP feed rejects queries
    inside the most recent 15 minutes on free-tier keys).
    """
    tickers = [str(t).strip().upper() for t in tickers if str(t).strip()]
    if not tickers:
        return pd.DataFrame(columns=OPEN_PRICE_COLUMNS)
    yf_fetch = yf_fallback or _default_yf_open_prices

    equities, index_symbols = _split_index_symbols(tickers)
    fallback_symbols: List[str] = list(index_symbols)

    start = pd.Timestamp(run_date)
    end = start + pd.Timedelta(days=1)

    rows: List[dict] = []
    if equities:
        try:
            cli = client if client is not None else _build_client()
            served: set = set()
            for batch in _chunk(equities):
                try:
                    data = _get_bars(
                        cli,
                        batch,
                        start=start.to_pydatetime(),
                        end=end.to_pydatetime(),
                        feed="iex",  # same-day/latest data: free-tier real-time feed
                    )
                except Exception as exc:  # noqa: BLE001 - fail-safe by design
                    logger.warning(
                        "[PRICE_SOURCE] alpaca fallback -> yfinance opens for %d symbols "
                        "(batch request failed: %s)",
                        len(batch),
                        exc,
                    )
                    fallback_symbols.extend(batch)
                    continue
                for sym, bars in data.items():
                    if not bars:
                        continue
                    sym_u = str(sym).strip().upper()
                    bar = bars[0]
                    rows.append(
                        {
                            "ticker": sym_u,
                            "open": float(bar.open),
                            "price_date": str(_bar_timestamp_to_naive_date(bar.timestamp).date()),
                        }
                    )
                    served.add(sym_u)
                missing = [t for t in batch if t not in served]
                if missing:
                    logger.warning(
                        "[PRICE_SOURCE] alpaca fallback -> yfinance opens for %d symbols "
                        "with no alpaca bar on %s: %s%s",
                        len(missing),
                        run_date,
                        sorted(missing)[:20],
                        "..." if len(missing) > 20 else "",
                    )
                    fallback_symbols.extend(missing)
        except Exception as exc:  # noqa: BLE001 - fail-safe by design
            logger.warning(
                "[PRICE_SOURCE] alpaca fallback -> yfinance opens for %d symbols "
                "(alpaca client unavailable: %s)",
                len(equities),
                exc,
            )
            fallback_symbols.extend(equities)

    df = pd.DataFrame(rows, columns=OPEN_PRICE_COLUMNS) if rows else pd.DataFrame(columns=OPEN_PRICE_COLUMNS)

    fallback_symbols = sorted(set(fallback_symbols))
    if fallback_symbols and fallback_to_yfinance:
        try:
            yf_df = yf_fetch(fallback_symbols, run_date)
            if yf_df is not None and not yf_df.empty:
                df = pd.concat([df, yf_df[OPEN_PRICE_COLUMNS]], ignore_index=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[PRICE_SOURCE] yfinance opens fallback also failed for %s: %s",
                fallback_symbols[:20],
                exc,
            )

    if df.empty:
        raise RuntimeError(f"No usable open prices for run_date={run_date}")
    return df.drop_duplicates(subset=["ticker"], keep="last").reset_index(drop=True)
