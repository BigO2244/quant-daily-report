import datetime as dt
import gzip
import json
from pathlib import Path

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
    monkeypatch.setattr(module, 'quote_client', lambda session: object())
    monkeypatch.setattr(module, 'collect_quote', lambda symbol, session, budget, **kw: Ticker(symbol, session).get_info())
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


def test_quote_transient_retries_share_budget_and_skip_company_info(tmp_path, monkeypatch):
    from scripts import capture_aquila_ranking as module
    import time
    from types import SimpleNamespace
    data = pytest.importorskip('yfinance.data')
    requests = pytest.importorskip('curl_cffi.requests')
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    calls = []
    statuses = iter([502, 503, 200])
    class Response:
        content = b'not retained'
        def __init__(self, status): self.status_code = status
        def json(self): return {'quoteResponse': {'result': [{'symbol': 'AAPL', 'currency': 'USD', 'marketCap': 100, 'regularMarketPrice': 10, 'regularMarketTime': EPOCH}]}}
    def request(self, method, url, **kwargs):
        calls.append((url, kwargs))
        return Response(next(statuses))
    monkeypatch.setattr(requests.Session, 'request', request)
    class Client:
        def __init__(self, session): self.session = session
        def get_raw_json(self, url, params): return self.session.get(url, params=params).json()
    monkeypatch.setattr(data, 'YfData', Client)
    budget = module.RequestBudget(max_http=3, max_seconds=60)
    session = recording_session(CaptureStore(tmp_path / 'retry'), budget)
    module.collect_quote('AAPL', session, budget)
    assert budget.count == 3 and not budget.stopped
    assert session.quotes['AAPL']['regularMarketTime'] == EPOCH
    assert all(url.endswith('/v7/finance/quote') for url, _ in calls)
    assert all(kw['params'] == {'symbols': 'AAPL', 'formatted': 'false'} for _, kw in calls)
    assert len(list((tmp_path / 'retry').glob('http-*.gz'))) == 3
    session.close()


@pytest.mark.parametrize('failure,expected_calls', [('transient', 3), ('malformed', 1), ('rate_limit', 1)])
def test_quote_retry_policy_never_retries_bad_evidence_or_rate_limit(monkeypatch, failure, expected_calls):
    from types import SimpleNamespace
    from scripts import capture_aquila_ranking as module
    data = pytest.importorskip('yfinance.data')
    calls = []
    class Client:
        def __init__(self, session): pass
        def get_raw_json(self, url, params):
            calls.append(url)
            if failure == 'transient': raise module.TransientQuoteError('transient Yahoo quote HTTP 502')
            if failure == 'rate_limit': raise CaptureError('provider rate limit or runtime deadline')
            return {'quoteResponse': {'result': []}}
    monkeypatch.setattr(data, 'YfData', Client)
    with pytest.raises(CaptureError):
        module.collect_quote('AAPL', SimpleNamespace(quotes={}), SimpleNamespace(stopped=False))
    assert len(calls) == expected_calls


def test_failure_frames_never_include_exception_message_or_locals():
    from scripts.capture_aquila_ranking import safe_failure
    try:
        credential = 'DO_NOT_RETAIN_SECRET'
        raise TypeError('https://example.invalid?crumb=' + credential)
    except TypeError as exc:
        result = safe_failure(exc)
    assert result['error_type'] == 'TypeError'
    assert result['frames'][-1]['function'] == 'test_failure_frames_never_include_exception_message_or_locals'
    assert 'DO_NOT_RETAIN' not in json.dumps(result)
    assert 'crumb' not in json.dumps(result)


def test_quote_stopped_global_budget_prevents_dispatch(monkeypatch):
    from types import SimpleNamespace
    from scripts import capture_aquila_ranking as module
    data = pytest.importorskip('yfinance.data')
    class Client:
        def __init__(self, session): pass
        def get_raw_json(self, *args, **kwargs): pytest.fail('global stop must prevent request')
    monkeypatch.setattr(data, 'YfData', Client)
    with pytest.raises(CaptureError, match='collection stopped'):
        module.collect_quote('AAPL', SimpleNamespace(quotes={}), SimpleNamespace(stopped=True))


