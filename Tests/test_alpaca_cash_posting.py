"""Offline source-shaped cash posting contract regressions; no network."""
from copy import deepcopy
from decimal import Decimal
import pytest
from core.alpaca_cash_posting import certify_cash_posting, read_account_activities
from core.economic_reconciliation import Fill, MarkedPosition, reconcile_economic_truth

P2_ORDERS = [('order-0', 'AAPL', 'SELL', '0.018199', '335.482'), ('order-1', 'AMZN', 'SELL', '0.004104', '255.786'), ('order-2', 'LRCX', 'SELL', '0.006536', '299.566'), ('order-3', 'AVGO', 'BUY', '1.460154', '364.24'), ('order-4', 'BRK.B', 'BUY', '1.049703', '506.43'), ('order-5', 'GOOGL', 'BUY', '1.556234', '341.51'), ('order-6', 'META', 'BUY', '0.815411', '652.034'), ('order-7', 'MSFT', 'BUY', '1.075459', '494.22'), ('order-8', 'NVDA', 'BUY', '2.414679', '220.2'), ('order-9', 'STX', 'BUY', '0.038375', '821.71'), ('order-10', 'TSLA', 'BUY', '1.453866', '365.49'), ('order-11', 'WDC', 'BUY', '0.035062', '447.788')]
P2_LEGS = [('order-0', '0.018199', '335.482'), ('order-1', '0.004104', '255.786'), ('order-2', '0.006536', '299.566'), ('order-3', '1', '364.24'), ('order-3', '0.460154', '364.24'), ('order-4', '1', '506.43'), ('order-4', '0.049703', '506.43'), ('order-5', '1', '341.51'), ('order-5', '0.556234', '341.51'), ('order-6', '0.815411', '652.034'), ('order-7', '1', '494.22'), ('order-7', '0.075459', '494.22'), ('order-8', '2', '220.2'), ('order-8', '0.414679', '220.2'), ('order-9', '0.038375', '821.71'), ('order-10', '1', '365.49'), ('order-10', '0.453866', '365.49'), ('order-11', '0.035062', '447.788')]


def fixture():
    orders = [dict(id=oid, client_order_id='client-'+oid, symbol=symbol, side=side,
                   qty=qty, filled_qty=qty, filled_avg_price=price, status='filled')
              for oid, symbol, side, qty, price in P2_ORDERS]
    by_id = {r['id']: r for r in orders}
    activities = [dict(id=f'activity-{i}', activity_type='FILL', order_id=oid,
                       symbol=by_id[oid]['symbol'], side=by_id[oid]['side'], qty=qty,
                       price=price, transaction_time='2026-09-11T15:09:00Z')
                  for i, (oid, qty, price) in enumerate(P2_LEGS)]
    return dict(orders=orders, activities=activities, starting_cash='4302.52',
                ending_cash='543.22', boundary='2026-09-11T15:08:01Z', trade_date='2026-09-11',
                observed_at='2026-09-11T15:10:00Z')


def test_actual_p2_18_legs_12_orders_and_raw_preserved():
    args = fixture()
    before = deepcopy(args)
    evidence = certify_cash_posting(**args)
    assert args == before
    assert len(evidence['activities']) == 18
    assert len(evidence['orders']) == 12
    assert evidence['per_fill_signed_consideration'] == '-3759.30'
    assert evidence['per_order_signed_consideration'] == '-3759.30'
    assert evidence['raw_signed_activity_consideration'] == '-3759.312217752'
    assert len(evidence['content_hash']) == 64
    fills = [Fill(symbol=r['symbol'], side=r['side'], quantity=float(r['qty']),
                  price=float(r['filled_avg_price']), order_id=r['id']) for r in args['orders']]
    starting = {f.symbol: f.quantity for f in fills if f.side == 'SELL'}
    ending = [MarkedPosition(f.symbol, f.quantity, f.price) for f in fills if f.side == 'BUY']
    value = sum(p.quantity*p.mark for p in ending)
    kwargs = dict(trade_date=args['trade_date'], starting_cash=4302.52,
                  ending_cash=543.22, starting_positions=starting, fills=fills,
                  ending_positions=ending, broker_equity=543.22+value)
    raw = reconcile_economic_truth(**kwargs)
    posted = reconcile_economic_truth(**kwargs, cash_posting_evidence=evidence)
    assert 'CASH_FROM_FILLS_MISMATCH' in raw.reason_codes
    assert posted.reconciled
    assert posted.cash_delta == 0
    assert posted.tolerance.cash_abs == .01
    assert posted.fill_notional_buys == raw.fill_notional_buys
    assert posted.fill_notional_sells == raw.fill_notional_sells
    assert posted.to_dict()['cash']['posting_evidence'] == evidence
    tampered = deepcopy(evidence)
    tampered['ending_cash'] = '543.23'
    with pytest.raises(ValueError, match='hash'):
        reconcile_economic_truth(**kwargs, cash_posting_evidence=tampered)


