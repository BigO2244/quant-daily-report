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


# All 33 retained broker activities: identifiers anonymized, raw timestamps preserved.
ACTUAL_DAY_ACTIVITIES = [{'activity_type': 'FILL', 'cum_qty': '1.327061', 'id': 'activity-0', 'leaves_qty': '0', 'order_id': 'prior-12', 'price': '102.63', 'qty': '1', 'side': 'sell', 'symbol': 'INTC', 'transaction_time': '2026-09-11T13:35:28.340483Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '0.327061', 'id': 'activity-1', 'leaves_qty': '1', 'order_id': 'prior-12', 'price': '102.63', 'qty': '0.327061', 'side': 'sell', 'symbol': 'INTC', 'transaction_time': '2026-09-11T13:35:28.342105Z', 'type': 'partial_fill'}, {'activity_type': 'FILL', 'cum_qty': '0.193074', 'id': 'activity-2', 'leaves_qty': '0', 'order_id': 'prior-13', 'price': '301.136', 'qty': '0.193074', 'side': 'sell', 'symbol': 'LRCX', 'transaction_time': '2026-09-11T13:35:29.881121Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '0.560744', 'id': 'activity-3', 'leaves_qty': '0', 'order_id': 'prior-14', 'price': '987.514', 'qty': '0.560744', 'side': 'sell', 'symbol': 'MU', 'transaction_time': '2026-09-11T13:35:30.102289Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '1.267882', 'id': 'activity-4', 'leaves_qty': '0', 'order_id': 'prior-15', 'price': '853.01', 'qty': '1', 'side': 'sell', 'symbol': 'STX', 'transaction_time': '2026-09-11T13:35:30.988503Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '0.267882', 'id': 'activity-5', 'leaves_qty': '1', 'order_id': 'prior-15', 'price': '853.01', 'qty': '0.267882', 'side': 'sell', 'symbol': 'STX', 'transaction_time': '2026-09-11T13:35:30.989883Z', 'type': 'partial_fill'}, {'activity_type': 'FILL', 'cum_qty': '1', 'id': 'activity-6', 'leaves_qty': '1.2555', 'order_id': 'prior-16', 'price': '460', 'qty': '1', 'side': 'sell', 'symbol': 'WDC', 'transaction_time': '2026-09-11T13:35:32.78476Z', 'type': 'partial_fill'}, {'activity_type': 'FILL', 'cum_qty': '2.2555', 'id': 'activity-7', 'leaves_qty': '0', 'order_id': 'prior-16', 'price': '460', 'qty': '1', 'side': 'sell', 'symbol': 'WDC', 'transaction_time': '2026-09-11T13:35:34.047938Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '1.2555', 'id': 'activity-8', 'leaves_qty': '1', 'order_id': 'prior-16', 'price': '460', 'qty': '0.2555', 'side': 'sell', 'symbol': 'WDC', 'transaction_time': '2026-09-11T13:35:34.049494Z', 'type': 'partial_fill'}, {'activity_type': 'FILL', 'cum_qty': '0.140728', 'id': 'activity-9', 'leaves_qty': '0', 'order_id': 'prior-17', 'price': '103.694', 'qty': '0.140728', 'side': 'sell', 'symbol': 'INTC', 'transaction_time': '2026-09-11T14:23:01.954243Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '1.602608', 'id': 'activity-10', 'leaves_qty': '0', 'order_id': 'prior-18', 'price': '333.23', 'qty': '1', 'side': 'buy', 'symbol': 'AAPL', 'transaction_time': '2026-09-11T14:23:03.283113Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '0.602608', 'id': 'activity-11', 'leaves_qty': '1', 'order_id': 'prior-18', 'price': '333.23', 'qty': '0.602608', 'side': 'buy', 'symbol': 'AAPL', 'transaction_time': '2026-09-11T14:23:03.284644Z', 'type': 'partial_fill'}, {'activity_type': 'FILL', 'cum_qty': '1', 'id': 'activity-12', 'leaves_qty': '1.08181', 'order_id': 'prior-19', 'price': '256.56', 'qty': '1', 'side': 'buy', 'symbol': 'AMZN', 'transaction_time': '2026-09-11T14:23:05.246015Z', 'type': 'partial_fill'}, {'activity_type': 'FILL', 'cum_qty': '2.08181', 'id': 'activity-13', 'leaves_qty': '0', 'order_id': 'prior-19', 'price': '256.55', 'qty': '1', 'side': 'buy', 'symbol': 'AMZN', 'transaction_time': '2026-09-11T14:23:05.940165Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '1.08181', 'id': 'activity-14', 'leaves_qty': '1', 'order_id': 'prior-19', 'price': '256.55', 'qty': '0.08181', 'side': 'buy', 'symbol': 'AMZN', 'transaction_time': '2026-09-11T14:23:05.94178Z', 'type': 'partial_fill'}, {'activity_type': 'FILL', 'cum_qty': '0.018199', 'id': 'activity-15', 'leaves_qty': '0', 'order_id': 'order-0', 'price': '335.482', 'qty': '0.018199', 'side': 'sell', 'symbol': 'AAPL', 'transaction_time': '2026-09-11T15:08:31.465978Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '0.004104', 'id': 'activity-16', 'leaves_qty': '0', 'order_id': 'order-1', 'price': '255.786', 'qty': '0.004104', 'side': 'sell', 'symbol': 'AMZN', 'transaction_time': '2026-09-11T15:08:31.653804Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '0.006536', 'id': 'activity-17', 'leaves_qty': '0', 'order_id': 'order-2', 'price': '299.566', 'qty': '0.006536', 'side': 'sell', 'symbol': 'LRCX', 'transaction_time': '2026-09-11T15:08:31.845388Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '1.460154', 'id': 'activity-18', 'leaves_qty': '0', 'order_id': 'order-3', 'price': '364.24', 'qty': '1', 'side': 'buy', 'symbol': 'AVGO', 'transaction_time': '2026-09-11T15:08:32.677551Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '0.460154', 'id': 'activity-19', 'leaves_qty': '1', 'order_id': 'order-3', 'price': '364.24', 'qty': '0.460154', 'side': 'buy', 'symbol': 'AVGO', 'transaction_time': '2026-09-11T15:08:32.679136Z', 'type': 'partial_fill'}, {'activity_type': 'FILL', 'cum_qty': '1.049703', 'id': 'activity-20', 'leaves_qty': '0', 'order_id': 'order-4', 'price': '506.43', 'qty': '1', 'side': 'buy', 'symbol': 'BRK.B', 'transaction_time': '2026-09-11T15:08:34.779543Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '0.049703', 'id': 'activity-21', 'leaves_qty': '1', 'order_id': 'order-4', 'price': '506.43', 'qty': '0.049703', 'side': 'buy', 'symbol': 'BRK.B', 'transaction_time': '2026-09-11T15:08:34.781173Z', 'type': 'partial_fill'}, {'activity_type': 'FILL', 'cum_qty': '1.556234', 'id': 'activity-22', 'leaves_qty': '0', 'order_id': 'order-5', 'price': '341.51', 'qty': '1', 'side': 'buy', 'symbol': 'GOOGL', 'transaction_time': '2026-09-11T15:08:37.479342Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '0.556234', 'id': 'activity-23', 'leaves_qty': '1', 'order_id': 'order-5', 'price': '341.51', 'qty': '0.556234', 'side': 'buy', 'symbol': 'GOOGL', 'transaction_time': '2026-09-11T15:08:37.48111Z', 'type': 'partial_fill'}, {'activity_type': 'FILL', 'cum_qty': '0.815411', 'id': 'activity-24', 'leaves_qty': '0', 'order_id': 'order-6', 'price': '652.034', 'qty': '0.815411', 'side': 'buy', 'symbol': 'META', 'transaction_time': '2026-09-11T15:08:38.714468Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '1.075459', 'id': 'activity-25', 'leaves_qty': '0', 'order_id': 'order-7', 'price': '494.22', 'qty': '1', 'side': 'buy', 'symbol': 'MSFT', 'transaction_time': '2026-09-11T15:08:39.420897Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '0.075459', 'id': 'activity-26', 'leaves_qty': '1', 'order_id': 'order-7', 'price': '494.22', 'qty': '0.075459', 'side': 'buy', 'symbol': 'MSFT', 'transaction_time': '2026-09-11T15:08:39.422426Z', 'type': 'partial_fill'}, {'activity_type': 'FILL', 'cum_qty': '2.414679', 'id': 'activity-27', 'leaves_qty': '0', 'order_id': 'order-8', 'price': '220.2', 'qty': '2', 'side': 'buy', 'symbol': 'NVDA', 'transaction_time': '2026-09-11T15:08:41.73577Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '0.414679', 'id': 'activity-28', 'leaves_qty': '2', 'order_id': 'order-8', 'price': '220.2', 'qty': '0.414679', 'side': 'buy', 'symbol': 'NVDA', 'transaction_time': '2026-09-11T15:08:41.737295Z', 'type': 'partial_fill'}, {'activity_type': 'FILL', 'cum_qty': '0.038375', 'id': 'activity-29', 'leaves_qty': '0', 'order_id': 'order-9', 'price': '821.71', 'qty': '0.038375', 'side': 'buy', 'symbol': 'STX', 'transaction_time': '2026-09-11T15:08:43.368305Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '1.453866', 'id': 'activity-30', 'leaves_qty': '0', 'order_id': 'order-10', 'price': '365.49', 'qty': '1', 'side': 'buy', 'symbol': 'TSLA', 'transaction_time': '2026-09-11T15:08:44.230697Z', 'type': 'fill'}, {'activity_type': 'FILL', 'cum_qty': '0.453866', 'id': 'activity-31', 'leaves_qty': '1', 'order_id': 'order-10', 'price': '365.49', 'qty': '0.453866', 'side': 'buy', 'symbol': 'TSLA', 'transaction_time': '2026-09-11T15:08:44.232312Z', 'type': 'partial_fill'}, {'activity_type': 'FILL', 'cum_qty': '0.035062', 'id': 'activity-32', 'leaves_qty': '0', 'order_id': 'order-11', 'price': '447.788', 'qty': '0.035062', 'side': 'buy', 'symbol': 'WDC', 'transaction_time': '2026-09-11T15:08:45.800292Z', 'type': 'fill'}]


