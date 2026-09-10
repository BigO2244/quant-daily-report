"""Hash-bound sleeve transfers, committed only after exact broker reconciliation.

Transfers move existing beneficial ownership within one PAPER account; they are
never broker fills and never change aggregate shares or account cash.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from pathlib import Path

OWNERS = {'caerus_aquila', 'caerus_orion'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def net_demands(demands, prices, decisions):
    residual, transfers = {}, []
    for symbol, owners in sorted(demands.items()):
        delta = {owner: float(q) for owner, q in owners.items()}
        if set(delta) - OWNERS or any(not math.isfinite(q) for q in delta.values()):
            raise ValueError('invalid transfer owner demand')
        sellers = [s for s, q in delta.items() if q < -1e-6]
        buyers = [s for s, q in delta.items() if q > 1e-6]
        if sellers and buyers:
            seller, buyer = sellers[0], buyers[0]
            quantity = round(min(-delta[seller], delta[buyer]), 6)
            price = float(prices[symbol])
            if not math.isfinite(price) or price <= 0:
                raise ValueError('invalid transfer valuation price')
            lineage = {}
            for owner in (seller, buyer):
                row = decisions[owner]
                if not row.get('decision_id') or len(str(row.get('decision_hash', ''))) != 64:
                    raise ValueError('transfer lacks sleeve decision lineage')
                lineage[owner] = {k: row[k] for k in ('decision_id', 'decision_hash')}
            transfers.append(dict(symbol=symbol, from_sleeve=seller, to_sleeve=buyer,
                                  quantity=quantity, reference_price=price,
                                  reference_notional=quantity * price, decisions=lineage))
            delta[seller] = round(delta[seller] + quantity, 6)
            delta[buyer] = round(delta[buyer] - quantity, 6)
        residual[symbol] = delta
    return residual, transfers


def validate_transfers(qa):
    """Recompute paired transfers and residual demands from signed original intent."""
    transfers = qa.get('internal_transfers') or []
    if not transfers:
        demands = qa.get('signed_sleeve_demands') or {}
        if any(any(float(q) > 1e-6 for q in owners.values()) and any(float(q) < -1e-6 for q in owners.values()) for owners in demands.values()):
            raise ValueError('opposing sleeve demands lack internal transfer authority')
        return demands
    if qa.get('quantity_contract', {}).get('sizing_mode') == 'FIXED_QUANTITY':
        raise ValueError('protected Aquila holdings cannot transfer')
    prices, decisions = {}, {}
    for row in transfers:
        if row['symbol'] in prices:
            raise ValueError('duplicate internal transfer symbol')
        prices[row['symbol']] = row['reference_price']
        for owner, decision in row['decisions'].items():
            if owner in decisions and decisions[owner] != decision:
                raise ValueError('conflicting transfer decisions')
            decisions[owner] = decision
    residual, expected = net_demands(qa['signed_sleeve_demands'], prices, decisions)
    if expected != transfers or residual != qa.get('broker_sleeve_demands'):
        raise ValueError('internal transfer arithmetic mismatch')
    return residual


def apply_transfers(ownership, transfers):
    for row in transfers:
        owner = ownership.setdefault(row['symbol'], {})
        quantity = float(row['quantity'])
        seller, buyer = row['from_sleeve'], row['to_sleeve']
        if seller not in OWNERS or buyer not in OWNERS or seller == buyer or not math.isfinite(quantity) or quantity <= 0:
            raise ValueError('invalid internal transfer')
        if float(owner.get(seller, 0)) + 1e-6 < quantity:
            raise ValueError('internal transfer exceeds source sleeve inventory')
        owner[seller] = round(float(owner.get(seller, 0)) - quantity, 6)
        owner[buyer] = round(float(owner.get(buyer, 0)) + quantity, 6)


def commit_transfer_receipt(*, plan, receipt_root, run_id, outcome, economic_status, attainment_ok):
    qa = plan.to_dict()['constraints'].get('aquila_quantity_authority') or {}
    validate_transfers(qa)
    transfers = qa.get('internal_transfers') or []
    if not transfers:
        return None
    if (outcome.terminal_outcome.value not in {'RECONCILED_SUCCESS', 'AUTHORIZED_NO_TRADE'}
            or not outcome.plan_hash_validated or not outcome.authorization_validated
            or outcome.plan_hash_received != plan.content_hash
            or economic_status != 'RECONCILED' or not attainment_ok
            or len(outcome.orders_filled) != len(plan.orders)):
        raise ValueError('internal transfer requires complete reconciled exact execution')
    body = dict(schema_version='caerus.sleeve_transfer_receipt.v1', plan_hash=plan.content_hash,
                plan_id=plan.plan_id, account_id_hash=plan.account_id_hash,
                ownership_snapshot_sha256=qa['ownership_snapshot_sha256'],
                transfers=transfers, broker_order_ids=sorted(str(r.get('id') or r.get('order_id')) for r in outcome.orders_filled))
    root = Path(receipt_root); root.mkdir(parents=True, exist_ok=True)
    path = root / f'{plan.content_hash}.json'
    if path.exists():
        prior = json.loads(path.read_text()); check = dict(prior); claimed = check.pop('content_hash')
        if digest(check) != claimed or any(prior.get(k) != v for k, v in body.items()):
            raise ValueError('immutable internal transfer receipt conflict')
        return prior
    body.update(committed_at=dt.datetime.now(dt.timezone.utc).isoformat(), run_id=run_id)
    body['content_hash'] = digest(body)
    # Atomic no-overwrite publication; retries retain the first committed epoch.
    import os, tempfile
    fd, temp = tempfile.mkstemp(prefix='.transfer-', dir=root)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(body, handle, sort_keys=True, indent=2, allow_nan=False)
            handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
        try:
            os.link(temp, path)
            directory_fd = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except FileExistsError:
            return commit_transfer_receipt(plan=plan, receipt_root=root, run_id=run_id,
                outcome=outcome, economic_status=economic_status, attainment_ok=attainment_ok)
    finally:
        os.unlink(temp)
    return body


def load_transfer_receipts(*, receipt_root, plans, account_hash, broker_orders, fills, as_of):
    """Verify committed transfers against immutable plans and actual broker fills."""
    def timestamp(value):
        import re
        raw = str(value).replace('Z', '+00:00')
        match = re.fullmatch(r'(.+T\d{2}:\d{2}:\d{2})\.(\d+)([+-]\d{2}:\d{2})', raw)
        if match:
            raw = match[1] + '.' + match[2][:6].ljust(6, '0') + match[3]
        value = dt.datetime.fromisoformat(raw)
        if value.tzinfo is None:
            raise ValueError('transfer timestamp needs timezone')
        return value
    fills_by_order = {}
    for row in fills:
        fills_by_order.setdefault(str(row.get('order_id')), []).append(row)
    receipts = []
    committed_plan_hashes = set()
    for path in sorted(Path(receipt_root).glob('*.json')):
        receipt = json.loads(path.read_text()); body = dict(receipt); claimed = body.pop('content_hash', None)
        if claimed != digest(body) or receipt.get('schema_version') != 'caerus.sleeve_transfer_receipt.v1':
            raise ValueError('transfer receipt hash invalid')
        plan = plans.get(receipt['plan_hash'])
        if not plan or receipt['plan_id'] != plan['plan_id'] or plan.get('account_scope') != 'PAPER':
            raise ValueError('transfer receipt plan identity mismatch')
        if receipt['account_id_hash'] != account_hash or plan['account_id_hash'] != account_hash:
            raise ValueError('transfer receipt account mismatch')
        qa = plan['constraints']['aquila_quantity_authority']; validate_transfers(qa)
        if receipt['transfers'] != qa['internal_transfers'] or receipt['ownership_snapshot_sha256'] != qa['ownership_snapshot_sha256']:
            raise ValueError('transfer receipt authority mismatch')
        committed_plan_hashes.add(receipt['plan_hash'])
        committed = timestamp(receipt['committed_at'])
        if committed < timestamp(plan['created_at']):
            raise ValueError('transfer receipt predates authority')
        if committed > timestamp(as_of):
            continue
        order_ids = []
        for exact in [*plan.get('sell_orders', []), *plan.get('buy_orders', [])]:
            matching = [r for r in broker_orders.values() if r.get('client_order_id') == exact['client_order_id']]
            if len(matching) != 1:
                raise ValueError('transfer receipt lacks broker order evidence')
            order = matching[0]; order_ids.append(str(order['id']))
            observations = fills_by_order.get(str(order['id']), [])
            if (abs(sum(float(f['qty']) for f in observations) - float(exact['quantity'])) > 1e-6
                    or any(f['symbol'] != exact['symbol'] or f['side'].upper() != exact['side']
                           or timestamp(f['transaction_time_utc']) > committed for f in observations)):
                raise ValueError('transfer receipt lacks complete causal broker fills')
        if sorted(order_ids) != receipt['broker_order_ids']:
            raise ValueError('transfer receipt broker identity mismatch')
        receipts.append(receipt)
    for plan_hash, plan in plans.items():
        qa = plan.get('constraints', {}).get('aquila_quantity_authority') or {}
        orders = [*plan.get('sell_orders', []), *plan.get('buy_orders', [])]
        if not qa.get('internal_transfers') or plan_hash in committed_plan_hashes or not orders:
            continue
        completed = True
        for exact in orders:
            observed = [r for r in broker_orders.values() if r.get('client_order_id') == exact['client_order_id']]
            rows = fills_by_order.get(str(observed[0]['id']), []) if len(observed) == 1 else []
            completed = completed and abs(sum(float(f['qty']) for f in rows) - float(exact['quantity'])) <= 1e-6
        if completed:
            raise ValueError('completed transfer plan lacks committed ownership receipt')
    if len({r['plan_hash'] for r in receipts}) != len(receipts):
        raise ValueError('duplicate transfer receipt')
    return sorted(receipts, key=lambda r: (timestamp(r['committed_at']), r['plan_hash']))
