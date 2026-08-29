"""Fail-closed NYSE session calendar for the approved research automation window."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Dict, FrozenSet, Iterable
from zoneinfo import ZoneInfo

from projects.alpha_lab.factory import ContractValidationError


_HOLIDAYS: Dict[int, FrozenSet[date]] = {
    2026: frozenset(date.fromisoformat(value) for value in (
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
        "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
        "2026-11-26", "2026-12-25",
    )),
    2027: frozenset(date.fromisoformat(value) for value in (
        "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
        "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
        "2027-11-25", "2027-12-24",
    )),
    2028: frozenset(date.fromisoformat(value) for value in (
        "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29",
        "2028-06-19", "2028-07-04", "2028-09-04", "2028-11-23",
        "2028-12-25",
    )),
}
_EARLY_CLOSES: Dict[date, time] = {
    date(2026, 11, 27): time(13, 0),
    date(2026, 12, 24): time(13, 0),
    date(2027, 11, 26): time(13, 0),
    date(2028, 7, 3): time(13, 0),
    date(2028, 11, 24): time(13, 0),
}
_TIMEZONE = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Session:
    session_date: date
    status: str
    close_at: datetime | None
    decision_not_before: datetime | None


def session_for(day: date, *, decision_buffer_minutes: int = 15) -> Session:
    """Return a scheduled NYSE session, rejecting years not explicitly sourced."""

    if day.year not in _HOLIDAYS:
        raise ContractValidationError(
            "NYSE calendar year {} is not approved; update from the official calendar".format(day.year)
        )
    if day.weekday() >= 5:
        return Session(day, "CLOSED_WEEKEND", None, None)
    if day in _HOLIDAYS[day.year]:
        return Session(day, "CLOSED_HOLIDAY", None, None)
    close_clock = _EARLY_CLOSES.get(day, time(16, 0))
    close_at = datetime.combine(day, close_clock, tzinfo=_TIMEZONE)
    return Session(
        day,
        "OPEN_EARLY_CLOSE" if day in _EARLY_CLOSES else "OPEN_REGULAR",
        close_at,
        close_at - timedelta(minutes=decision_buffer_minutes),
    )


def trading_sessions(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        if session_for(current).close_at is not None:
            yield current
        current += timedelta(days=1)
