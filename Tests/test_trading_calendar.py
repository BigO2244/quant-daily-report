import datetime as dt
from zoneinfo import ZoneInfo

import paper.trading_calendar as trading_calendar


def test_good_friday_is_market_closed() -> None:
    now_et = dt.datetime(2026, 4, 3, 9, 35, tzinfo=ZoneInfo("America/New_York"))

    status = trading_calendar.market_session_status(
        run_date="2026-04-03",
        now_et=now_et,
        cutoff_time_et="15:45",
    )

    assert trading_calendar.is_trading_day("2026-04-03") is False
    assert trading_calendar.next_trading_day("2026-04-03") == "2026-04-06"
    assert trading_calendar.prev_trading_day("2026-04-06") == "2026-04-02"
    assert status.is_trading_day is False
    assert status.is_open_now is False
    assert status.reason == "MARKET_CLOSED_DAY"
    assert status.next_open_et.isoformat() == "2026-04-06T09:30:00-04:00"


def test_2026_memorial_day_and_adjacent_sessions() -> None:
    assert trading_calendar.is_trading_day("2026-05-22") is True
    assert trading_calendar.is_trading_day("2026-05-25") is False
    assert trading_calendar.is_trading_day("2026-05-26") is True
    assert trading_calendar.prev_trading_day("2026-05-26") == "2026-05-22"
    assert trading_calendar.next_trading_day("2026-05-22") == "2026-05-26"


def test_juneteenth_and_observed_fixed_holidays_are_market_closed() -> None:
    assert trading_calendar.is_trading_day("2026-06-19") is False
    assert trading_calendar.is_trading_day("2026-07-03") is False
    assert trading_calendar.is_trading_day("2027-12-24") is False


def test_early_close_dates_remain_trading_sessions() -> None:
    assert trading_calendar.is_trading_day("2026-11-27") is True
    assert trading_calendar.is_trading_day("2026-12-24") is True
    assert trading_calendar.is_early_close_day("2026-11-27") is True
    assert trading_calendar.is_early_close_day("2026-12-24") is True
    assert trading_calendar.is_early_close_day("2026-07-03") is False


def test_early_close_session_uses_one_pm_close() -> None:
    before_close = trading_calendar.market_session_status(
        run_date="2026-11-27",
        now_et=dt.datetime(
            2026,
            11,
            27,
            12,
            59,
            59,
            999999,
            tzinfo=ZoneInfo("America/New_York"),
        ),
        cutoff_time_et="15:45",
    )
    at_close = trading_calendar.market_session_status(
        run_date="2026-11-27",
        now_et=dt.datetime(2026, 11, 27, 13, 0, tzinfo=ZoneInfo("America/New_York")),
        cutoff_time_et="15:45",
    )

    assert before_close.is_trading_day is True
    assert before_close.is_open_now is True
    assert before_close.session_close_et.isoformat() == "2026-11-27T13:00:00-05:00"
    assert at_close.is_trading_day is True
    assert at_close.is_open_now is False
    assert at_close.reason == "AFTER_MARKET_CUTOFF"


def test_regular_session_close_boundary_is_half_open() -> None:
    before_close = trading_calendar.market_session_status(
        run_date="2026-08-12",
        now_et=dt.datetime(
            2026,
            8,
            12,
            15,
            59,
            59,
            999999,
            tzinfo=ZoneInfo("America/New_York"),
        ),
        cutoff_time_et="16:00",
    )
    at_close = trading_calendar.market_session_status(
        run_date="2026-08-12",
        now_et=dt.datetime(2026, 8, 12, 16, 0, tzinfo=ZoneInfo("America/New_York")),
        cutoff_time_et="16:00",
    )

    assert before_close.is_open_now is True
    assert before_close.reason == "MARKET_OPEN"
    assert at_close.is_open_now is False
    assert at_close.reason == "AFTER_MARKET_CUTOFF"
    assert at_close.next_open_et.isoformat() == "2026-08-13T09:30:00-04:00"