@pytest.mark.parametrize('price,posted', [('1.005', '1.00'), ('1.015', '1.02')])
def test_half_even_ties(price, posted):
    args = fixture()
    args['orders'] = [dict(id='o', client_order_id='c', symbol='X', side='BUY',
                          qty='1', filled_qty='1', filled_avg_price=price, status='filled')]
    args['activities'] = [dict(id='a', activity_type='FILL', order_id='o', symbol='X',
                              side='buy', qty='1', price=price, transaction_time='2026-09-11T15:09:00Z')]
    args.update(starting_cash='10.00', ending_cash=str(Decimal('10')-Decimal(posted)))
    assert certify_cash_posting(**args)['orders'][0]['posted_consideration'] == posted


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'conflicting', 'extra', 'nonfill',
                                     'wrongqty', 'wrongprice', 'partial', 'cash', 'before', 'fee'])
def test_incomplete_or_unexplained_evidence_rejected(mutation):
    args = fixture()
    if mutation == 'missing': args['activities'].pop()
    elif mutation == 'duplicate': args['activities'].append(dict(args['activities'][0]))
    elif mutation == 'conflicting': args['activities'][0]['symbol'] = 'WRONG'
    elif mutation == 'extra': args['activities'][0]['order_id'] = 'unknown'
    elif mutation == 'nonfill': args['activities'].append({'id':'fee', 'activity_type':'FEE'})
    elif mutation == 'wrongqty': args['activities'][0]['qty'] = '0.9'
    elif mutation == 'wrongprice': args['activities'][0]['price'] = '1'
    elif mutation == 'partial': args['orders'][0]['status'] = 'partially_filled'
    elif mutation == 'cash': args['ending_cash'] = '543.23'
    elif mutation == 'before': args['activities'][0]['transaction_time'] = '2026-09-11T15:00:00Z'
    elif mutation == 'fee': args['activities'][0]['fee_amount'] = '.01'
    with pytest.raises(ValueError): certify_cash_posting(**args)


def test_granularity_divergence_rejected_even_if_one_matches_cash():
    args = fixture()
    args['orders'] = [dict(id='o', client_order_id='c', symbol='X', side='BUY',
                          qty='2', filled_qty='2', filled_avg_price='1.005', status='filled')]
    args['activities'] = [dict(id=str(i), activity_type='FILL', order_id='o', symbol='X',
                              side='buy', qty='1', price='1.005', transaction_time='2026-09-11T15:09:00Z') for i in range(2)]
    args.update(starting_cash='10.00', ending_cash='8.00')
    with pytest.raises(ValueError, match='granularity'): certify_cash_posting(**args)


class Client:
    def __init__(self, pages): self.pages, self.calls = iter(pages), []
    def get(self, path, data):
        self.calls.append((path, data))
        return next(self.pages)


def test_all_activity_pagination_complete_and_bounded():
    client = Client([[{'id':'1'}, {'id':'2'}], [{'id':'3'}]])
    rows = read_account_activities(client, trade_date='2026-09-11', page_size=2)
    assert len(rows) == 3
    assert client.calls[0][0] == '/account/activities'
    assert client.calls[1][1]['page_token'] == '2'
    with pytest.raises(ValueError, match='exhausted'):
        read_account_activities(Client([[{'id':'1'}]]), trade_date='2026-09-11', page_size=1, max_pages=1)


