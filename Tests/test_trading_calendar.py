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
