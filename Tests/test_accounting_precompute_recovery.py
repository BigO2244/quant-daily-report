"""Offline recovery chain: rejected pair -> coherent capture -> ownership -> Aquila.

Synthetic broker responses only. Execution/confirmation coverage remains in
existing exact-plan and pipeline integration suites; this test joins the
previously untested accounting-to-precompute dependency.
"""
import datetime as dt
import json
import shutil
from types import SimpleNamespace

import pytest

from Tests.test_aquila_daily_source import setup
from Tests.test_causal_ownership_ledger import _cutover_fixture, _hash
from core.aquila_monthly import AquilaContractError
from core.causal_ownership_ledger import build_causal_ownership
from scripts import build_broker_truth_ledger as collector
from scripts.build_aquila_daily_source import build_daily_source


def test_stale_book_is_recovered_by_coherent_capture_without_changing_history(tmp_path, monkeypatch):
    setup(tmp_path)
    fixture = tmp_path / 'fixture'
    fixture.mkdir()
    old, plan, history = _cutover_fixture(fixture)
    ledger = tmp_path / 'outputs/ledger/paper'
    shutil.copytree(old, ledger, dirs_exist_ok=True)
    contract_path = ledger / 'ownership_cutover.json'
    contract = json.loads(contract_path.read_text())
    contract['opening_book'] = [{'symbol': 'AAPL', 'sleeve_id': 'caerus_orion', 'quantity': 12}]
    contract.pop('content_hash')
    contract['content_hash'] = _hash(contract)
    contract_path.write_text(json.dumps(contract))
    build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[])
    args = dict(repo_root=tmp_path, bundle_dir=tmp_path / 'bundle', trade_date='2026-09-08',
                generated_at='2026-09-08T09:00:30+00:00')
    with pytest.raises(AquilaContractError, match='stale or future'):
        build_daily_source(**args)

    positions = json.loads((ledger / 'positions_latest.json').read_text())['positions']
    class Client:
        calls = 0
        def account(self):
            self.calls += 1
            return {'equity': '1424', 'cash': '200', 'id': 'fixture-account'}
        def positions(self):
            if self.calls == 1:
                return [{**positions[0], 'market_value': '1230'}]
            return positions
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 9, 4, 23, 15, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(collector, 'dt', SimpleNamespace(datetime=Clock, timezone=dt.timezone))
    account, rows, as_of, receipt = collector.capture_account_positions(Client(), ledger, retry_delay=0)
    snap = collector.build_account_snapshot(account, as_of, 'paper')
    # Fixture's immutable account identity predates the new capture.
    snap['account_id_hash'] = contract['account_id_hash']
    collector.append_jsonl(ledger / 'account_snapshots.jsonl', [snap])
    collector.atomic_write(ledger / 'positions_latest.json', json.dumps({'pulled_at_utc': as_of, 'positions': rows}))
    result = build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[])
    assert result['status'] == 'PASS'
    assert receipt['attempt'] == 2
    assert (ledger / 'causal_fills.jsonl').read_bytes() == history
    source_path = build_daily_source(**args)
    source = json.loads(source_path.read_text())
    assert source['quantity_contract']['action'] == 'MONTHLY_REBALANCE'
    assert len(source['target_weights']) == 10
