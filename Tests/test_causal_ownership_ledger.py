from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from core.causal_ownership_ledger import CausalOwnershipError, build_causal_ownership


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _fixture(tmp_path: Path, *, unmatched_after_epoch: bool = False) -> tuple[Path, Path]:
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    fills = [
        {
            "activity_id": "legacy-short",
            "transaction_time_utc": "2026-08-12T14:00:00Z",
            "trade_date_et": "2026-08-12",
            "symbol": "HCA",
            "side": "sell_short",
            "qty": 1,
            "price": 100,
            "multiplier": 1,
            "notional": 100,
            "order_id": "broker-legacy-short",
            "fill_type": "fill",
            "cum_qty": 1,
            "leaves_qty": 0,
        },
        {
            "activity_id": "legacy-cover",
            "transaction_time_utc": "2026-08-12T15:00:00Z",
            "trade_date_et": "2026-08-12",
            "symbol": "HCA",
            "side": "buy",
            "qty": 1,
            "price": 99,
            "multiplier": 1,
            "notional": 99,
            "order_id": "broker-legacy-cover",
            "fill_type": "fill",
            "cum_qty": 1,
            "leaves_qty": 0,
        },
        {
            "activity_id": "legacy-buy",
            # Alpaca history may emit fractional widths that Python 3.10's
            # datetime parser does not accept without normalization.
            "transaction_time_utc": "2026-08-13T14:00:00.52469Z",
            "trade_date_et": "2026-08-13",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "price": 100,
            "multiplier": 1,
            "notional": 1000,
            "order_id": "broker-legacy",
            "fill_type": "fill",
            "cum_qty": 10,
            "leaves_qty": 0,
        },
        {
            "activity_id": "causal-sell",
            "transaction_time_utc": "2026-08-14T13:36:00Z",
            "trade_date_et": "2026-08-14",
            "symbol": "AAPL",
            "side": "sell",
            "qty": 2,
            "price": 101,
            "multiplier": 1,
            "notional": 202,
            "order_id": "broker-sell",
            "fill_type": "fill",
            "cum_qty": 2,
            "leaves_qty": 0,
        },
        {
            "activity_id": "causal-buy",
            "transaction_time_utc": "2026-08-14T13:37:00Z",
            "trade_date_et": "2026-08-14",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 4,
            "price": 102,
            "multiplier": 1,
            "notional": 408,
            "order_id": "broker-unmatched" if unmatched_after_epoch else "broker-buy",
            "fill_type": "fill",
            "cum_qty": 4,
            "leaves_qty": 0,
        },
    ]
    with (ledger / "fills.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fills[0]))
        writer.writeheader()
        writer.writerows(fills)
    _write_jsonl(
        ledger / "orders.jsonl",
        [
            {"id": "broker-sell", "client_order_id": "cx-sell", "updated_at": "2026-08-14T13:36:00Z"},
            {"id": "broker-buy", "client_order_id": "cx-buy", "updated_at": "2026-08-14T13:37:00Z"},
        ],
    )
    _write_jsonl(
        ledger / "account_snapshots.jsonl",
        [
            {
                "pulled_at_utc": "2026-08-14T23:15:00Z",
                "equity": "1424",
                "cash": "200",
            }
        ],
    )
    (ledger / "positions_latest.json").write_text(
        json.dumps(
            {
                "pulled_at_utc": "2026-08-14T23:15:00Z",
                "positions": [
                    {
                        "symbol": "AAPL",
                        "qty": "12",
                        "market_value": "1224",
                        "current_price": "102",
                        "cost_basis": "1200",
                        "unrealized_pl": "24",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    plan = {
        "schema_version": "caerus.execution_plan.v3",
        "plan_id": "plan:2026-08-14:test",
        "created_at": "2026-08-14T13:35:00Z",
        "sell_orders": [
            {
                "symbol": "AAPL",
                "side": "SELL",
                "client_order_id": "cx-sell",
                "allocation_id": "allocation:test",
                "session_id": "session:test",
                "sleeve_contributions": [
                    {"sleeve_id": "caerus_alpha", "allocation_fraction": 0.75},
                    {"sleeve_id": "caerus_beta", "allocation_fraction": 0.25},
                ],
            }
        ],
        "buy_orders": [
            {
                "symbol": "AAPL",
                "side": "BUY",
                "client_order_id": "cx-buy",
                "allocation_id": "allocation:test",
                "session_id": "session:test",
                "sleeve_contributions": [
                    {"sleeve_id": "caerus_alpha", "allocation_fraction": 0.75},
                    {"sleeve_id": "caerus_beta", "allocation_fraction": 0.25},
                ],
            }
        ],
    }
    plan["content_hash"] = _hash(plan)
    plan_path = tmp_path / "plans" / "exact_execution_plan_2026-08-14.json"
    plan_path.parent.mkdir()
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return ledger, plan_path


def test_causal_ownership_preserves_legacy_and_reconciles(tmp_path: Path) -> None:
    ledger, plan = _fixture(tmp_path)
    result = build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[plan])

    assert result["status"] == "PASS"
    ownership = json.loads((ledger / "ownership_latest.json").read_text())
    by_owner = {
        row["sleeve_id"]: row["quantity"] for row in ownership["positions"]
    }
    assert by_owner == {
        "caerus_alpha": pytest.approx(3.0),
        "caerus_beta": pytest.approx(1.0),
        "legacy_unattributed": pytest.approx(8.0),
    }
    valuation = json.loads((ledger / "valuation_latest.json").read_text())
    assert valuation["as_of"] == "2026-08-14T23:15:00Z"
    assert sum(
        row["market_value"] for row in valuation["positions"][0]["ownership"]
    ) == pytest.approx(1224.0)
    records = [json.loads(line) for line in (ledger / "causal_fills.jsonl").read_text().splitlines()]
    assert records[0]["attribution_status"] == "LEGACY_UNATTRIBUTED"
    assert records[-1]["allocation_id"] == "allocation:test"


def test_post_cutover_unmatched_fill_fails_closed(tmp_path: Path) -> None:
    ledger, plan = _fixture(tmp_path, unmatched_after_epoch=True)
    with pytest.raises(CausalOwnershipError, match="lack exact-plan lineage"):
        build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[plan])


def test_causal_fill_history_is_immutable(tmp_path: Path) -> None:
    ledger, plan = _fixture(tmp_path)
    build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[plan])
    rows = [json.loads(line) for line in (ledger / "causal_fills.jsonl").read_text().splitlines()]
    rows[-1]["quantity"] = 99
    _write_jsonl(ledger / "causal_fills.jsonl", rows)
    with pytest.raises(CausalOwnershipError, match="append-only causal fill changed"):
        build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[plan])


def _wrap_plan(path: Path) -> Path:
    plan = json.loads(path.read_text())
    plan['as_of'] = '2026-08-14T13:35:00Z'
    plan.pop('content_hash')
    plan['content_hash'] = _hash(plan)
    path.write_text(json.dumps(plan))
    handoff = path.parent / 'handoff.json'
    handoff.write_text(json.dumps({
        'schema_version': 'caerus.authorized_execution_handoff.v1',
        'execution_lane': 'paper', 'exact_execution_plan': plan,
        'trade_date': '2026-08-14',
        'exact_execution_plan_id': plan['plan_id'],
        'exact_execution_plan_hash': plan['content_hash'],
    }))
    pointer = path.parent / 'pointer.json'
    pointer.write_text(json.dumps({
        'schema_version': 'caerus.exact_execution_plan_pointer.v1',
        'json_path': 'handoff.json', 'plan_id': plan['plan_id'],
        'plan_hash': plan['content_hash'], 'trade_date': '2026-08-14',
    }))
    return pointer


def test_raw_pointer_handoff_deduplicate(tmp_path: Path) -> None:
    from core.causal_ownership_ledger import _exact_order_index
    _, plan = _fixture(tmp_path)
    pointer = _wrap_plan(plan)
    raw = _exact_order_index([plan], plan.parent)
    assert _exact_order_index([pointer], plan.parent) == raw
    assert _exact_order_index([plan, pointer, plan.parent / 'handoff.json'], plan.parent) == raw


@pytest.mark.parametrize('mutation', ['escape', 'hash', 'id', 'date', 'handoff_date', 'nested_date', 'handoff_hash', 'nested_hash'])
def test_pointer_tamper_fails(tmp_path: Path, mutation: str) -> None:
    from core.causal_ownership_ledger import _exact_order_index
    _, plan = _fixture(tmp_path)
    pointer = _wrap_plan(plan)
    value = json.loads(pointer.read_text())
    if mutation == 'escape':
        value['json_path'] = '../outside.json'
    elif mutation == 'hash':
        value['plan_hash'] = '0' * 64
    elif mutation == 'id':
        value['plan_id'] = 'wrong'
    elif mutation == 'date':
        value['trade_date'] = '2026-08-15'
    else:
        handoff = plan.parent / 'handoff.json'
        payload = json.loads(handoff.read_text())
        if mutation == 'handoff_hash':
            payload['exact_execution_plan_hash'] = '0' * 64
        elif mutation == 'handoff_date':
            payload['trade_date'] = '2026-08-15'
        elif mutation == 'nested_date':
            nested = payload['exact_execution_plan']
            nested['trade_date'] = '2026-08-15'
            nested.pop('content_hash')
            nested['content_hash'] = _hash(nested)
            payload['exact_execution_plan_hash'] = nested['content_hash']
            value['plan_hash'] = nested['content_hash']
        else:
            payload['exact_execution_plan']['created_at'] = '2026-01-01T00:00:00Z'
        handoff.write_text(json.dumps(payload))
    pointer.write_text(json.dumps(value))
    with pytest.raises(CausalOwnershipError):
        _exact_order_index([pointer], plan.parent)


def _cutover_fixture(tmp_path: Path) -> tuple[Path, Path, bytes]:
    ledger, plan = _fixture(tmp_path)
    # Reproduce the deployed historical reader: all old fills are immutable legacy.
    build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[])
    history = (ledger / 'causal_fills.jsonl').read_bytes()
    account = json.loads((ledger / 'account_snapshots.jsonl').read_text())
    account['account_id_hash'] = 'a' * 64
    _write_jsonl(ledger / 'account_snapshots.jsonl', [account])
    positions = json.loads((ledger / 'positions_latest.json').read_text())
    contract = {
        'schema_version': 'caerus.ownership_cutover.v1', 'account_scope': 'PAPER',
        'account_id_hash': 'a' * 64, 'effective_at': positions['pulled_at_utc'],
        'history_prefix_bytes': len(history), 'history_sha256': hashlib.sha256(history).hexdigest(),
        'history_fill_count': len(history.splitlines()),
        'opening_positions_snapshot': positions, 'positions_snapshot_hash': _hash(positions),
        'opening_account_snapshot': account, 'account_snapshot_hash': _hash(account),
        'opening_book': [{'symbol': 'AAPL', 'sleeve_id': 'caerus_orion', 'quantity': 8},
                         {'symbol': 'AAPL', 'sleeve_id': 'caerus_aquila', 'quantity': 4}],
    }
    contract['content_hash'] = _hash(contract)
    (ledger / 'ownership_cutover.json').write_text(json.dumps(contract))
    return ledger, plan, history


def test_prospective_cutover_preserves_history_and_is_idempotent(tmp_path: Path) -> None:
    ledger, plan, history = _cutover_fixture(tmp_path)
    for _ in range(2):
        result = build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[plan])
        assert result['new_causal_fill_rows'] == 0
        assert (ledger / 'causal_fills.jsonl').read_bytes() == history
    owners = json.loads((ledger / 'ownership_latest.json').read_text())['positions']
    assert {r['sleeve_id']: r['quantity'] for r in owners} == {'caerus_orion': 8, 'caerus_aquila': 4}


