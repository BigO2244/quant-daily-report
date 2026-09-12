import pytest
from core.execution_certification import consecutive_gate, certify_session
import copy
import json
from core.execution_certification import canonical_hash, decision_nav_provenance


def rows():
    return [{'trade_date': str(i), 'certified': True, 'unexplained_count': 0} for i in range(5)]


def test_five_clean_sessions_and_duplicate_rerun():
    sample = rows()
    assert consecutive_gate(sample, list(map(str, range(5))))['status'] == 'CERTIFIED'
    with pytest.raises(ValueError, match='duplicate'):
        consecutive_gate(sample + sample[:1], list(map(str, range(5))))


@pytest.mark.parametrize('failure', ['missing', 'discrepancy', 'failed'])
def test_failure_resets_counter(failure):
    sample = rows()
    if failure == 'missing': sample.pop(3)
    elif failure == 'discrepancy': sample[3]['unexplained_count'] = 1
    else: sample[3]['certified'] = False
    assert consecutive_gate(sample, list(map(str, range(5))))['consecutive_clean_sessions'] == 1


def test_missing_evidence_fails_all_added_controls(tmp_path):
    report = certify_session(repo_root=tmp_path, trade_date='2026-09-11')
    assert not report['certified']
    assert report['unexplained_count'] > 0
    assert not report['controls']['deterministic_target']['pass']
    assert not report['controls']['broker_pretrade_nav_sizing']['pass']


def nav_fixture():
    """A $1,000 Decision with a broker mark $1 below the fresh quote mark."""
    before = {'captured_at': '2026-09-11T13:35:00+00:00',
              'capture_started_at': '2026-09-11T13:34:59+00:00',
              'capture_completed_at': '2026-09-11T13:35:00+00:00',
              'account': {'account_id_hash': 'a'*64, 'equity': '999', 'cash': '900'},
              'positions': [{'symbol': 'ABC', 'qty': '2'}], 'open_orders': []}
    reconstruction = {'broker_reported_nav': 999, 'authoritative_position_value': 100,
                      'authoritative_account_nav': 1000,
                      'broker_reported_to_authoritative_nav_delta': 1,
                      'planning_equity': 1000, 'planning_cash': 900}
    quote = {'broker_snapshot_max_age_seconds': 120,
             'broker_snapshot_captured_at': before['captured_at'],
             'quotes': [{'symbol': 'ABC', 'price': 50}],
             'nav_reconstruction': reconstruction}
    quote['content_hash'] = canonical_hash(quote)
    plan = {'source_artifact_hashes': {
                'broker_state_at_decision': canonical_hash(before),
                'authorization_market_state': quote['content_hash']},
            'account_id_hash': 'a'*64,
            'created_at': '2026-09-11T13:35:01+00:00',
            'authorization_state': {'authorized_at': '2026-09-11T13:35:02+00:00'},
            'market_state': {'quote_evidence': quote},
            'starting_positions': [{'symbol': 'ABC', 'quantity': 2}],
            'starting_cash': 900, 'portfolio_nav': 1000,
            'risk_state': {'decision_nav_reconstruction': copy.deepcopy(reconstruction)},
            'constraints': {'full_current_account_required': True, 'capital_cap_usd': 1000}}
    return plan, {'broker_state_at_decision': before}


def test_original_snapshot_and_fresh_marks_prove_nav():
    plan, payload = nav_fixture()
    assert decision_nav_provenance(plan=plan, payload=payload, trade_date='2026-09-11')


@pytest.mark.parametrize('corruption', [
    'snapshot_cash', 'missing_binding', 'missing_snapshot', 'account', 'stale',
    'future', 'wrong_date', 'holdings', 'nav', 'cash', 'quote', 'cap', 'open_orders',
    'capture_duration', 'reconstruction', 'negative_quantity'])
def test_nav_provenance_rejects_corruption(corruption):
    plan, payload = nav_fixture()
    snapshot = payload['broker_state_at_decision']
    date = '2026-09-11'
    if corruption == 'snapshot_cash': snapshot['account']['cash'] = '950'
    elif corruption == 'missing_binding': plan['source_artifact_hashes'].pop('broker_state_at_decision')
    elif corruption == 'missing_snapshot': payload.clear()
    elif corruption == 'account': plan['account_id_hash'] = 'b'*64
    elif corruption == 'stale': plan['authorization_state']['authorized_at'] = '2026-09-11T13:38:00+00:00'
    elif corruption == 'future': plan['created_at'] = '2026-09-11T13:34:59+00:00'
    elif corruption == 'wrong_date': date = '2026-09-10'
    elif corruption == 'holdings': plan['starting_positions'][0]['quantity'] = 3
    elif corruption == 'nav': plan['portfolio_nav'] = 999
    elif corruption == 'cash': plan['starting_cash'] = 899
    elif corruption == 'quote': plan['market_state']['quote_evidence']['quotes'][0]['price'] = 51
    elif corruption == 'cap': plan['constraints']['capital_cap_usd'] = 500
    elif corruption == 'open_orders':
        snapshot['open_orders'] = [{'symbol': 'ABC'}]
        plan['source_artifact_hashes']['broker_state_at_decision'] = canonical_hash(snapshot)
    elif corruption == 'capture_duration':
        snapshot['capture_started_at'] = '2026-09-11T13:30:00+00:00'
        plan['source_artifact_hashes']['broker_state_at_decision'] = canonical_hash(snapshot)
    elif corruption == 'reconstruction': plan['risk_state']['decision_nav_reconstruction']['planning_equity'] = 500
    elif corruption == 'negative_quantity':
        snapshot['positions'][0]['qty'] = '-2'
        plan['source_artifact_hashes']['broker_state_at_decision'] = canonical_hash(snapshot)
    try:
        passed = decision_nav_provenance(plan=plan, payload=payload, trade_date=date)
    except (KeyError, ValueError):
        passed = False
    assert not passed


