import datetime as dt
import gzip
import json

import pytest

from scripts.capture_aquila_ranking import (
    CaptureError, CaptureStore, group_issuers, normalize_panel, parse_membership,
    recording_session, validate_clock,
)
from core.portfolio_operating_model import content_hash

NOW = dt.datetime(2026, 9, 8, 11, tzinfo=dt.timezone.utc)
EPOCH = int(dt.datetime(2026, 9, 4, 20, tzinfo=dt.timezone.utc).timestamp())


def panel():
    members = [{'symbol': f'T{n}', 'issuer_id': str(n).zfill(10)} for n in range(1, 13)]
    issuers = group_issuers(members)
    quotes = {r['yahoo_symbol']: {'symbol': r['yahoo_symbol'], 'currency': 'USD',
              'marketCap': 1000 - n, 'regularMarketPrice': 100, 'regularMarketTime': EPOCH,
              'source_sha256': 'a' * 64} for n, r in enumerate(issuers)}
    return issuers, quotes


def normalize(issuers, quotes):
    return normalize_panel(issuers, quotes, previous_session='2026-09-04',
                           execution_session='2026-09-08', captured_at=NOW, sources=[])


def test_membership_uses_constituents_not_other_table():
    html = '<table><tr><th>Symbol</th></tr><tr><td>BAD</td></tr></table>'
    html += '<table id="constituents"><tr><th>Symbol</th><th>CIK</th></tr>'
    html += ''.join(f'<tr><td>T{i}</td><td>{i}</td></tr>' for i in range(1, 491)) + '</table>'
    members = parse_membership(html)
    assert len(members) == 490
    assert members[0] == {'symbol': 'T1', 'issuer_id': '0000000001'}
    with pytest.raises(CaptureError, match='coverage'):
        parse_membership(html, min_listings=491)
    with pytest.raises(CaptureError):
        parse_membership(html.replace('<td>T2</td>', '<td>T1</td>'))


def test_grouping_never_sums_share_class_capitalization():
    members = [{'symbol': f'T{n}', 'issuer_id': str(n)} for n in range(12)]
    members += [{'symbol': s, 'issuer_id': 'alphabet'} for s in ['GOOG', 'GOOGL']]
    members += [{'symbol': s, 'issuer_id': 'berkshire'} for s in ['BRK.A', 'BRK.B']]
    issuers = group_issuers(members)
    assert len(issuers) == 14
    alphabet = next(r for r in issuers if r['issuer_id'] == 'alphabet')
    assert alphabet['execution_symbol'] == 'GOOGL'
    berkshire = next(r for r in issuers if r['issuer_id'] == 'berkshire')
    assert berkshire['execution_symbol'] == 'BRK.B'
    assert berkshire['yahoo_symbol'] == 'BRK-B'
    with pytest.raises(CaptureError, match='unreviewed'):
        group_issuers(members + [{'symbol': 'OTHER', 'issuer_id': 'alphabet'}])


def test_complete_panel_cutoff_deterministic_and_hash_valid():
    issuers, quotes = panel()
    quotes['T10']['marketCap'] = quotes['T11']['marketCap']
    result = normalize(issuers, quotes)
    assert result['rank_cutoff'] == {'10': '0000000010', '11': '0000000011'}
    assert len(result['issuers']) == 12 and result['readiness'] == 'TIER_B'
    assert result['content_hash'] == content_hash({k: v for k, v in result.items() if k != 'content_hash'})


@pytest.mark.parametrize('field,value', [('marketCap', None), ('marketCap', -1),
    ('marketCap', float('inf')), ('regularMarketPrice', 0), ('currency', 'CAD'),
    ('regularMarketTime', EPOCH + 86400 * 4), ('regularMarketTime', 'formatted-date'),
    ('regularMarketTime', EPOCH - 86400), ('source_sha256', '')])
def test_bad_quote_blocks_entire_formation(field, value):
    issuers, quotes = panel()
    quotes['T1'][field] = value
    with pytest.raises(CaptureError):
        normalize(issuers, quotes)


def test_missing_member_blocks_and_holiday_clock_is_valid():
    issuers, quotes = panel()
    del quotes['T12']
    with pytest.raises(CaptureError, match='coverage'):
        normalize(issuers, quotes)
    validate_clock('2026-09-04', '2026-09-08', NOW)
    with pytest.raises(CaptureError):
        validate_clock('2026-09-03', '2026-09-08', NOW)
    with pytest.raises(CaptureError):
        validate_clock('2026-09-04', '2026-09-08', NOW.replace(hour=15))


def test_immutable_store_preserves_sources(tmp_path):
    store = CaptureStore(tmp_path / 'capture')
    ref = store.write('raw.json.gz', {'observation': 1}, compress=True)
    assert json.loads(gzip.decompress((store.root / 'raw.json.gz').read_bytes())) == {'observation': 1}
    assert len(ref['sha256']) == 64
    with pytest.raises(FileExistsError):
        store.write('raw.json.gz', {'observation': 2}, compress=True)
    assert store.root.joinpath('raw.json.gz').stat().st_mode & 0o222 == 0