@pytest.mark.parametrize('pages', [[{}], [[{'id':'1'}, {'id':'1'}]], [[{}]], [[{'id':'1'}], [{'id':'1'}]]])
def test_pagination_malformed_duplicate_or_loop_rejected(pages):
    with pytest.raises(ValueError):
        read_account_activities(Client(pages), trade_date='2026-09-11', page_size=1)


def test_real_alpaca_paper_materializer_helper_and_live_unchanged():
    from brokers.alpaca_broker import AlpacaBroker
    from scripts.live_pilot_execute import _paper_cash_posting_evidence
    args = fixture()
    activities = args.pop('activities')
    broker = object.__new__(AlpacaBroker)
    broker.paper = True
    broker.trading_client = Client([activities])
    evidence = _paper_cash_posting_evidence(broker=broker, **args)
    assert evidence['ending_cash'] == '543.22'
    assert broker.trading_client.calls[0][0] == '/account/activities'
    broker.paper = False
    assert _paper_cash_posting_evidence(broker=broker, **args) is None
    assert len(broker.trading_client.calls) == 1


def test_future_activity_and_rehashed_trace_tampering_rejected():
    import hashlib
    import json
    from core.alpaca_cash_posting import validated_posted_cash
    args = fixture()
    args['activities'][0]['transaction_time'] = '2026-09-11T15:11:00Z'
    with pytest.raises(ValueError, match='after broker snapshot'):
        certify_cash_posting(**args)
    args = fixture()
    original = certify_cash_posting(**args)
    fills = [Fill(symbol=r['symbol'], side=r['side'], quantity=float(r['qty']),
                  price=float(r['filled_avg_price']), order_id=r['id']) for r in args['orders']]
    for change in ('consideration', 'endpoint', 'policy', 'duplicatefill'):
        evidence = deepcopy(original)
        local_fills = list(fills)
        if change == 'consideration': evidence['orders'][0]['posted_consideration'] = '999'
        if change == 'endpoint': evidence['endpoint'] = '/fake'
        if change == 'policy': evidence['non_fill_activity_policy'] = 'ignore'
        if change == 'duplicatefill': local_fills[-1] = local_fills[0]
        evidence.pop('content_hash')
        evidence['content_hash'] = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        with pytest.raises(ValueError):
            validated_posted_cash(evidence, fills=local_fills, trade_date=args['trade_date'],
                                  starting_cash=args['starting_cash'], ending_cash=args['ending_cash'])


def test_snapshot_fractional_precision_accepts_prior_fill_in_same_second(monkeypatch):
    import datetime
    import types
    from scripts import live_pilot_execute as execution
    instants = iter([datetime.datetime(2026, 9, 11, 15, 9, 0, 600000, tzinfo=datetime.timezone.utc),
                     datetime.datetime(2026, 9, 11, 15, 9, 0, 900000, tzinfo=datetime.timezone.utc)])
    class Clock:
        @classmethod
        def now(cls, tz): return next(instants)
    monkeypatch.setattr(execution, 'dt', types.SimpleNamespace(datetime=Clock, timezone=datetime.timezone))
    broker = types.SimpleNamespace(get_account=lambda: {}, get_positions=lambda: [], list_orders=lambda **kwargs: [])
    snapshot = execution._broker_snapshot(broker)
    assert snapshot['capture_started_at'].endswith('.600000+00:00')
    assert snapshot['captured_at'] == snapshot['capture_completed_at'] == '2026-09-11T15:09:00.900000+00:00'
    args = fixture()
    for row in args['activities']:
        row['transaction_time'] = '2026-09-11T15:09:00.800000Z'
    args['observed_at'] = snapshot['captured_at']
    assert certify_cash_posting(**args)['ending_cash'] == '543.22'
    args['activities'][0]['transaction_time'] = '2026-09-11T15:09:00.950000Z'
    with pytest.raises(ValueError, match='after broker snapshot'):
        certify_cash_posting(**args)