@pytest.mark.parametrize('digits', range(1, 10))
@pytest.mark.parametrize('zone', ['Z', '+00:00', '-04:00'])
def test_rfc3339_every_broker_fraction_width_preserves_nanoseconds(digits, zone):
    from core.alpaca_cash_posting import _time
    hour = '11' if zone == '-04:00' else '15'
    fraction = '123456789'[:digits]
    parsed = _time(f'2026-09-11T{hour}:09:00.{fraction}{zone}')
    base = _time('2026-09-11T15:09:00Z')
    assert (parsed - base).value == int(fraction.ljust(9, '0'))


@pytest.mark.parametrize('value', ['2026-09-11T15:09:00', '2026-09-11',
                                   '2026-09-11 15:09:00Z', 'September 11 2026 UTC',
                                   '2026-09-11T15:09:00.1234567890Z',
                                   '2026-09-11T15:09:00+25:00',
                                   '2026-09-11T15:09:00-00:00',
                                   '2026-02-30T15:09:00Z'])
def test_timestamp_rejects_naive_or_non_rfc3339(value):
    from core.alpaca_cash_posting import _time
    with pytest.raises(ValueError): _time(value)


def test_future_nanosecond_is_not_truncated_into_observation():
    args = fixture()
    args['observed_at'] = '2026-09-11T15:10:00.123456788Z'
    args['activities'][0]['transaction_time'] = '2026-09-11T15:10:00.123456789Z'
    with pytest.raises(ValueError, match='after broker snapshot'):
        certify_cash_posting(**args)
    args['activities'][0]['transaction_time'] = '2026-09-11T15:10:00.123456787Z'
    assert certify_cash_posting(**args)['ending_cash'] == '543.22'


def test_actual_all_33_activity_replay_including_prior_five_fraction_timestamp():
    from brokers.alpaca_broker import AlpacaBroker
    from scripts.live_pilot_execute import _paper_cash_posting_evidence
    args = fixture()
    args.pop('activities')
    args['observed_at'] = '2026-09-11T15:13:20.658381+00:00'
    broker = object.__new__(AlpacaBroker)
    broker.paper = True
    broker.trading_client = Client([deepcopy(ACTUAL_DAY_ACTIVITIES)])
    assert any(r['transaction_time'] == '2026-09-11T13:35:32.78476Z' for r in ACTUAL_DAY_ACTIVITIES)
    evidence = _paper_cash_posting_evidence(broker=broker, **args)
    assert evidence['all_activity_count'] == 33
    assert len(evidence['activities']) == 18
    assert len(evidence['orders']) == 12
    assert evidence['ending_cash'] == '543.22'
    assert evidence['per_fill_signed_consideration'] == '-3759.30'
