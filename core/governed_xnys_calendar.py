"""Dependency-free governed XNYS session calendar for artifact validation."""

from __future__ import annotations

import calendar
import datetime as dt
from functools import lru_cache


XNYS_CALENDAR_POLICY_ID = "XNYS_US_EQUITIES_HOLIDAY_RULES_V1"


def _observed_fixed_holiday(year: int, month: int, day: int) -> dt.date:
    holiday = dt.date(year, month, day)
    if holiday.weekday() == calendar.SATURDAY:
        return holiday - dt.timedelta(days=1)
    if holiday.weekday() == calendar.SUNDAY:
        return holiday + dt.timedelta(days=1)
    return holiday


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> dt.date:
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + dt.timedelta(days=offset + (occurrence - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    last = dt.date(year, month, calendar.monthrange(year, month)[1])
    return last - dt.timedelta(days=(last.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> dt.date:
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
def _holiday_dates(year: int) -> frozenset[dt.date]:
    holidays = {
        _observed_fixed_holiday(year, 1, 1),
        _nth_weekday(year, 1, calendar.MONDAY, 3),
        _nth_weekday(year, 2, calendar.MONDAY, 3),
        _easter_sunday(year) - dt.timedelta(days=2),
        _last_weekday(year, 5, calendar.MONDAY),
        _observed_fixed_holiday(year, 7, 4),
        _nth_weekday(year, 9, calendar.MONDAY, 1),
        _nth_weekday(year, 11, calendar.THURSDAY, 4),
        _observed_fixed_holiday(year, 12, 25),
    }
    if year >= 2022:
        holidays.add(_observed_fixed_holiday(year, 6, 19))
    return frozenset(holidays)


def is_xnys_session(value: str) -> bool:
    day = dt.date.fromisoformat(value)
    return day.weekday() < 5 and day not in (
        _holiday_dates(day.year - 1)
        | _holiday_dates(day.year)
        | _holiday_dates(day.year + 1)
    )


def next_xnys_session(value: str) -> str:
    day = dt.date.fromisoformat(value)
    while True:
        day += dt.timedelta(days=1)
        if is_xnys_session(day.isoformat()):
            return day.isoformat()


def previous_xnys_session(value: str) -> str:
    day = dt.date.fromisoformat(value)
    while True:
        day -= dt.timedelta(days=1)
        if is_xnys_session(day.isoformat()):
            return day.isoformat()


def xnys_session_window(value: str, *, count: int) -> list[str]:
    if count <= 0 or not is_xnys_session(value):
        raise ValueError("XNYS session window requires a positive count and session")
    sessions = [value]
    while len(sessions) < count:
        sessions.append(previous_xnys_session(sessions[-1]))
    return list(reversed(sessions))


__all__ = [
    "XNYS_CALENDAR_POLICY_ID", "is_xnys_session", "next_xnys_session",
    "previous_xnys_session", "xnys_session_window",
]
