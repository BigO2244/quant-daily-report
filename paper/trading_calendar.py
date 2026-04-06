# paper/trading_calendar.py
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd


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


def _observed_fixed_holiday(year: int, month: int, day: int) -> dt.date:
    holiday = dt.date(year, month, day)
    if holiday.weekday() == calendar.SATURDAY:
        return holiday - dt.timedelta(days=1)
    if holiday.weekday() == calendar.SUNDAY:
        return holiday + dt.timedelta(days=1)
    return holiday


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> dt.date:
    first_day = dt.date(year, month, 1)
    offset = (weekday - first_day.weekday()) % 7
    return first_day + dt.timedelta(days=offset + (occurrence - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    last_dom = calendar.monthrange(year, month)[1]
    last_day = dt.date(year, month, last_dom)
    offset = (last_day.weekday() - weekday) % 7
    return last_day - dt.timedelta(days=offset)


def _easter_sunday(year: int) -> dt.date:
    # Anonymous Gregorian algorithm.
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


@lru_cache(maxsize=None)
def _us_market_holiday_dates(year: int) -> frozenset[str]:
    holidays = {
        _observed_fixed_holiday(year, 1, 1),   # New Year's Day
        _nth_weekday(year, 1, calendar.MONDAY, 3),   # MLK Day
        _nth_weekday(year, 2, calendar.MONDAY, 3),   # Presidents Day
        _easter_sunday(year) - dt.timedelta(days=2),   # Good Friday
        _last_weekday(year, 5, calendar.MONDAY),   # Memorial Day
        _observed_fixed_holiday(year, 7, 4),   # Independence Day
        _nth_weekday(year, 9, calendar.MONDAY, 1),   # Labor Day
        _nth_weekday(year, 11, calendar.THURSDAY, 4),   # Thanksgiving
        _observed_fixed_holiday(year, 12, 25),   # Christmas
    }
    if year >= 2022:
        holidays.add(_observed_fixed_holiday(year, 6, 19))   # Juneteenth
    return frozenset(day.isoformat() for day in holidays)


def _is_weekday(date_str: str) -> bool:
    return pd.Timestamp(date_str).weekday() < 5


def is_trading_day(date_str: str) -> bool:
    if not _is_weekday(date_str):
        return False

    date_obj = pd.Timestamp(date_str).date()
    holiday_dates = (
        _us_market_holiday_dates(date_obj.year - 1)
        | _us_market_holiday_dates(date_obj.year)
        | _us_market_holiday_dates(date_obj.year + 1)
    )
    return date_obj.isoformat() not in holiday_dates


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