def session_fixture(tmp_path, monkeypatch):
    import core.execution_certification as module
    plan, payload = nav_fixture()
    payload['exact_execution_plan'] = plan
    payload['approved_execution_package'] = {'approved_target_rows': [
        {'symbol': 'ABC', 'target_weight': .1}]}
    run = tmp_path/'run'
    run.mkdir()
    (run/'execution_payload.json').write_text(json.dumps(payload))
    # A later submission snapshot is deliberately different. It cannot replace
    # the original Decision input when certifying the sizing provenance.
    later = copy.deepcopy(payload['broker_state_at_decision'])
    later['captured_at'] = '2026-09-11T13:36:00+00:00'
    later['account']['equity'] = '1001'
    (run/'live_pilot_broker_snapshot_pre.json').write_text(json.dumps(later))
    monkeypatch.setattr(module, '_find_submit_run', lambda *args: run)
    monkeypatch.setattr(module, 'integrity_session', lambda **kwargs: {'controls': {
        'broker_reconciliation': {'pass': False, 'reasons': ['fixture_missing']},
        'execution_consumed_exact_artifact': {'pass': False, 'reasons': ['fixture_missing']}}})
    return plan, payload


def test_certificate_uses_original_snapshot_not_later_submission(tmp_path, monkeypatch):
    session_fixture(tmp_path, monkeypatch)
    report = certify_session(repo_root=tmp_path, trade_date='2026-09-11')
    assert report['controls']['broker_pretrade_nav_sizing']['pass']
    assert not report['certified']  # This fixture supplies only the NAV control.


def test_copied_targets_do_not_prove_independent_producer(tmp_path, monkeypatch):
    import hashlib
    plan, payload = session_fixture(tmp_path, monkeypatch)
    weights = {'ABC': .1}
    target_text = json.dumps({'target_weights': weights})
    targets = []
    for name in ['target_a.json', 'target_b.json']:
        (tmp_path/name).write_text(target_text)
        targets.append({'path': name, 'sha256': hashlib.sha256(target_text.encode()).hexdigest(),
                        'input_hashes': plan['source_artifact_hashes']})
    replay = {'trade_date': '2026-09-11', 'input_hashes': plan['source_artifact_hashes'],
              'target_weights_hash': hashlib.sha256(json.dumps(weights, sort_keys=True,
                                                              allow_nan=False).encode()).hexdigest(),
              'targets': targets, 'producer_verified': True}
    receipt = tmp_path/'outputs/execution_certification/2026-09-11/target_replay.json'
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(replay))
    report = certify_session(repo_root=tmp_path, trade_date='2026-09-11')
    assert not report['controls']['deterministic_target']['pass']
    assert report['controls']['deterministic_target']['reasons']
    assert report['controls']['deterministic_target']['verification']['production_authority'] is False
    assert report['evidence_sha256'][str(receipt.relative_to(tmp_path))] == hashlib.sha256(receipt.read_bytes()).hexdigest()


@pytest.mark.parametrize('corruption', [None, 'missing_activities', 'cash', 'position', 'missing_orders'])
def test_explicit_no_order_session_provenance(tmp_path, monkeypatch, corruption):
    import core.execution_certification as module
    plan, payload = session_fixture(tmp_path, monkeypatch)
    plan.update(sell_orders=[], buy_orders=[], expected_posttrade_positions=plan['starting_positions'],
                expected_posttrade_cash=900.)
    run = tmp_path/'run'
    (run/'execution_payload.json').write_text(json.dumps(payload))
    after = copy.deepcopy(payload['broker_state_at_decision'])
    after['captured_at'] = '2026-09-11T13:37:00+00:00'
    if corruption == 'cash': after['account']['cash'] = '899'
    if corruption == 'position': after['positions'][0]['qty'] = '3'
    (run/'live_pilot_broker_snapshot_post.json').write_text(json.dumps(after))
    if corruption != 'missing_orders':
        (run/'live_pilot_orders_submitted.json').write_text(json.dumps({'orders': []}))
    cash = {'expected': 900., 'actual': 900., 'delta': 0., 'posting_evidence': {'activities': []}}
    if corruption == 'missing_activities': cash['posting_evidence'].clear()
    (run/'canonical_economic_verification.json').write_text(json.dumps({'trade_date': '2026-09-11',
        'economic_reconciliation': {'reconciled': True, 'cash': cash,
            'positions': {'expected': {'ABC': 2.}, 'actual': {'ABC': 2.}, 'quantity_deltas': {}},
            'nav': {'delta': 0.}, 'tolerance': {'nav_abs': .01}}}))
    monkeypatch.setattr(module, 'integrity_session', lambda **kwargs: {'controls': {
        'broker_reconciliation': {'pass': True, 'reasons': []},
        'execution_consumed_exact_artifact': {'pass': True, 'reasons': []}}})
    result = certify_session(repo_root=tmp_path, trade_date='2026-09-11')
    assert result['controls']['numeric_provenance']['pass'] is (corruption is None)
    assert not result['certified']  # Other evidence is intentionally absent.
