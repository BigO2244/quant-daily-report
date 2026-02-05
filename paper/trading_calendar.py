# paper/trading_calendar.py
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd


_US_MARKET_HOLIDAYS_STUB = {
    "2024-01-01",  # New Year's Day
    "2024-07-04",  # Independence Day
    "2024-12-25",  # Christmas
    "2025-01-01",
    "2025-07-04",
    "2025-12-25",
    "2026-01-01",
    "2026-07-03",  # observed
    "2026-12-25",
}


@dataclass
class MarketSessionStatus:
    is_trading_day: bool
    is_open_now: bool
    reason: str


def _is_weekday(date_str: str) -> bool:
    return pd.Timestamp(date_str).weekday() < 5


def is_trading_day(date_str: str) -> bool:
    # TODO(PHASE-3): Replace holiday stub with full exchange calendar integration.
    return _is_weekday(date_str) and date_str not in _US_MARKET_HOLIDAYS_STUB


def next_trading_day(date_str: str) -> str:
    d = pd.Timestamp(date_str)
    while True:
        d = d + pd.Timedelta(days=1)
        cand = str(d.date())
        if is_trading_day(cand):
            return cand


def prev_trading_day(date_str: str) -> str:
    d = pd.Timestamp(date_str)
    while True:
        d = d - pd.Timedelta(days=1)
        cand = str(d.date())
        if is_trading_day(cand):
            return cand


def market_session_status(
    run_date: str,
    now_et: dt.datetime | None,
    cutoff_time_et: str,
) -> MarketSessionStatus:
    if not is_trading_day(run_date):
        return MarketSessionStatus(False, False, "MARKET_CLOSED_DAY")

    if now_et is None:
        return MarketSessionStatus(True, True, "ASSUMED_OPEN_NO_CLOCK")

    if now_et.date().isoformat() != run_date:
        return MarketSessionStatus(False, False, "RUN_DATE_NOT_TODAY")

    market_open = dt.time(hour=9, minute=30)
    cutoff_hour, cutoff_minute = [int(x) for x in cutoff_time_et.split(":", 1)]
    market_cutoff = dt.time(hour=cutoff_hour, minute=cutoff_minute)

    if now_et.time() < market_open:
        return MarketSessionStatus(True, False, "BEFORE_MARKET_OPEN")
    if now_et.time() > market_cutoff:
        return MarketSessionStatus(True, False, "AFTER_MARKET_CUTOFF")

    return MarketSessionStatus(True, True, "MARKET_OPEN")
