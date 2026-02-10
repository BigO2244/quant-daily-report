# paper/trading_calendar.py
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

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
    calendar_name: str
    session_open_et: dt.datetime | None
    session_close_et: dt.datetime | None
    next_open_et: dt.datetime | None


XNYS_CALENDAR_NAME = "XNYS"
ET_TZ = ZoneInfo("America/New_York")


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
    cutoff_hour, cutoff_minute = [int(x) for x in cutoff_time_et.split(":", 1)]
    run_day = pd.Timestamp(run_date).date()
    session_open_et = dt.datetime.combine(run_day, dt.time(hour=9, minute=30), tzinfo=ET_TZ)
    session_close_et = dt.datetime.combine(
        run_day,
        dt.time(hour=cutoff_hour, minute=cutoff_minute),
        tzinfo=ET_TZ,
    )

    if now_et is not None:
        if now_et.tzinfo is None:
            raise ValueError("now_et must be timezone-aware")
        now_et = now_et.astimezone(ET_TZ)

    next_open_et = None
    if now_et is not None:
        now_day = now_et.date().isoformat()
        today_open_et = dt.datetime.combine(now_et.date(), dt.time(hour=9, minute=30), tzinfo=ET_TZ)
        today_close_et = dt.datetime.combine(now_et.date(), dt.time(hour=cutoff_hour, minute=cutoff_minute), tzinfo=ET_TZ)
        if is_trading_day(now_day) and now_et < today_open_et:
            next_open_et = today_open_et
        elif is_trading_day(now_day) and today_open_et <= now_et <= today_close_et:
            next_open_et = now_et
        else:
            next_open_day = next_trading_day(now_day)
            next_open_et = dt.datetime.combine(
                pd.Timestamp(next_open_day).date(),
                dt.time(hour=9, minute=30),
                tzinfo=ET_TZ,
            )

    if not is_trading_day(run_date):
        return MarketSessionStatus(
            False,
            False,
            "MARKET_CLOSED_DAY",
            XNYS_CALENDAR_NAME,
            session_open_et,
            session_close_et,
            next_open_et,
        )

    if now_et is None:
        return MarketSessionStatus(
            True,
            True,
            "ASSUMED_OPEN_NO_CLOCK",
            XNYS_CALENDAR_NAME,
            session_open_et,
            session_close_et,
            next_open_et,
        )

    if now_et.date().isoformat() != run_date:
        return MarketSessionStatus(
            False,
            False,
            "RUN_DATE_NOT_TODAY",
            XNYS_CALENDAR_NAME,
            session_open_et,
            session_close_et,
            next_open_et,
        )

    if now_et < session_open_et:
        return MarketSessionStatus(
            True,
            False,
            "BEFORE_MARKET_OPEN",
            XNYS_CALENDAR_NAME,
            session_open_et,
            session_close_et,
            next_open_et,
        )
    if now_et > session_close_et:
        return MarketSessionStatus(
            True,
            False,
            "AFTER_MARKET_CUTOFF",
            XNYS_CALENDAR_NAME,
            session_open_et,
            session_close_et,
            next_open_et,
        )

    return MarketSessionStatus(
        True,
        True,
        "MARKET_OPEN",
        XNYS_CALENDAR_NAME,
        session_open_et,
        session_close_et,
        next_open_et,
    )
