#!/usr/bin/env python3
"""Bounded prospective Yahoo company-cap observation; no broker or orders.

Capture is opt-in via capture_ranking(). Importing this module never contacts a
provider. Current Wikipedia membership and Yahoo issuer caps are Tier B proxies.
"""
from __future__ import annotations

import argparse
import concurrent.futures
from contextlib import contextmanager
import datetime as dt
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
import traceback
from html.parser import HTMLParser
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.governed_xnys_calendar import is_xnys_session, next_xnys_session
from core.portfolio_operating_model import content_hash
from core.aquila_monthly import owner_exclusion_policy

MEMBERSHIP_URL = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
MAX_HTTP, MAX_ISSUERS, MAX_BYTES = 1600, 500, 64 * 1024 * 1024
MAX_SECONDS, MAX_WORKERS, REQUESTS_PER_SECOND = 900, 4, 4
ET = ZoneInfo('America/New_York')
PREFERENCES = ({'GOOG', 'GOOGL'}, 'GOOGL'), ({'FOX', 'FOXA'}, 'FOXA'), ({'NWS', 'NWSA'}, 'NWSA'), ({'BRK.A', 'BRK.B'}, 'BRK.B')


class CaptureError(ValueError):
    pass


class TransientQuoteError(CaptureError):
    """A recorded 502/503/504 on the required Yahoo quote endpoint."""


def safe_failure(exc):
    # Frame identities aid diagnosis without persisting exception text, locals,
    # source lines, request query strings, credentials or provider bodies.
    return {
        'error_type': type(exc).__name__,
        'reason': str(exc) if isinstance(exc, CaptureError) else 'provider_or_capture_failure',
        'frames': [{'file': Path(frame.filename).name, 'function': frame.name,
                    'line': frame.lineno} for frame in traceback.extract_tb(exc.__traceback__)],
    }


def quote_client(session):
    from yfinance.data import YfData
    return YfData(session=session)


