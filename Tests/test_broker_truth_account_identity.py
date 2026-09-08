import json

import pytest

from core.live_pilot_guardrails import account_id_hash
from scripts.build_broker_truth_ledger import append_jsonl, build_account_snapshot


def test_fresh_paper_snapshot_uses_broker_id_and_preserves_history(tmp_path):
    path = tmp_path / 'account_snapshots.jsonl'
    old = b'{"pulled_at_utc":"2026-09-01T00:00:00Z","equity":"100"}\n'
    path.write_bytes(old)
    snapshot = build_account_snapshot({'id': 'broker-uuid', 'account_number': '12345678', 'equity': '110'},
                                      '2026-09-09T00:00:00Z', 'paper')
    assert snapshot['account_id_hash'] == account_id_hash('broker-uuid')
    assert snapshot['account_id_hash'] != account_id_hash('12345678')
    assert 'broker-uuid' not in json.dumps(snapshot)
    append_jsonl(path, [snapshot])
    assert path.read_bytes().startswith(old)


def test_missing_paper_account_id_fails_and_live_shape_unchanged():
    with pytest.raises(ValueError, match='lacks account ID'):
        build_account_snapshot({'account_number': '12345678'}, 'now', 'paper')
    assert 'account_id_hash' not in build_account_snapshot({'id': 'live-id'}, 'now', 'live')