def test_cutover_history_tamper_fails(tmp_path: Path) -> None:
    ledger, plan, _ = _cutover_fixture(tmp_path)
    path = ledger / 'causal_fills.jsonl'
    path.write_bytes(path.read_bytes().replace(b'legacy-buy', b'legacy-bad'))
    with pytest.raises(CausalOwnershipError, match='history hash mismatch'):
        build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[plan])


def test_explicit_sell_consumes_only_contributing_owner() -> None:
    from core.causal_ownership_ledger import _consume_contributors
    book = {'AAPL': {'caerus_orion': 8., 'caerus_aquila': 4.}}
    _consume_contributors(book, 'AAPL', 8, [{'sleeve_id': 'caerus_orion', 'allocation_fraction': 1.}])
    assert book == {'AAPL': {'caerus_orion': 0., 'caerus_aquila': 4.}}
    with pytest.raises(CausalOwnershipError, match='contributing owner inventory'):
        _consume_contributors(book, 'AAPL', 1, [{'sleeve_id': 'caerus_orion', 'allocation_fraction': 1.}])


@pytest.mark.parametrize('matched', [True, False])
def test_prospective_forward_sell_and_unknown_fill(tmp_path: Path, matched: bool) -> None:
    ledger, old_plan, history = _cutover_fixture(tmp_path)
    with (ledger / 'fills.csv').open(newline='') as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames, list(reader)
    row = dict(rows[-1], activity_id='forward-sell', transaction_time_utc='2026-08-15T14:00:00Z',
               side='sell', qty='8', price='102', notional='816', order_id='forward-order')
    with (ledger / 'fills.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows + [row])
    with (ledger / 'orders.jsonl').open('a') as handle:
        handle.write(json.dumps({'id': 'forward-order', 'client_order_id': 'forward-client' if matched else 'unknown'}) + '\n')
    plan = {
        'schema_version': 'caerus.execution_plan.v3', 'plan_id': 'forward-plan',
        'account_scope': 'PAPER', 'account_id_hash': 'a' * 64,
        'created_at': '2026-08-15T13:35:00Z', 'buy_orders': [],
        'sell_orders': [{'symbol': 'AAPL', 'side': 'SELL', 'client_order_id': 'forward-client',
                         'allocation_id': 'allocation:forward', 'session_id': 'session:forward',
                         'sleeve_contributions': [{'sleeve_id': 'caerus_orion', 'allocation_fraction': 1}]}],
    }
    plan['content_hash'] = _hash(plan)
    forward = old_plan.parent / 'forward.json'
    forward.write_text(json.dumps(plan))
    positions = json.loads((ledger / 'positions_latest.json').read_text())
    positions['pulled_at_utc'] = '2026-08-15T23:15:00Z'
    positions['positions'][0].update(qty='4', market_value='408')
    (ledger / 'positions_latest.json').write_text(json.dumps(positions))
    with (ledger / 'account_snapshots.jsonl').open('a') as handle:
        handle.write(json.dumps({'pulled_at_utc': positions['pulled_at_utc'], 'account_id_hash': 'a' * 64,
                                 'equity': '1424', 'cash': '1016'}) + '\n')
    if not matched:
        with pytest.raises(CausalOwnershipError, match='lack exact-plan lineage'):
            build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[old_plan, forward])
        assert (ledger / 'causal_fills.jsonl').read_bytes() == history
        return
    for _ in range(2):
        build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[old_plan, forward])
    assert (ledger / 'causal_fills.jsonl').read_bytes().startswith(history)
    owners = json.loads((ledger / 'ownership_latest.json').read_text())['positions']
    assert owners == [{'symbol': 'AAPL', 'sleeve_id': 'caerus_aquila', 'quantity': 4.0}]


def test_conflicting_client_id_fails(tmp_path: Path) -> None:
    from core.causal_ownership_ledger import _exact_order_index
    _, path = _fixture(tmp_path)
    plan = json.loads(path.read_text())
    plan.pop('content_hash')
    plan['plan_id'] = 'other-plan'
    plan['content_hash'] = _hash(plan)
    other = path.parent / 'other.json'
    other.write_text(json.dumps(plan))
    with pytest.raises(CausalOwnershipError, match='conflicting exact plans'):
        _exact_order_index([path, other], path.parent)


def test_applied_cutover_cannot_be_reassigned(tmp_path: Path) -> None:
    ledger, plan, _ = _cutover_fixture(tmp_path)
    build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[plan])
    path = ledger / 'ownership_cutover.json'
    contract = json.loads(path.read_text())
    contract.pop('content_hash')
    contract['opening_book'][0]['sleeve_id'] = 'other_sleeve'
    contract['content_hash'] = _hash(contract)
    path.write_text(json.dumps(contract))
    with pytest.raises(CausalOwnershipError, match='previously applied cutover'):
        build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[plan])