def test_recording_excludes_credentials_and_retains_epoch(tmp_path, monkeypatch):
    requests = pytest.importorskip('curl_cffi.requests')
    class Response:
        status_code = 200
        content = b'provider body containing secret'
        def json(self):
            return {'quoteResponse': {'result': [{'symbol': 'AAPL', 'marketCap': 123,
                'regularMarketPrice': 100, 'regularMarketTime': EPOCH, 'currency': 'USD',
                'crumb': 'DO_NOT_RETAIN', 'authorization': 'DO_NOT_RETAIN'}]}}
    monkeypatch.setattr(requests.Session, 'request', lambda *a, **kw: Response())
    class Budget:
        deadline = float('inf')
        stopped = False
        def reserve(self):
            return 1
    store = CaptureStore(tmp_path / 'capture')
    session = recording_session(store, Budget())
    session.get('https://query1.finance.yahoo.com/v7/finance/quote?crumb=DO_NOT_RETAIN')
    body = gzip.decompress(store.root.joinpath('quote-0001.json.gz').read_bytes())
    assert b'DO_NOT_RETAIN' not in body and b'authorization' not in body
    assert session.quotes['AAPL']['regularMarketTime'] == EPOCH
    session.close()


def test_probe_is_nonformation_and_bounded(tmp_path, monkeypatch):
    import sys
    import time
    import types
    from scripts import capture_aquila_ranking as module
    html = '<table id="constituents"><tr><th>Symbol</th><th>CIK</th></tr>'
    html += '<tr><td>AAPL</td><td>1</td></tr>'
    html += ''.join(f'<tr><td>T{i}</td><td>{i}</td></tr>' for i in range(2, 491)) + '</table>'
    cache = types.SimpleNamespace(get_cookie_cache=lambda: 'original')
    original = cache.get_cookie_cache
    observed_budgets = []
    class Session:
        def __init__(self, store, budget):
            self.store, self.budget, self.quotes = store, budget, {}
            observed_budgets.append((store.max_bytes, budget.max_http, budget.deadline - time.monotonic()))
        def get(self, url):
            self.budget.reserve()
            self.store.write('membership.json.gz', {'html': html}, compress=True)
            return types.SimpleNamespace(status_code=200, text=html)
        def close(self):
            pass
    class Ticker:
        def __init__(self, symbol, session):
            assert symbol == 'AAPL'
            self.session = session
        def get_info(self):
            # Cookie persistence is replaced only while the capture runs.
            assert cache.get_cookie_cache() != 'original'
            self.session.budget.reserve()
            quote = {'symbol': 'AAPL', 'currency': 'USD', 'marketCap': 1000,
                     'regularMarketPrice': 100, 'regularMarketTime': EPOCH}
            ref = self.session.store.write('quote.json.gz', quote, compress=True)
            self.session.quotes['AAPL'] = {**quote, 'source_sha256': ref['sha256']}
            return quote
    monkeypatch.setitem(sys.modules, 'yfinance', types.SimpleNamespace(Ticker=Ticker, cache=cache, __version__='test'))
    monkeypatch.setattr(module, 'recording_session', Session)
    result_path = module.probe_source(output_root=tmp_path)
    result = json.loads(result_path.read_text())
    assert result_path.name == 'probe.json'
    assert result['accepted'] is False and result['status'] == 'PASS'
    assert result['classification'] == 'SOURCE_ONLY_NOT_FORMATION'
    assert 'formation_id' not in result and 'formation_session' not in result
    assert not list(tmp_path.glob('*.json'))
    assert result['logical_issuer_calls'] == 1 and result['http_requests'] == 2
    assert observed_budgets[0][:2] == (4 * 1024 * 1024, 12)
    assert 59 <= observed_budgets[0][2] <= 60
    assert cache.get_cookie_cache is original


def test_parameterized_probe_limits_stop_dispatch_and_bytes(tmp_path, monkeypatch):
    from scripts import capture_aquila_ranking as module
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    monkeypatch.setattr(module.time, 'monotonic', lambda: 1)
    budget = module.RequestBudget(max_http=12, max_seconds=60)
    for _ in range(12):
        budget.reserve()
    with pytest.raises(CaptureError, match='ceiling'):
        budget.reserve()
    assert budget.count == 12
    store = CaptureStore(tmp_path / 'small', max_bytes=100)
    with pytest.raises(CaptureError, match='ceiling'):
        store.write('large.json', 'X' * 100)
    store.write('failure.json', {'accepted': False})
    assert store.retained_bytes <= 100
    with pytest.raises(CaptureError, match='invalid request budget'):
        module.RequestBudget(max_http=1601)
