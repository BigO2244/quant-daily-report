"""Evidence-bound PAPER cash certification; never chooses a rounding unit by fit."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import hashlib
import json
from collections.abc import Mapping, Sequence
from zoneinfo import ZoneInfo

CENT = Decimal('0.01')


def _decimal(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('invalid cash-posting decimal') from exc
    if not result.is_finite():
        raise ValueError('nonfinite cash-posting decimal')
    return result


def _time(value):
    result = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('activity boundary requires timezone')
    return result


def read_account_activities(client, *, trade_date, max_pages=10, page_size=100):
    """Read ALL activity types, with a finite complete pagination proof."""
    rows, seen, token = [], set(), None
    for _ in range(max_pages):
        query = {'date': trade_date, 'direction': 'asc', 'page_size': page_size}
        if token is not None:
            query['page_token'] = token
        page = client.get('/account/activities', data=query)
        if not isinstance(page, list) or len(page) > page_size:
            raise ValueError('malformed account activity page')
        for row in page:
            if not isinstance(row, Mapping) or not isinstance(row.get('id'), str) or not row['id']:
                raise ValueError('activity identity missing')
            if row['id'] in seen:
                raise ValueError('duplicate account activity')
            seen.add(row['id'])
            rows.append(dict(row))
        if len(page) < page_size:
            return rows
        token = page[-1]['id']
    raise ValueError('account activity pagination exhausted')


def certify_cash_posting(*, activities: Sequence[Mapping], orders: Sequence[Mapping],
                         starting_cash, ending_cash, boundary, trade_date, observed_at=None):
    """Require both candidate granularities AND exact cash identity to agree.

    Average-price compatibility uses half a unit at the terminal broker average's
    reported decimal precision. This represents serialization precision only;
    activity quantity, cent consideration and cash identities remain exact.
    """
    with localcontext() as ctx:
        ctx.prec = 50
        return _certify(activities=activities, orders=orders, starting_cash=starting_cash,
                        ending_cash=ending_cash, boundary=boundary, trade_date=trade_date,
                        observed_at=observed_at or dt.datetime.now(dt.timezone.utc).isoformat())


def _certify(*, activities, orders, starting_cash, ending_cash, boundary, trade_date, observed_at):
    start, end, cutoff = _decimal(starting_cash), _decimal(ending_cash), _time(boundary)
    observed = _time(observed_at)
    if cutoff > observed or cutoff.astimezone(ZoneInfo('America/New_York')).date().isoformat() != trade_date:
        raise ValueError('cash posting boundary invalid')
    if start != start.quantize(CENT) or end != end.quantize(CENT):
        raise ValueError('broker cash is not cent-valued')
    expected, client_ids = {}, set()
    for row in orders:
        oid = str(row.get('id') or row.get('order_id') or '')
        cid = str(row.get('client_order_id') or '')
        side = str(row.get('side') or '').lower()
        qty = _decimal(row.get('filled_qty') or row.get('filled_quantity'))
        avg = _decimal(row.get('filled_avg_price') or row.get('fill_price'))
        status = str(row.get('status') or '').lower().split('.')[-1]
        if (not oid or oid in expected or not cid or cid in client_ids or
                side not in {'buy', 'sell'} or status != 'filled' or qty <= 0 or avg <= 0 or
                not str(row.get('symbol') or '') or
                _decimal(row.get('qty') or row.get('quantity')) != qty):
            raise ValueError('terminal full order identity invalid')
        if _decimal(row.get('fees', 0)) != 0:
            raise ValueError('nonzero fill fee requires explicit posting contract')
        expected[oid] = (row, qty, avg)
        client_ids.add(cid)
    seen, groups, records = set(), {oid: [] for oid in expected}, []
    for row in activities:
        if not isinstance(row, Mapping):
            raise ValueError('malformed activity')
        aid = str(row.get('id') or '')
        if not aid or aid in seen:
            raise ValueError('duplicate or missing activity ID')
        seen.add(aid)
        if row.get('activity_type') != 'FILL':
            raise ValueError('independent cash activity present')
        timestamp = _time(row.get('transaction_time'))
        if timestamp.astimezone(ZoneInfo('America/New_York')).date().isoformat() != trade_date:
            raise ValueError('activity trade date mismatch')
        if timestamp > observed:
            raise ValueError('activity timestamp after broker snapshot')
        oid = str(row.get('order_id') or '')
        if oid not in expected:
            if timestamp >= cutoff:
                raise ValueError('unexplained extra order activity')
            continue
        if timestamp < cutoff:
            raise ValueError('expected order precedes starting-state boundary')
        original, _, _ = expected[oid]
        if row.get('symbol') != original.get('symbol') or str(row.get('side')).lower() != str(original.get('side')).lower():
            raise ValueError('activity order identity mismatch')
        qty, price = _decimal(row.get('qty')), _decimal(row.get('price'))
        if qty <= 0 or price <= 0:
            raise ValueError('activity economics invalid')
        if any(_decimal(row[key]) != 0 for key in ('fee', 'fee_amount') if row.get(key) is not None):
            raise ValueError('activity fee requires explicit posting contract')
        gross = qty * price
        record = {'activity_id': aid, 'order_id': oid, 'client_order_id': original['client_order_id'],
                  'symbol': row['symbol'], 'side': str(row['side']).upper(),
                  'transaction_time': str(row['transaction_time']), 'quantity': str(qty),
                  'price': str(price), 'raw_consideration': str(gross),
                  'cent_consideration': str(gross.quantize(CENT, rounding=ROUND_HALF_EVEN))}
        records.append(record)
        groups[oid].append(record)
    per_fill, per_order, raw_total = Decimal(0), Decimal(0), Decimal(0)
    order_records = []
    for oid, (original, qty, avg) in sorted(expected.items()):
        legs = groups[oid]
        if not legs or sum((_decimal(r['quantity']) for r in legs), Decimal(0)) != qty:
            raise ValueError('activity quantity coverage incomplete')
        gross = sum((_decimal(r['raw_consideration']) for r in legs), Decimal(0))
        quantum = Decimal(1).scaleb(avg.as_tuple().exponent)
        if abs(gross / qty - avg) > quantum / 2:
            raise ValueError('activity average price incompatible with reported precision')
        fill_cents = sum((_decimal(r['cent_consideration']) for r in legs), Decimal(0))
        order_cents = gross.quantize(CENT, rounding=ROUND_HALF_EVEN)
        if fill_cents != order_cents:
            raise ValueError('cash posting granularity disagreement')
        sign = 1 if str(original['side']).lower() == 'sell' else -1
        per_fill += sign * fill_cents
        per_order += sign * order_cents
        raw_total += sign * gross
        order_records.append({'order_id': oid, 'client_order_id': original['client_order_id'],
                              'raw_quantity': str(qty), 'raw_average_price': str(avg),
                              'raw_order_average_consideration': str(qty * avg),
                              'raw_activity_consideration': str(gross),
                              'posted_consideration': str(order_cents), 'side': str(original['side']).upper()})
    if per_fill != per_order or start + per_fill != end:
        raise ValueError('posted cash does not exactly explain broker cash')
    evidence = {'schema_version': 'caerus.alpaca_paper_cash_posting.v1',
                'provider': 'alpaca', 'scope': 'PAPER', 'endpoint': '/account/activities',
                'trade_date': trade_date, 'starting_state_boundary': str(boundary),
                'broker_snapshot_observed_at': str(observed_at),
                'rounding': 'ROUND_HALF_EVEN', 'granularity': 'per_fill_equals_per_order',
                'non_fill_activity_policy': 'reject_all_same_day',
                'all_activity_count': len(activities),
                'all_activities_sha256': hashlib.sha256(json.dumps(list(activities), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest(),
                'starting_cash': str(start), 'ending_cash': str(end),
                'raw_signed_activity_consideration': str(raw_total),
                'per_fill_signed_consideration': str(per_fill),
                'per_order_signed_consideration': str(per_order),
                'orders': order_records, 'activities': sorted(records, key=lambda r: r['activity_id'])}
    evidence['content_hash'] = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return evidence


def validated_posted_cash(evidence, *, fills, trade_date, starting_cash, ending_cash):
    """Recheck evidence arithmetic and bind it to the pure verifier's raw fills."""
    payload = dict(evidence)
    digest = payload.pop('content_hash', None)
    if digest != hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest():
        raise ValueError('cash posting evidence hash mismatch')
    rows = {r['order_id']: r for r in evidence['orders']}
    if len(rows) != len(evidence['orders']) or len(fills) != len(rows):
        raise ValueError('cash posting fill coverage mismatch')
    if len({fill.order_id for fill in fills}) != len(fills):
        raise ValueError('duplicate raw fill order ID')
    orders = []
    for fill in fills:
        row = rows.get(fill.order_id)
        if (row is None or _decimal(row['raw_quantity']) != _decimal(fill.quantity) or
                _decimal(row['raw_average_price']) != _decimal(fill.price) or
                row['side'] != fill.side.upper() or _decimal(fill.fees) != 0):
            raise ValueError('cash posting raw fill mismatch')
        orders.append({'id': fill.order_id, 'client_order_id': row['client_order_id'],
                       'symbol': fill.symbol, 'side': fill.side, 'status': 'filled',
                       'qty': row['raw_quantity'], 'filled_qty': row['raw_quantity'],
                       'filled_avg_price': row['raw_average_price']})
    activities = [{'id': r['activity_id'], 'activity_type': 'FILL', 'order_id': r['order_id'],
                   'symbol': r['symbol'], 'side': r['side'], 'qty': r['quantity'],
                   'price': r['price'], 'transaction_time': r['transaction_time']}
                  for r in evidence['activities']]
    rebuilt = certify_cash_posting(activities=activities, orders=orders,
                                  starting_cash=starting_cash, ending_cash=ending_cash,
                                  boundary=evidence['starting_state_boundary'], trade_date=trade_date,
                                  observed_at=evidence['broker_snapshot_observed_at'])
    for key in ('provider', 'scope', 'schema_version', 'rounding', 'granularity', 'trade_date',
                'endpoint', 'non_fill_activity_policy', 'orders', 'activities',
                'starting_cash', 'ending_cash', 'per_fill_signed_consideration',
                'per_order_signed_consideration', 'raw_signed_activity_consideration'):
        if evidence.get(key) != rebuilt[key]:
            raise ValueError('cash posting evidence arithmetic mismatch')
    if (not isinstance(evidence.get('all_activity_count'), int) or
            evidence['all_activity_count'] < len(activities) or
            len(str(evidence.get('all_activities_sha256') or '')) != 64):
        raise ValueError('cash posting ingestion provenance missing')
    return float(_decimal(starting_cash) + _decimal(rebuilt['per_fill_signed_consideration']))