def collect_quote(symbol, session, budget, *, client=None):
    """Collect raw quote fields with a same-epoch, provider-cap-only fallback.

    No shares-times-price estimate, dropped issuer, or stale replacement is
    permitted. Both field sources remain immutable and separately identified.
    """
    client = client if client is not None else quote_client(session)
    def fetch(url, params):
        for attempt in range(3):
            if budget.stopped:
                raise CaptureError('collection stopped')
            try:
                return client.get_raw_json(url, params=params)
            except TransientQuoteError:
                if attempt == 2:
                    raise
    fetch('https://query1.finance.yahoo.com/v7/finance/quote',
          {'symbols': symbol, 'formatted': 'false'})
    if symbol not in session.quotes:
        raise CaptureError('required raw quote missing for ' + symbol)
    quote = session.quotes[symbol]
    if quote.get('marketCap') is None:
        fetch('https://query2.finance.yahoo.com/v10/finance/quoteSummary/' + symbol,
              {'modules': 'price', 'formatted': 'false'})
        cap = session.cap_quotes.get(symbol, {})
        for field in ('symbol', 'currency', 'regularMarketTime', 'regularMarketPrice'):
            if quote.get(field) is None or cap.get(field) != quote[field]:
                raise CaptureError('market cap fallback identity or quote epoch mismatch for ' + symbol)
        value = cap.get('marketCap')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise CaptureError('missing or invalid provider market cap for ' + symbol)
        quote['marketCap'] = value
        quote['market_cap_source_sha256'] = cap['source_sha256']
        quote['market_cap_source_endpoint'] = 'quoteSummary.price'


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class _Constituents(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.in_cell = False
        self.rows = []
        self.row = []
        self.cell = ''

    def handle_starttag(self, tag, attrs):
        if tag == 'table' and dict(attrs).get('id') == 'constituents':
            self.active = True
        if not self.active:
            return
        if tag == 'tr':
            self.row = []
        if tag in ('th', 'td'):
            self.in_cell, self.cell = True, ''

    def handle_data(self, data):
        if self.active and self.in_cell:
            self.cell += data

    def handle_endtag(self, tag):
        if not self.active:
            return
        if tag in ('td', 'th'):
            self.row.append(self.cell.strip())
            self.in_cell = False
        if tag == 'tr' and self.row:
            self.rows.append(self.row)
        if tag == 'table':
            self.active = False


def parse_membership(html, *, min_listings=490, max_listings=510):
    parser = _Constituents()
    parser.feed(html)
    if not parser.rows or 'Symbol' not in parser.rows[0] or 'CIK' not in parser.rows[0]:
        raise CaptureError('constituents Symbol/CIK table missing')
    header = parser.rows[0]
    si, ci = header.index('Symbol'), header.index('CIK')
    rows = []
    for cells in parser.rows[1:]:
        if len(cells) != len(header):
            raise CaptureError('membership row width mismatch')
        symbol, cik = cells[si], cells[ci]
        if not re.fullmatch(r'[A-Z][A-Z0-9.\-]*', symbol) or not re.fullmatch(r'\d{1,10}', cik) or int(cik) <= 0:
            raise CaptureError('invalid membership identity')
        rows.append({'symbol': symbol, 'issuer_id': str(int(cik)).zfill(10)})
    if not min_listings <= len(rows) <= max_listings or len({r['symbol'] for r in rows}) != len(rows):
        raise CaptureError('membership coverage or duplicate listing')
    return rows


def group_issuers(members):
    identities = {row['symbol']: row['issuer_id'] for row in members}
    if len(identities) != len(members):
        raise CaptureError('duplicate listing identity')
    for family, _ in PREFERENCES:
        if len({identities[s] for s in family if s in identities}) > 1:
            raise CaptureError('known share classes disagree on issuer CIK')
    groups = {}
    for row in members:
        groups.setdefault(row['issuer_id'], []).append(row['symbol'])
    issuers = []
    for issuer_id, symbols in sorted(groups.items()):
        choices = set(symbols)
        preferred = next((preferred for family, preferred in PREFERENCES if choices <= family and preferred in choices), None)
        if len(choices) > 1 and preferred is None:
            raise CaptureError('unreviewed multiple-class issuer')
        execution_symbol = preferred or symbols[0]
        issuers.append({'issuer_id': issuer_id, 'execution_symbol': execution_symbol,
                        'yahoo_symbol': execution_symbol.replace('.', '-'), 'listing_symbols': sorted(symbols)})
    if not 11 <= len(issuers) <= MAX_ISSUERS:
        raise CaptureError('issuer coverage outside bounds')
    return issuers


def eligible_issuers(issuers, execution_session, *, policy=None):
    policy = owner_exclusion_policy() if policy is None else policy
    active = dt.date.fromisoformat(execution_session) >= dt.date.fromisoformat(policy['effective_date'])
    excluded_symbols = set(policy['symbols']) if active else set()
    excluded_ids = set(policy['issuer_ids']) if active else set()
    eligible, excluded = [], []
    for issuer in issuers:
        symbols = set(issuer.get('listing_symbols') or []) | {issuer['execution_symbol'], issuer['yahoo_symbol']}
        if symbols & excluded_symbols or issuer['issuer_id'] in excluded_ids:
            excluded.append({**issuer, 'reason': 'OWNER_EXCLUDED', 'policy_id': policy['policy_id']})
        else:
            eligible.append(issuer)
    return eligible, {'policy': policy, 'policy_hash': content_hash(policy), 'policy_effective': active,
                      'membership_issuer_count': len(issuers), 'eligible_issuer_count': len(eligible),
                      'excluded_issuers': excluded}


def validate_clock(previous_session, execution_session, now):
    if now.tzinfo is None:
        raise CaptureError('capture clock requires timezone')
    previous, execution = dt.date.fromisoformat(previous_session), dt.date.fromisoformat(execution_session)
    if not is_xnys_session(previous_session) or next_xnys_session(previous_session) != execution_session:
        raise CaptureError('sessions must be adjacent governed XNYS sessions')
    # Conservative 16:00 bound also admits the next morning after early closes.
    close = dt.datetime.combine(previous, dt.time(16), ET)
    opening = dt.datetime.combine(execution, dt.time(9, 30), ET)
    if not close <= now < opening:
        raise CaptureError('capture must follow completed close and precede execution open')


def normalize_panel(issuers, quotes, *, previous_session, execution_session, captured_at, sources, policy=None):
    validate_clock(previous_session, execution_session, captured_at)
    issuers, eligibility = eligible_issuers(issuers, execution_session, policy=policy)
    expected = {r['yahoo_symbol'] for r in issuers}
    if len(expected) != len(issuers) or set(quotes) != expected or len(issuers) < 11:
        raise CaptureError('full issuer panel coverage required')
    normalized = []
    for issuer in issuers:
        quote = quotes[issuer['yahoo_symbol']]
        if quote.get('symbol') != issuer['yahoo_symbol'] or quote.get('currency') != 'USD':
            raise CaptureError('quote symbol or currency mismatch')
        for key in ('marketCap', 'regularMarketPrice', 'regularMarketTime'):
            value = quote.get(key)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                raise CaptureError('missing or invalid quote field: ' + key)
        timestamp = dt.datetime.fromtimestamp(quote['regularMarketTime'], dt.timezone.utc)
        if timestamp > captured_at or timestamp.astimezone(ET).date().isoformat() != previous_session:
            raise CaptureError('stale or future quote timestamp')
        if not re.fullmatch('[0-9a-f]{64}', str(quote.get('source_sha256', ''))):
            raise CaptureError('missing raw quote lineage')
        cap_source = quote.get('market_cap_source_sha256', quote['source_sha256'])
        if not re.fullmatch('[0-9a-f]{64}', str(cap_source)):
            raise CaptureError('missing market cap field lineage')
        normalized.append({**issuer, 'market_cap': quote['marketCap'],
                           'market_cap_source_sha256': cap_source,
                           'market_cap_source_endpoint': quote.get('market_cap_source_endpoint', 'v7.quote'),
                           'regularMarketPrice': quote['regularMarketPrice'],
                           'regularMarketTime': quote['regularMarketTime'],
                           'source_sha256': quote['source_sha256']})
    normalized.sort(key=lambda row: (-row['market_cap'], row['issuer_id']))
    result = {'accepted': True, 'schema_version': 'caerus.aquila_ranking.v1',
              'formation_id': 'aquila-' + previous_session + '-' + content_hash(normalized)[:16],
              'formation_session': previous_session, 'execution_session': execution_session,
              'captured_at': captured_at.isoformat(), 'issuers': normalized,
              'universe_eligibility': eligibility,
              'rank_cutoff': {'10': normalized[9]['issuer_id'], '11': normalized[10]['issuer_id']},
              'sources': sources, 'readiness': 'TIER_B', 'classification': 'PROSPECTIVE_YAHOO_COMPANY_CAP_PROXY'}
    result['content_hash'] = content_hash(result)
    return result


class CaptureStore:
    def __init__(self, root, *, max_bytes=MAX_BYTES):
        if not 0 < max_bytes <= MAX_BYTES:
            raise CaptureError('invalid retained byte limit')
        self.max_bytes = max_bytes
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=False)
        self.lock = threading.Lock()
        self.retained_bytes = 0
        self.response_bytes = 0
        self.refs = []

    def write(self, name, value, *, compress=False):
        raw = _bytes(value)
        payload = gzip.compress(raw, mtime=0) if compress else raw
        with self.lock:
            limit = self.max_bytes if name in ('failure.json', 'probe.json') else self.max_bytes - min(1024 * 1024, self.max_bytes // 4)
            if self.retained_bytes + len(payload) > limit:
                raise CaptureError('retained source byte ceiling')
            path = (self.root / name).resolve()
            with path.open('xb') as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            path.chmod(0o444)
            ref = {'path': str(path), 'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)}
            self.retained_bytes += len(payload)
            self.refs.append(ref)
            return ref


class RequestBudget:
    def __init__(self, *, max_http=MAX_HTTP, max_seconds=MAX_SECONDS):
        if not 0 < max_http <= MAX_HTTP or not 0 < max_seconds <= MAX_SECONDS:
            raise CaptureError('invalid request budget')
        self.max_http = max_http
        self.lock = threading.Lock()
        self.deadline = time.monotonic() + max_seconds
        self.next_dispatch = 0.0
        self.count = 0
        self.stopped = False

    def reserve(self):
        with self.lock:
            now = time.monotonic()
            delay = max(0, self.next_dispatch - now)
            if self.stopped or self.count >= self.max_http or now + delay >= self.deadline:
                self.stopped = True
                raise CaptureError('HTTP count or time ceiling')
            self.count += 1
            self.next_dispatch = now + delay + 1 / REQUESTS_PER_SECOND
            if delay:
                time.sleep(delay)
            return self.count


def recording_session(store, budget):
    """Create a real curl Session subclass accepted by the public yfinance API."""
    from curl_cffi import requests

    class RecordingCurlSession(requests.Session):
        def __init__(self):
            super().__init__(impersonate='chrome')
            self.quotes = {}
            self.cap_quotes = {}
            self.quote_lock = threading.Lock()

        def request(self, method, url, *args, **kwargs):
            number = budget.reserve()
            kwargs['timeout'] = min(20, max(0.01, budget.deadline - time.monotonic()))
            # Disable redirects: curl redirects would otherwise evade request accounting.
            kwargs['allow_redirects'] = False
            response = super().request(method, url, *args, **kwargs)
            parsed = urlsplit(url)
            observed = dt.datetime.now(dt.timezone.utc).isoformat()
            safe = {'method': str(method).upper(), 'host': parsed.hostname, 'path': parsed.path,
                    'status': response.status_code, 'captured_at': observed,
                    'response_bytes': len(response.content), 'credentials_persisted': False}
            params = kwargs.get('params') or {}
            symbol = params.get('symbols') if isinstance(params, dict) else None
            if isinstance(symbol, str) and re.fullmatch(r'[A-Z][A-Z0-9.\-]*', symbol):
                safe['symbol'] = symbol
            store.write(f'http-{number:04d}.json.gz', safe, compress=True)
            with store.lock:
                store.response_bytes += len(response.content)
                if store.response_bytes > store.max_bytes:
                    budget.stopped = True
                    raise CaptureError('cumulative HTTP response byte ceiling')
            if (parsed.path == '/v7/finance/quote' or parsed.path.startswith('/v10/finance/quoteSummary/')) and response.status_code in (502, 503, 504):
                raise TransientQuoteError('transient Yahoo quote HTTP ' + str(response.status_code))
            # Cookie, crumb, headers and arbitrary provider text are never retained.
            # Preserve only the raw numeric source fields needed for normalization.
            if parsed.path == '/v7/finance/quote' and response.status_code == 200:
                payload = response.json()
                result = payload.get('quoteResponse', {}).get('result', [])
                if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
                    raise CaptureError('expected exactly one raw quote')
                if symbol is not None and result[0].get('symbol') != symbol:
                    raise CaptureError('raw quote does not match requested issuer')
                allowed = ('symbol', 'quoteType', 'currency', 'marketCap', 'regularMarketPrice', 'regularMarketTime', 'marketState')
                rows = [{key: row[key] for key in allowed if key in row} for row in result]
                safe['quoteResponse'] = {'result': rows}
                ref = store.write(f'quote-{number:04d}.json.gz', safe, compress=True)
                with self.quote_lock:
                    for row in rows:
                        self.quotes[row.get('symbol')] = {**row, 'source_sha256': ref['sha256']}
            elif parsed.path.startswith('/v10/finance/quoteSummary/') and response.status_code == 200:
                results = response.json().get('quoteSummary', {}).get('result') or []
                if len(results) != 1 or not isinstance(results[0].get('price'), dict):
                    raise CaptureError('missing provider price module')
                price = results[0]['price']
                fields = ('symbol', 'currency', 'marketCap', 'regularMarketTime', 'regularMarketPrice')
                raw = {key: price[key].get('raw') if isinstance(price[key], dict) else price[key]
                       for key in fields if key in price}
                safe['price'] = raw
                ref = store.write(f'cap-{number:04d}.json.gz', safe, compress=True)
                with self.quote_lock:
                    self.cap_quotes[raw.get('symbol')] = {**raw, 'source_sha256': ref['sha256']}
            elif parsed.hostname == 'en.wikipedia.org' and response.status_code == 200:
                safe['html'] = response.text
                store.write(f'membership-{number:04d}.json.gz', safe, compress=True)
            if response.status_code == 429 or time.monotonic() >= budget.deadline:
                budget.stopped = True
                raise CaptureError('provider rate limit or runtime deadline')
            return response

    return RecordingCurlSession()


@contextmanager
def memory_cookie_cache():
    # yfinance's cookie cache is process-global. Run this collector in a dedicated
    # process; suppress disk credential caching without changing its request API.
    from yfinance import cache as yf_cache
    original_cookie_cache = yf_cache.get_cookie_cache
    class MemoryCookieCache:
        def __init__(self):
            self.values = {}
        def lookup(self, key):
            return self.values.get(key)
        def store(self, key, value):
            self.values[key] = {'cookie': value, 'age': dt.timedelta(0)}
    memory_cache = MemoryCookieCache()
    yf_cache.get_cookie_cache = lambda: memory_cache
    try:
        yield
    finally:
        yf_cache.get_cookie_cache = original_cookie_cache


def capture_ranking(*, output_root, previous_session, execution_session, now=None):
    """Perform one bounded capture, returning accepted JSON path; failures retain sources.

    output_root is an existing approved rankings directory. A unique child is
    exclusively created; <previous_session>.json is published last at output_root. No application retries.
    """
    import yfinance as yf
    now = now or dt.datetime.now(dt.timezone.utc)
    validate_clock(previous_session, execution_session, now)
    actual_now = dt.datetime.now(dt.timezone.utc)
    if abs((now - actual_now).total_seconds()) > 60:
        raise CaptureError('capture cannot backdate its observation clock')
    if (Path(output_root) / (previous_session + '.json')).exists():
        raise CaptureError('canonical ranking already exists; overwrite refused')
    root = Path(output_root) / actual_now.strftime('%Y%m%dT%H%M%S.%fZ')
    store, budget = CaptureStore(root), RequestBudget()
    session = None
    cookie_context = memory_cookie_cache()
    cookie_context.__enter__()
    try:
        session = recording_session(store, budget)
        client = quote_client(session)
        response = session.get(MEMBERSHIP_URL)
        if response.status_code != 200:
            raise CaptureError('membership HTTP failure')
        membership_issuers = group_issuers(parse_membership(response.text))
        policy = owner_exclusion_policy()
        issuers, eligibility = eligible_issuers(membership_issuers, execution_session, policy=policy)
        def collect(issuer):
            if budget.stopped:
                raise CaptureError('collection stopped')
            # Direct v7 capture avoids unrelated company-info parsing failures.
            try:
                collect_quote(issuer['yahoo_symbol'], session, budget, client=client)
            except Exception:
                budget.stopped = True
                raise
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            list(pool.map(collect, issuers))
        captured = dt.datetime.now(dt.timezone.utc)
        if time.monotonic() >= budget.deadline:
            raise CaptureError('runtime deadline')
        panel = normalize_panel(membership_issuers, session.quotes, previous_session=previous_session,
                                execution_session=execution_session, captured_at=captured, sources=list(store.refs), policy=policy)
        if content_hash(owner_exclusion_policy()) != content_hash(policy):
            raise CaptureError("owner exclusion policy changed during capture")
        receipt = store.write('receipt.json', {'accepted': True, 'http_requests': budget.count,
                              'logical_issuer_calls': len(issuers), 'sources': list(store.refs),
                              'credentials_persisted': False, 'yfinance_version': yf.__version__})
        panel['receipt'] = receipt
        panel['content_hash'] = content_hash({k: v for k, v in panel.items() if k != 'content_hash'})
        return Path(store.write('../' + previous_session + '.json', panel)['path'])
    except Exception as exc:
        budget.stopped = True
        # Do not persist provider exception messages: they may contain crumb-bearing URLs.
        store.write('failure.json', {'accepted': False, **safe_failure(exc),
                    'http_requests': budget.count, 'sources': list(store.refs), 'credentials_persisted': False})
        raise CaptureError('capture failed; immutable failure receipt: ' + str(root)) from None
    finally:
        cookie_context.__exit__(None, None, None)
        if session is not None:
            session.close()


def probe_source(*, output_root, symbol="AAPL"):
    """One membership request plus one raw quote; never creates a formation."""
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]*", symbol):
        raise CaptureError("invalid probe symbol")
    import yfinance as yf
    observed = dt.datetime.now(dt.timezone.utc)
    root = Path(output_root) / ('probe-' + observed.strftime('%Y%m%dT%H%M%S.%fZ'))
    store = CaptureStore(root, max_bytes=4 * 1024 * 1024)
    budget = RequestBudget(max_http=12, max_seconds=60)
    session = None
    with memory_cookie_cache():
        try:
            session = recording_session(store, budget)
            client = quote_client(session)
            response = session.get(MEMBERSHIP_URL)
            if response.status_code != 200:
                raise CaptureError('membership HTTP failure')
            members = parse_membership(response.text)
            if not any(row['symbol'].replace('.', '-') == symbol for row in members):
                raise CaptureError('probe symbol absent from membership')
            collect_quote(symbol, session, budget, client=client)
            quote = session.quotes.get(symbol, {})
            if quote.get('symbol') != symbol or quote.get('currency') != 'USD':
                raise CaptureError('probe quote identity or currency missing')
            for key in ('marketCap', 'regularMarketPrice', 'regularMarketTime'):
                value = quote.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                    raise CaptureError('missing or invalid probe quote field: ' + key)
            captured = dt.datetime.now(dt.timezone.utc)
            if quote['regularMarketTime'] > captured.timestamp() or time.monotonic() >= budget.deadline:
                raise CaptureError('future quote epoch or probe deadline')
            if not re.fullmatch('[0-9a-f]{64}', str(quote.get('source_sha256', ''))):
                raise CaptureError('missing probe raw quote lineage')
            payload = {'accepted': False, 'classification': 'SOURCE_ONLY_NOT_FORMATION',
                       'status': 'PASS', 'captured_at': captured.isoformat(), 'symbol': symbol,
                       'quote': quote, 'membership_listings': len(members), 'sources': list(store.refs),
                       'http_requests': budget.count, 'logical_issuer_calls': 1,
                       'limits': {'http': 12, 'seconds': 60, 'bytes': 4 * 1024 * 1024},
                       'credentials_persisted': False, 'yfinance_version': yf.__version__}
            return Path(store.write('probe.json', payload)['path'])
        except Exception as exc:
            budget.stopped = True
            store.write('probe.json', {'accepted': False, 'classification': 'SOURCE_ONLY_NOT_FORMATION',
                        'status': 'FAIL', **safe_failure(exc),
                        'http_requests': budget.count, 'sources': list(store.refs), 'credentials_persisted': False})
            raise CaptureError('source probe failed; immutable receipt: ' + str(root)) from None
        finally:
            if session is not None:
                session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', required=True)
    parser.add_argument('--probe-only', action='store_true')
    parser.add_argument('--probe-symbol', default='AAPL')
    parser.add_argument('--previous-session')
    parser.add_argument('--execution-session')
    args = parser.parse_args()
    if args.probe_only:
        if args.previous_session or args.execution_session:
            parser.error('source-only probe cannot specify formation sessions')
        result = probe_source(output_root=args.output_root, symbol=args.probe_symbol)
        print(json.dumps({'probe_path': str(result), 'accepted': False}))
        return
    if not args.previous_session or not args.execution_session:
        parser.error('formation capture requires both sessions')
    result = capture_ranking(output_root=args.output_root,
                             previous_session=args.previous_session,
                             execution_session=args.execution_session)
    print(json.dumps({'accepted_path': str(result)}))


if __name__ == '__main__':
    main()
