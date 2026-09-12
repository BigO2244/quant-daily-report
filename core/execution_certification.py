"""Five consecutive XNYS sessions; missing evidence never earns certification.

Extends (does not replace) the historical twenty-session integrity metric.
All outputs are diagnostics and grant no capital or promotion authority.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from core.trading_integrity_certification import certify_session as integrity_session, trading_sessions_ending, _read, _find_submit_run
from authority.exact_plan import exact_execution_plan_from_dict


def number(value):
    if isinstance(value, bool):
        raise ValueError('boolean numeric evidence')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('nonfinite numeric evidence')
    return result


def stamp(value):
    result = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timestamp lacks timezone')
    return result


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def decision_nav_provenance(*, plan, payload, trade_date):
    """Verify the original hash-bound Decision snapshot and same-window marks.

    The submission snapshot is a later execution observation. Its timestamp or
    NAV must never be substituted for the inputs used to authorize this plan.
    Legacy plans remain readable but cannot acquire this proof retroactively.
    """
    before = payload['broker_state_at_decision']
    sources = plan['source_artifact_hashes']
    if canonical_hash(before) != sources['broker_state_at_decision']:
        return False
    account = before['account']
    if not account['account_id_hash'] or account['account_id_hash'] != plan['account_id_hash']:
        return False
    if not isinstance(before['open_orders'], list) or before['open_orders']:
        return False
    quote = plan['market_state']['quote_evidence']
    quote_hash = canonical_hash({k: v for k, v in quote.items() if k != 'content_hash'})
    if quote_hash != quote['content_hash'] or quote_hash != sources['authorization_market_state']:
        return False
    captured = stamp(before['captured_at'])
    started = stamp(before['capture_started_at'])
    completed = stamp(before['capture_completed_at'])
    created = stamp(plan['created_at'])
    sealed = stamp(plan['authorization_state']['authorized_at'])
    max_age = number(quote['broker_snapshot_max_age_seconds'])
    from zoneinfo import ZoneInfo
    if (max_age <= 0 or max_age > 120 or captured != completed
            or captured.astimezone(ZoneInfo('America/New_York')).date().isoformat() != trade_date
            or not 0 <= (completed-started).total_seconds() <= max_age
            or not 0 <= (created-completed).total_seconds() <= max_age
            or not 0 <= (sealed-completed).total_seconds() <= max_age
            or sealed < created
            or stamp(quote['broker_snapshot_captured_at']) != captured):
        return False

    def quantities(rows, key):
        result = {}
        if not isinstance(rows, list):
            raise ValueError('positions must be a list')
        for row in rows:
            symbol = row['symbol']
            quantity = number(row[key])
            if not isinstance(symbol, str) or not symbol or symbol in result or quantity < 0:
                raise ValueError('invalid or duplicate position')
            result[symbol] = quantity
        return {symbol: qty for symbol, qty in result.items() if qty > 1e-12}

    positions = quantities(before['positions'], 'qty')
    if positions != quantities(plan['starting_positions'], 'quantity'):
        return False
    prices = {}
    for row in quote['quotes']:
        if row['symbol'] in prices or number(row['price']) <= 0:
            return False
        prices[row['symbol']] = number(row['price'])
    cash = number(account['cash'])
    reported_nav = number(account['equity'])
    position_value = sum(qty * prices[symbol] for symbol, qty in positions.items())
    nav = cash + position_value
    reconstruction = plan['risk_state']['decision_nav_reconstruction']
    constraints = plan['constraints']
    def equal(left, right):
        return abs(number(left) - number(right)) <= .01
    return (reported_nav > 0 and cash >= 0 and nav > 0
            and reconstruction == quote['nav_reconstruction']
            and equal(plan['starting_cash'], cash)
            and equal(plan['portfolio_nav'], nav)
            and equal(reconstruction['broker_reported_nav'], reported_nav)
            and equal(reconstruction['authoritative_position_value'], position_value)
            and equal(reconstruction['authoritative_account_nav'], nav)
            and equal(reconstruction['broker_reported_to_authoritative_nav_delta'], nav-reported_nav)
            and equal(reconstruction['planning_equity'], nav)
            and equal(reconstruction['planning_cash'], cash)
            and constraints['full_current_account_required'] is True
            and number(constraints['capital_cap_usd']) >= nav)


def certify_session(*, repo_root: Path, trade_date: str):
    root = Path(repo_root).resolve()
    base = integrity_session(repo_root=root, trade_date=trade_date)
    controls = dict(base['controls'])
    workflow = _read(root / 'outputs/workflow' / trade_date / 'execution.json')
    run = _find_submit_run(root, trade_date, workflow)
    evidence = {}
    def read(name):
        path = run / name if run else root / '__missing__' / name
        value = _read(path) or {}
        if path.is_file():
            evidence[str(path.relative_to(root))] = sha(path)
        return value
    payload = read('execution_payload.json')
    plan = payload.get('exact_execution_plan') or {}
    constraints = plan.get('constraints') or {}
    before = read('live_pilot_broker_snapshot_pre.json')
    after = read('live_pilot_broker_snapshot_post.json')
    equality = read('equality_gate.json')
    orders = read('live_pilot_orders_submitted.json').get('orders')
    econ = read('canonical_economic_verification.json')
    economic = econ.get('economic_reconciliation') or {}
    def check(name, fn, reason):
        try:
            passed = bool(fn())
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, OSError):
            passed = False
        controls[name] = {'pass': passed, 'reasons': [] if passed else [reason]}

    check('deterministic_exact_plan', lambda: bool(exact_execution_plan_from_dict(plan)), 'exact_plan_rebuild_failed')
    # Repeated model targets must be independently materialized against identical inputs.
    replay_path = root / 'outputs/execution_certification' / trade_date / 'target_replay.json'
    if replay_path.is_file():
        evidence[str(replay_path.relative_to(root))] = sha(replay_path)
    # Receipts and copied output files are never an authorization to pass.
    # Re-execute the original producer using the plan-bound local source chain.
    from core.target_replay import verify_target_replay
    replay_verification = verify_target_replay(repo_root=root, payload=payload, trade_date=trade_date)
    controls['deterministic_target'] = {
        'pass': replay_verification['pass'], 'reasons': replay_verification['reasons'],
        'verification': replay_verification,
    }
    evidence.update(replay_verification.get('input_sha256') or {})
    check('exact_target_consumption', lambda: equality['plan_hash_validated'] is True and equality['authorization_validated'] is True
          and equality['enforced_pre_submit'] is True and equality['authorized_plan_hash'] == plan['content_hash']
          and equality['authorized_order_ids'] == equality['submitted_order_ids'], 'exact_order_equality_not_proven')
    intended = [*(plan.get('sell_orders') or []), *(plan.get('buy_orders') or [])]
    def explained():
        if not isinstance(orders, list) or len(orders) != len(intended): return False
        by_id = {o['client_order_id']: o for o in orders}
        if len(by_id) != len(orders): return False
        for expected in intended:
            actual = by_id[expected['client_order_id']]
            if actual['symbol'] != expected['symbol'] or actual['side'].upper() != expected['side'].upper(): return False
            if abs(number(actual['filled_qty']) - number(expected['quantity'])) > 1e-8: return False
            if str(actual.get('status', (actual.get('raw') or {}).get('status'))).lower().split('.')[-1] != 'filled': return False
        return True
    check('order_fill_explanation', explained, 'missing_duplicate_unfilled_or_unexplained_order')
    def sell_first():
        if constraints.get('sell_first') is not True: return False
        if constraints.get('post_sell_rebudgeting') not in (True, 'FORBIDDEN'): return False
        # Exact sealed plans forbid discretionary resizing. Prove the complete
        # conservative order budget instead of requiring a target mutation.
        budget = number(plan['starting_cash'])
        for order in intended:
            value = number(order['quantity']) * number(order.get('cap_enforcement_price') or order['price'])
            budget += value if order['side'] == 'SELL' else -value
            if budget < 0: return False
        sells = [stamp(o['filled_at']) for o in orders if o['side'].upper() == 'SELL']
        buys = [stamp((o.get('raw') or {}).get('submitted_at')) for o in orders if o['side'].upper() == 'BUY']
        return (not sells or not buys or max(sells) <= min(buys)) and explained()
    check('sell_first_rebudgeting', sell_first, 'confirmed_sells_before_buys_not_proven')
    def nav_basis():
        return decision_nav_provenance(plan=plan, payload=payload, trade_date=trade_date)
    check('broker_pretrade_nav_sizing', nav_basis, 'fresh_pretrade_broker_NAV_not_bound_to_plan')
    check('cash_constraints', lambda: number(after['account']['cash']) >= number(after['account']['equity'])*.025
          and number(plan['expected_posttrade_cash']) >= 0, 'cash_floor_not_proven')
    check('final_positions', lambda: economic['positions']['actual'] == economic['positions']['expected']
          and not economic['positions']['quantity_deltas'] and economic['reconciled'] is True, 'positions_not_reconciled')
    check('cash_nav_reconciliation', lambda: econ['trade_date'] == trade_date and economic['reconciled'] is True
          and abs(number(economic['cash']['delta'])) <= .01
          and abs(number(economic['nav']['delta'])) <= number(economic['tolerance']['nav_abs'])
          and stamp(after['captured_at']).date().isoformat() == trade_date, 'cash_NAV_reconciliation_not_proven')
    def numeric_provenance():
        if not (controls['execution_consumed_exact_artifact']['pass']
                and controls['order_fill_explanation']['pass'] and econ.get('trade_date') == trade_date
                and bool(plan['source_artifact_hashes'])):
            return False
        activities = economic['cash']['posting_evidence']['activities']
        if intended:
            return (isinstance(activities, list) and bool(activities)
                    and {o['client_order_id'] for o in intended} == {a['client_order_id'] for a in activities}
                    and all(o.get('sleeve_contributions') and o.get('client_order_id') for o in intended))
        # Explicit no-order evidence is meaningful; absent orders or posting
        # fields are not proof that nothing happened.
        if not (plan['sell_orders'] == [] and plan['buy_orders'] == []
                and orders == [] and activities == [] and economic['reconciled'] is True):
            return False
        def quantities(rows, field):
            if not isinstance(rows, list):
                raise ValueError('missing no-order positions')
            result = {row['symbol']: number(row[field]) for row in rows}
            if len(result) != len(rows) or any(qty < 0 for qty in result.values()):
                raise ValueError('invalid no-order positions')
            return {symbol: qty for symbol, qty in result.items() if qty != 0}
        starting = quantities(plan['starting_positions'], 'quantity')
        cash = number(before['account']['cash'])
        return (starting == quantities(plan['expected_posttrade_positions'], 'quantity')
                == quantities(before['positions'], 'qty') == quantities(after['positions'], 'qty')
                == economic['positions']['expected'] == economic['positions']['actual']
                and not economic['positions']['quantity_deltas']
                and all(abs(number(value)-cash) <= .01 for value in (
                    after['account']['cash'], plan['starting_cash'], plan['expected_posttrade_cash'],
                    economic['cash']['expected'], economic['cash']['actual']))
                and abs(number(economic['cash']['delta'])) <= .01)
    check('numeric_provenance', numeric_provenance, 'numeric_input_fill_provenance_incomplete')
    # The legacy metric compares limit-price projected cash with actual cash.
    # Keep that metric intact, but explain the difference only with the exact
    # broker-fill cash ledger and independently reconciled NAV/positions.
    if (controls['broker_reconciliation']['reasons'] == ['intended_vs_reconciled_cash_mismatch']
            and all(controls[name]['pass'] for name in ('cash_nav_reconciliation', 'final_positions', 'order_fill_explanation', 'numeric_provenance'))):
        controls['broker_reconciliation'] = {'pass': True, 'reasons': [],
            'explanation': 'EXPLAINED: actual fill cash posting reconciles; limit-price projected cash is not realized cash',
            'legacy_metric_reasons': base['controls']['broker_reconciliation']['reasons']}
    unexplained = [name for name, result in controls.items() if not result['pass']]
    return {'trade_date': trade_date, 'certified': not unexplained, 'controls': controls,
            'unexplained_discrepancies': unexplained, 'unexplained_count': len(unexplained),
            'evidence_sha256': evidence, 'run_root': str(run) if run else None}


def consecutive_gate(rows, expected_dates):
    # Recompute from dates, never increment a mutable counter or count duplicate reruns.
    by_date = {row['trade_date']: row for row in rows}
    if len(by_date) != len(rows): raise ValueError('duplicate session')
    streak = 0
    for date in expected_dates:
        row = by_date.get(date)
        streak = streak + 1 if row and row.get('certified') is True and row.get('unexplained_count') == 0 else 0
    return {'consecutive_clean_sessions': streak, 'required_sessions': 5,
            'status': 'CERTIFIED' if streak >= 5 else 'NOT_CERTIFIED', 'capital_authority': False}


def certify_window(*, repo_root: Path, through_date: str):
    dates = trading_sessions_ending(through_date, 5)
    rows = [certify_session(repo_root=repo_root, trade_date=d) for d in dates]
    return {'schema_version': 'caerus.execution_certification.v1', 'through_date': through_date,
            **consecutive_gate(rows, dates), 'sessions': rows}
