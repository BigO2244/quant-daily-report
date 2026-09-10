from types import SimpleNamespace

import pandas as pd
import pytest

from paper import paper_broker as pb
from scripts.live_pilot_build_plan_from_precompute import _hydrate_prices


def test_class_share_prices_restore_execution_symbols(monkeypatch):
    calls = []

    def download(**kwargs):
        calls.append(kwargs['tickers'])
        assert kwargs['tickers'] == ['BRK-B', 'AAPL']
        return pd.DataFrame([[502.0, 240.0]], index=pd.to_datetime(['2026-09-10']),
                            columns=pd.MultiIndex.from_tuples([('Open', 'BRK-B'), ('Open', 'AAPL')]))

    monkeypatch.setattr(pb, '_load_yfinance', lambda: SimpleNamespace(
        download=download, set_tz_cache_location=lambda _: None))
    prices, sources, missing = _hydrate_prices(
        ['BRK.B', 'AAPL'], payload={}, run_date='2026-09-10',
        price_fetcher=pb._fetch_open_prices_yfinance_impl)
    assert calls == [['BRK-B', 'AAPL']]
    assert prices == {'BRK.B': 502.0, 'AAPL': 240.0}
    assert missing == []
    assert sources['BRK.B'] == 'yfinance_open'


def test_alias_collision_blocks_before_fetch(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('ambiguous aliases must not reach provider')
    monkeypatch.setattr(pb, '_fetch_open_prices_yfinance_provider', forbidden)
    with pytest.raises(ValueError, match='Ambiguous'):
        pb._fetch_open_prices_yfinance_impl(['BRK.B', 'BRK-B'], '2026-09-10')


@pytest.mark.parametrize('price', [float('nan'), 0.0, -1.0])
def test_invalid_class_share_price_still_blocks(monkeypatch, price):
    monkeypatch.setattr(pb, '_fetch_open_prices_yfinance_provider', lambda symbols, date:
                        pd.DataFrame([{'ticker': 'BRK-B', 'open': price, 'price_date': date}]))
    prices, _, missing = _hydrate_prices(['BRK.B'], payload={}, run_date='2026-09-10',
                                        price_fetcher=pb._fetch_open_prices_yfinance_impl)
    assert prices == {}
    assert missing == ['BRK.B']


def test_unexpected_provider_symbol_blocks(monkeypatch):
    monkeypatch.setattr(pb, '_fetch_open_prices_yfinance_provider', lambda symbols, date:
                        pd.DataFrame([{'ticker': 'OTHER', 'open': 502, 'price_date': date}]))
    with pytest.raises(ValueError, match='Unexpected'):
        pb._fetch_open_prices_yfinance_impl(['BRK.B'], '2026-09-10')