@pytest.mark.parametrize('field,value', [('symbol', 'WRONG'), ('currency', 'CAD'), ('regularMarketTime', EPOCH + 1), ('regularMarketPrice', 99), ('marketCap', None), ('marketCap', 0)])
def test_cap_fallback_requires_same_identity_epoch_and_real_value(field, value):
    from types import SimpleNamespace
    from scripts import capture_aquila_ranking as module
    raw = {'symbol': 'AZO', 'currency': 'USD', 'regularMarketPrice': 100, 'regularMarketTime': EPOCH, 'source_sha256': 'a' * 64}
    cap = {**raw, 'marketCap': 500, 'source_sha256': 'b' * 64, field: value}
    session = SimpleNamespace(quotes={'AZO': raw}, cap_quotes={'AZO': cap})
    class Client:
        def get_raw_json(self, *args, **kwargs): return {}
    with pytest.raises(CaptureError):
        module.collect_quote('AZO', session, SimpleNamespace(stopped=False), client=Client())


def test_cap_fallback_preserves_separate_field_provenance():
    from types import SimpleNamespace
    from scripts import capture_aquila_ranking as module
    raw = {'symbol': 'AZO', 'currency': 'USD', 'regularMarketPrice': 100, 'regularMarketTime': EPOCH, 'source_sha256': 'a' * 64}
    cap = {**raw, 'marketCap': 500, 'source_sha256': 'b' * 64}
    session = SimpleNamespace(quotes={'AZO': raw}, cap_quotes={'AZO': cap})
    calls = []
    class Client:
        def get_raw_json(self, url, params): calls.append((url, params))
    module.collect_quote('AZO', session, SimpleNamespace(stopped=False), client=Client())
    assert raw['marketCap'] == 500 and raw['source_sha256'] == 'a' * 64
    assert raw['market_cap_source_sha256'] == 'b' * 64
    assert calls[-1][1] == {'modules': 'price', 'formatted': 'false'}


@pytest.mark.parametrize('include_excluded', [False, True])
def test_complete_500_issuer_capture_is_immutable_and_quote_only(tmp_path, monkeypatch, include_excluded):
    from scripts import capture_aquila_ranking as module
    data = pytest.importorskip('yfinance.data')
    requests = pytest.importorskip('curl_cffi.requests')
    original_datetime = dt.datetime
    now = original_datetime(2026, 9, 9, 9, tzinfo=dt.timezone.utc)
    epoch = int(original_datetime(2026, 9, 8, 20, tzinfo=dt.timezone.utc).timestamp())
    class FixedClock(original_datetime):
        @classmethod
        def now(cls, tz=None): return now if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(module.dt, 'datetime', FixedClock)
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    html = '<table id="constituents"><tr><th>Symbol</th><th>CIK</th></tr>'
    html += ''.join(f'<tr><td>T{i}</td><td>{i}</td></tr>' for i in range(1, 501)) + '</table>'
    if include_excluded:
        html = html.replace('<td>T500</td>', '<td>AZO</td>')
    urls = []
    class Response:
        status_code = 200
        content = b'synthetic public response'
        text = html
        def __init__(self, symbol=None): self.symbol = symbol
        def json(self): return {'quoteResponse': {'result': [{'symbol': self.symbol, 'currency': 'USD', 'marketCap': 100000 - int(self.symbol[1:]), 'regularMarketPrice': 100, 'regularMarketTime': epoch}]}}
    def request(self, method, url, **kwargs):
        urls.append(url)
        return Response((kwargs.get('params') or {}).get('symbols'))
    monkeypatch.setattr(requests.Session, 'request', request)
    class Client:
        def __init__(self, session): self.session = session
        def get_raw_json(self, url, params): return self.session.get(url, params=params).json()
    monkeypatch.setattr(data, 'YfData', Client)
    path = module.capture_ranking(output_root=tmp_path, previous_session='2026-09-08', execution_session='2026-09-09')
    result = json.loads(path.read_text())
    assert len(result['issuers']) == 500 - int(include_excluded)
    eligibility = result['universe_eligibility']
    assert eligibility['membership_issuer_count'] == 500
    assert eligibility['eligible_issuer_count'] == 500 - int(include_excluded)
    assert eligibility['policy']['symbols'] == ['AZO']
    assert [row['execution_symbol'] for row in eligibility['excluded_issuers']] == (['AZO'] if include_excluded else [])
    assert all(row['execution_symbol'] != 'AZO' for row in result['issuers'])
    assert result['content_hash'] == content_hash({k: v for k, v in result.items() if k != 'content_hash'})
    receipt = json.loads(Path(result['receipt']['path']).read_text())
    assert receipt['http_requests'] == 501 - int(include_excluded)
    assert all('quoteSummary' not in url and 'timeseries' not in url for url in urls)
    assert path.stat().st_mode & 0o222 == 0
    with pytest.raises(CaptureError, match='overwrite refused'):
        module.capture_ranking(output_root=tmp_path, previous_session='2026-09-08', execution_session='2026-09-09')


