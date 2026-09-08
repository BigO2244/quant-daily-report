import hashlib
import json
import shutil

import pytest

from core.causal_ownership_ledger import CausalOwnershipError, _hash, build_causal_ownership
from core.daily_portfolio_audit import _audit_cutover_ownership
from scripts.build_paper_ownership_cutover import create_cutover
from Tests.test_causal_ownership_ledger import _fixture


def _inputs(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    ledger, plan = _fixture(source)
    build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[])
    root = tmp_path / 'offline'
    target = root / 'outputs/ledger/paper'
    target.parent.mkdir(parents=True)
    shutil.move(str(ledger), target)
    ledger = target
    plans = root / 'outputs/paper_lane/plans'
    plans.mkdir(parents=True)
    shutil.move(str(plan), plans / plan.name)
    account = json.loads((ledger / 'account_snapshots.jsonl').read_text())
    account['account_id_hash'] = 'a' * 64
    (ledger / 'account_snapshots.jsonl').write_text(json.dumps(account) + '\n')
    account_path = tmp_path / 'account.json'
    account_path.write_text(json.dumps(account))
    positions = json.loads((ledger / 'positions_latest.json').read_text())
    capture = {'schema_version': 'caerus.paper_opening_capture.v1', 'account_scope': 'PAPER',
               'account_id_hash': 'a' * 64, 'pulled_at_utc': account['pulled_at_utc'],
               'account_snapshot_hash': _hash(account), 'positions_snapshot_hash': _hash(positions),
               'open_orders': []}
    capture['content_hash'] = _hash(capture)
    capture_path = tmp_path / 'capture.json'
    capture_path.write_text(json.dumps(capture))
    history = (ledger / 'causal_fills.jsonl').read_bytes()
    kwargs = dict(ledger_dir=ledger, account_snapshot=account_path,
                  positions_snapshot=ledger / 'positions_latest.json', no_open_orders_capture=capture_path,
                  expected_account_id_hash='a' * 64,
                  expected_history_sha256=hashlib.sha256(history).hexdigest(),
                  expected_history_bytes=len(history), expected_history_fill_count=5,
                  output=ledger / 'ownership_cutover.json')
    return root, history, kwargs


def test_cutover_write_once_roundtrip_independent_audit(tmp_path):
    root, history, kwargs = _inputs(tmp_path)
    contract = create_cutover(**kwargs)
    assert create_cutover(**kwargs) == contract
    assert contract['opening_book'] == [{'symbol': 'AAPL', 'sleeve_id': 'caerus_orion', 'quantity': 12.0}]
    ledger = kwargs['ledger_dir']
    plans = root / 'outputs/paper_lane/plans'
    build_causal_ownership(ledger_dir=ledger, exact_plan_paths=list(plans.glob('*.json')), plans_root=plans)
    ownership = json.loads((ledger / 'ownership_latest.json').read_text())
    assert _audit_cutover_ownership(root, ownership)
    assert (ledger / 'causal_fills.jsonl').read_bytes() == history


@pytest.mark.parametrize('defect', ['prefix', 'account', 'timestamp', 'open_order', 'capture_hash'])
def test_cutover_rejects_mismatched_acquired_evidence(tmp_path, defect):
    _, history, kwargs = _inputs(tmp_path)
    if defect == 'prefix':
        kwargs['expected_history_sha256'] = '0' * 64
    elif defect == 'account':
        kwargs['expected_account_id_hash'] = 'b' * 64
    else:
        path = kwargs['no_open_orders_capture']
        capture = json.loads(path.read_text())
        capture.pop('content_hash')
        if defect == 'timestamp':
            capture['pulled_at_utc'] = '2026-08-15T00:00:00Z'
        elif defect == 'open_order':
            capture['open_orders'] = [{'id': 'pending'}]
        capture['content_hash'] = '0' * 64 if defect == 'capture_hash' else _hash(capture)
        path.write_text(json.dumps(capture))
    with pytest.raises(CausalOwnershipError):
        create_cutover(**kwargs)
    assert not kwargs['output'].exists()
    assert (kwargs['ledger_dir'] / 'causal_fills.jsonl').read_bytes() == history


def test_cutover_refuses_to_replace_existing_different_output(tmp_path):
    _, _, kwargs = _inputs(tmp_path)
    kwargs['output'].write_text('retained artifact\n')
    with pytest.raises(CausalOwnershipError, match='write-once'):
        create_cutover(**kwargs)
    assert kwargs['output'].read_text() == 'retained artifact\n'
