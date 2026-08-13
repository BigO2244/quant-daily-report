from __future__ import annotations

import datetime as dt
import sys
import types
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import brokers.alpaca_broker as alpaca_broker_module
from brokers.alpaca_broker import AlpacaBroker


ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc


class _Value:
    def __init__(self, value: str) -> None:
        self.value = value


class _Request:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = dict(kwargs)
        for key, value in kwargs.items():
            setattr(self, key, value)


class _BarClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[_Request] = []

    def get_stock_bars(self, request: _Request) -> object:
        self.requests.append(request)
        return self.response


class _TradingClient:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows
        self.calendar_requests: list[_Request] = []

    def get_calendar(self, request: _Request) -> list[object]:
        self.calendar_requests.append(request)
        return list(self.rows)


def _module(name: str, **attributes: object) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


@pytest.fixture(autouse=True)
def _install_fake_alpaca_calendar_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep calendar tests independent of whether alpaca-py is installed."""

    monkeypatch.setitem(sys.modules, "alpaca", _module("alpaca"))
    monkeypatch.setitem(sys.modules, "alpaca.trading", _module("alpaca.trading"))
    monkeypatch.setitem(
        sys.modules,
        "alpaca.trading.requests",
        _module("alpaca.trading.requests", GetCalendarRequest=_Request),
    )


def _install_fake_alpaca_bar_sdk(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response: object,
) -> _BarClient:
    client = _BarClient(response)

    class _HistoricalClient:
        def __new__(cls, *, api_key: str, secret_key: str) -> _BarClient:
            assert api_key == "paper-key"
            assert secret_key == "paper-secret"
            return client

    sort = SimpleNamespace(DESC=_Value("desc"))
    currencies = SimpleNamespace(USD=_Value("USD"))
    adjustment = SimpleNamespace(RAW=_Value("raw"))
    feed = SimpleNamespace(IEX=_Value("iex"))
    timeframe = SimpleNamespace(Minute=_Value("1Min"))

    modules = {
        "alpaca": _module("alpaca"),
        "alpaca.common": _module("alpaca.common"),
        "alpaca.common.enums": _module(
            "alpaca.common.enums",
            Sort=sort,
            SupportedCurrencies=currencies,
        ),
        "alpaca.data": _module("alpaca.data"),
        "alpaca.data.enums": _module(
            "alpaca.data.enums",
            Adjustment=adjustment,
            DataFeed=feed,
        ),
        "alpaca.data.historical": _module(
            "alpaca.data.historical",
            StockHistoricalDataClient=_HistoricalClient,
        ),
        "alpaca.data.requests": _module(
            "alpaca.data.requests",
            StockBarsRequest=_Request,
        ),
        "alpaca.data.timeframe": _module(
            "alpaca.data.timeframe",
            TimeFrame=timeframe,
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(
        alpaca_broker_module,
        "load_alpaca_env",
        lambda: SimpleNamespace(key_id="paper-key", secret_key="paper-secret"),
    )
    return client


def _bar(
    timestamp: dt.datetime,
    *,
    close: float,
    symbol: str = "AAPL",
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: float = 100.0,
    trade_count: float = 10.0,
    vwap: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        timestamp=timestamp,
        open=close if open_ is None else open_,
        high=close if high is None else high,
        low=close if low is None else low,
        close=close,
        volume=volume,
        trade_count=trade_count,
        vwap=close if vwap is None else vwap,
    )


def _bar_broker() -> AlpacaBroker:
    return AlpacaBroker(
        trading_client=SimpleNamespace(),
        paper=True,
        base_url="https://paper-api.alpaca.markets",
    )


def test_session_final_bars_uses_one_unlimited_batch_and_normalizes_complete_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_open = dt.datetime(2026, 8, 13, 9, 30, tzinfo=ET)
    session_close = dt.datetime(2026, 8, 13, 16, 0, tzinfo=ET)
    expected_start = dt.datetime(2026, 8, 13, 19, 59, tzinfo=UTC)
    response = SimpleNamespace(
        data={
            "AAPL": [
                _bar(
                    expected_start,
                    open_=99.0,
                    high=101.0,
                    low=98.5,
                    close=100.25,
                    volume=1234.0,
                    trade_count=87.0,
                    vwap=100.1,
                )
            ],
            "MSFT": [
                _bar(
                    expected_start,
                    symbol="MSFT",
                    open_=199.0,
                    high=202.0,
                    low=198.0,
                    close=201.5,
                    volume=4321.0,
                    trade_count=65.0,
                    vwap=200.75,
                )
            ],
        }
    )
    client = _install_fake_alpaca_bar_sdk(monkeypatch, response=response)

    result = _bar_broker().get_session_final_bars(
        ["msft", "AAPL", "AAPL", ""],
        session_open_et=session_open,
        session_close_et=session_close,
    )

    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.symbol_or_symbols == ["AAPL", "MSFT"]
    assert request.start == session_close - dt.timedelta(minutes=1)
    assert request.end == session_close - dt.timedelta(microseconds=1)
    assert "limit" not in request.kwargs
    assert request.asof == "2026-08-13"
    assert request.timeframe.value == "1Min"
    assert request.sort.value == "desc"
    assert request.feed.value == "iex"
    assert request.adjustment.value == "raw"
    assert request.currency.value == "USD"
    assert result == {
        "AAPL": {
            "symbol": "AAPL",
            "price": "100.25",
            "close": "100.25",
            "bar_start": "2026-08-13T15:59:00-04:00",
            "bar_end_exclusive": "2026-08-13T16:00:00-04:00",
            "open": "99.0",
            "high": "101.0",
            "low": "98.5",
            "volume": "1234.0",
            "trade_count": "87.0",
            "vwap": "100.1",
            "timeframe": "1Min",
            "feed": "IEX",
            "adjustment": "raw",
            "currency": "USD",
        },
        "MSFT": {
            "symbol": "MSFT",
            "price": "201.5",
            "close": "201.5",
            "bar_start": "2026-08-13T15:59:00-04:00",
            "bar_end_exclusive": "2026-08-13T16:00:00-04:00",
            "open": "199.0",
            "high": "202.0",
            "low": "198.0",
            "volume": "4321.0",
            "trade_count": "65.0",
            "vwap": "200.75",
            "timeframe": "1Min",
            "feed": "IEX",
            "adjustment": "raw",
            "currency": "USD",
        },
    }


def test_session_final_bars_uses_the_final_minute_of_an_early_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_open = dt.datetime(2026, 11, 27, 9, 30, tzinfo=ET)
    session_close = dt.datetime(2026, 11, 27, 13, 0, tzinfo=ET)
    response = SimpleNamespace(
        data={"AAPL": [_bar(dt.datetime(2026, 11, 27, 17, 59, tzinfo=UTC), close=50.0)]}
    )
    client = _install_fake_alpaca_bar_sdk(monkeypatch, response=response)

    result = _bar_broker().get_session_final_bars(
        ["AAPL"],
        session_open_et=session_open,
        session_close_et=session_close,
    )

    request = client.requests[0]
    assert request.start == dt.datetime(2026, 11, 27, 12, 59, tzinfo=ET)
    assert request.end == dt.datetime(
        2026, 11, 27, 12, 59, 59, 999999, tzinfo=ET
    )
    assert request.asof == "2026-11-27"
    assert result["AAPL"]["bar_start"] == "2026-11-27T12:59:00-05:00"
    assert result["AAPL"]["bar_end_exclusive"] == "2026-11-27T13:00:00-05:00"


def test_session_final_bars_does_not_fabricate_a_missing_requested_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(
        data={
            "AAPL": [
                _bar(
                    dt.datetime(2026, 8, 13, 19, 59, tzinfo=UTC),
                    close=100.0,
                )
            ]
        }
    )
    _install_fake_alpaca_bar_sdk(monkeypatch, response=response)

    result = _bar_broker().get_session_final_bars(
        ["AAPL", "MSFT"],
        session_open_et=dt.datetime(2026, 8, 13, 9, 30, tzinfo=ET),
        session_close_et=dt.datetime(2026, 8, 13, 16, 0, tzinfo=ET),
    )

    assert set(result) == {"AAPL"}
    assert "MSFT" not in result


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [
            _bar(dt.datetime(2026, 8, 13, 19, 59, tzinfo=UTC), close=100.0),
            _bar(dt.datetime(2026, 8, 13, 19, 59, tzinfo=UTC), close=101.0),
        ],
        [_bar(dt.datetime(2026, 8, 13, 19, 58, tzinfo=UTC), close=100.0)],
        [_bar(dt.datetime(2026, 8, 13, 20, 0, tzinfo=UTC), close=100.0)],
    ],
    ids=["missing", "duplicate", "earlier-minute", "at-close-extended-hour"],
)
def test_session_final_bars_omits_any_symbol_without_one_exact_final_minute_bar(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[SimpleNamespace],
) -> None:
    response = SimpleNamespace(data={"AAPL": rows})
    _install_fake_alpaca_bar_sdk(monkeypatch, response=response)

    result = _bar_broker().get_session_final_bars(
        ["AAPL", "MSFT"],
        session_open_et=dt.datetime(2026, 8, 13, 9, 30, tzinfo=ET),
        session_close_et=dt.datetime(2026, 8, 13, 16, 0, tzinfo=ET),
    )

    assert result == {}


def _calendar_row(
    trade_date: dt.date,
    *,
    close: dt.time = dt.time(16, 0),
) -> SimpleNamespace:
    return SimpleNamespace(
        date=trade_date,
        open=dt.time(9, 30),
        close=close,
    )


def test_market_session_calendar_normalizes_normal_and_early_closes() -> None:
    normal_day = dt.date(2026, 8, 13)
    normal_client = _TradingClient([_calendar_row(normal_day)])
    normal = AlpacaBroker(normal_client).get_market_session_calendar(
        normal_day.isoformat()
    )

    assert normal_client.calendar_requests[0].start == normal_day
    assert normal_client.calendar_requests[0].end == normal_day
    assert normal == {
        "calendar": "Alpaca",
        "trade_date": "2026-08-13",
        "session_open_et": "2026-08-13T09:30:00-04:00",
        "session_close_et": "2026-08-13T16:00:00-04:00",
    }

    early_day = dt.date(2026, 11, 27)
    early_client = _TradingClient([_calendar_row(early_day, close=dt.time(13, 0))])
    early = AlpacaBroker(early_client).get_market_session_calendar(
        early_day.isoformat()
    )

    assert early == {
        "calendar": "Alpaca",
        "trade_date": "2026-11-27",
        "session_open_et": "2026-11-27T09:30:00-05:00",
        "session_close_et": "2026-11-27T13:00:00-05:00",
    }


def test_market_session_calendar_accepts_alpaca_py_datetime_bounds() -> None:
    trade_date = dt.date(2026, 8, 13)
    row = SimpleNamespace(
        date=trade_date,
        open=dt.datetime(2026, 8, 13, 9, 30),
        close=dt.datetime(2026, 8, 13, 16, 0),
    )

    result = AlpacaBroker(_TradingClient([row])).get_market_session_calendar(
        trade_date.isoformat()
    )

    assert result["session_open_et"] == "2026-08-13T09:30:00-04:00"
    assert result["session_close_et"] == "2026-08-13T16:00:00-04:00"


@pytest.mark.parametrize(
    "rows,match",
    [
        ([], "did not return exactly one session"),
        (
            [
                _calendar_row(dt.date(2026, 8, 13)),
                _calendar_row(dt.date(2026, 8, 13)),
            ],
            "did not return exactly one session",
        ),
        (
            [_calendar_row(dt.date(2026, 8, 14))],
            "date does not match request",
        ),
        (
            [
                SimpleNamespace(
                    date=dt.date(2026, 8, 13),
                    open=dt.time(16, 0),
                    close=dt.time(9, 30),
                )
            ],
            "session bounds are invalid",
        ),
        (
            [
                SimpleNamespace(
                    date=dt.date(2026, 8, 13),
                    open="09:30",
                    close="16:00",
                )
            ],
            "session bounds are malformed",
        ),
        (
            [
                SimpleNamespace(
                    date=dt.date(2026, 8, 13),
                    open=dt.time(1, 0, tzinfo=dt.timezone.utc),
                    close=dt.time(2, 0, tzinfo=dt.timezone.utc),
                )
            ],
            "session bounds are invalid",
        ),
    ],
    ids=[
        "missing",
        "multiple",
        "date-mismatch",
        "inverted-bounds",
        "string-bounds",
        "timezone-shifts-date",
    ],
)
def test_market_session_calendar_fails_closed_on_missing_or_mismatched_rows(
    rows: list[object],
    match: str,
) -> None:
    broker = AlpacaBroker(_TradingClient(rows))

    with pytest.raises(RuntimeError, match=match):
        broker.get_market_session_calendar("2026-08-13")