def test_price_fragment_is_sanitized_and_hash_bound(tmp_path, monkeypatch):
    import hashlib
    from scripts import capture_aquila_ranking as module
    requests = pytest.importorskip('curl_cffi.requests')
    class Response:
        status_code = 200
        content = b'provider text must not be retained'
        def json(self):
            return {'quoteSummary': {'result': [{'price': {'symbol': 'AZO', 'currency': 'USD', 'regularMarketTime': {'raw': EPOCH}, 'regularMarketPrice': {'raw': 100}, 'marketCap': {'raw': 500, 'fmt': 'ignore'}, 'crumb': 'DO_NOT_RETAIN'}}]}}
    monkeypatch.setattr(requests.Session, 'request', lambda *a, **kw: Response())
    budget = module.RequestBudget(max_http=1, max_seconds=60)
    store = CaptureStore(tmp_path / 'cap')
    session = recording_session(store, budget)
    session.get('https://query2.finance.yahoo.com/v10/finance/quoteSummary/AZO', params={'modules': 'price', 'crumb': 'DO_NOT_RETAIN'})
    source = store.root / 'cap-0001.json.gz'
    raw = json.loads(gzip.decompress(source.read_bytes()))
    assert raw['price'] == {'symbol': 'AZO', 'currency': 'USD', 'regularMarketTime': EPOCH, 'regularMarketPrice': 100, 'marketCap': 500}
    assert session.cap_quotes['AZO']['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert 'DO_NOT_RETAIN' not in json.dumps(raw) and 'provider text' not in json.dumps(raw)
    session.close()


def test_owner_exclusion_does_not_excuse_another_missing_quote():
    from scripts.capture_aquila_ranking import eligible_issuers
    issuers, quotes = panel()
    issuers.append({'issuer_id': 'owner-excluded', 'execution_symbol': 'AZO', 'yahoo_symbol': 'AZO', 'listing_symbols': ['AZO']})
    eligible, audit = eligible_issuers(issuers, "2026-09-09")
    assert len(eligible) == 12 and audit['excluded_issuers'][0]['reason'] == 'OWNER_EXCLUDED'
    for row in quotes.values():
        row['regularMarketTime'] = int(dt.datetime(2026, 9, 8, 20, tzinfo=dt.timezone.utc).timestamp())
    def current_panel():
        return normalize_panel(issuers, quotes, previous_session='2026-09-08', execution_session='2026-09-09', captured_at=dt.datetime(2026, 9, 9, 9, tzinfo=dt.timezone.utc), sources=[])
    assert len(current_panel()['issuers']) == 12
    del quotes['T1']
    with pytest.raises(CaptureError, match='coverage'):
        current_panel()


def test_exclusion_effective_date_and_issuer_identity():
    from scripts.capture_aquila_ranking import eligible_issuers
    issuer = {'issuer_id': '0000866787', 'execution_symbol': 'RENAMED', 'yahoo_symbol': 'RENAMED', 'listing_symbols': ['RENAMED']}
    old, old_audit = eligible_issuers([issuer], '2026-09-08')
    new, new_audit = eligible_issuers([issuer], '2026-09-09')
    assert old == [issuer] and not old_audit['policy_effective']
    assert new == [] and new_audit['policy_effective']
    assert new_audit['policy_hash'] == content_hash(new_audit['policy'])
