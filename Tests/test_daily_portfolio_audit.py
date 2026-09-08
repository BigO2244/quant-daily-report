from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from core.daily_portfolio_audit import (
    DailyPortfolioAuditError,
    build_daily_portfolio_audit,
)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[str, Path]:
    trade_date = "2026-08-14"
    bundle = tmp_path / "outputs" / "precompute" / trade_date
    session = {"session_id": "session:test", "content_hash": "session-hash"}
    decisions = {"session_hash": "session-hash"}
    allocation = {"allocation_id": "allocation:test", "content_hash": "allocation-hash"}
    package = {
        "session_id": "session:test",
        "allocation_id": "allocation:test",
        "allocation_content_hash": "allocation-hash",
        "approved_target_hash": "target-hash",
    }
    _write(bundle / "session_manifest.json", session)
    _write(bundle / "sleeve_decisions.json", decisions)
    _write(bundle / "portfolio_allocation.json", allocation)
    _write(bundle / "paper_target_package.json", package)
    source_hashes = {
        name: hashlib.sha256((bundle / filename).read_bytes()).hexdigest()
        for name, filename in {
            "session": "session_manifest.json",
            "decisions": "sleeve_decisions.json",
            "allocation": "portfolio_allocation.json",
            "target": "paper_target_package.json",
        }.items()
    }
    plan_path = (
        tmp_path
        / "outputs"
        / "paper_lane"
        / "plans"
        / f"exact_execution_plan_{trade_date}.json"
    )
    plan = {
        "schema_version": "caerus.execution_plan.v3",
        "plan_id": "plan:test",
        "run_id": "run:test",
        "source_artifact_hashes": source_hashes,
        "sell_orders": [],
        "buy_orders": [],
    }
    plan["content_hash"] = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    _write(plan_path, plan)
    _write(
        tmp_path / "outputs" / "workflow" / trade_date / "execution.json",
        {"status": "success", "run_id": "run:test"},
    )
    as_of = "2026-08-14T23:15:00Z"
    _write(
        tmp_path / "outputs" / "ledger" / "paper" / "ownership_latest.json",
        {"as_of": as_of, "reconciliation": {"status": "PASS"}},
    )
    _write(
        tmp_path / "outputs" / "ledger" / "paper" / "valuation_latest.json",
        {"as_of": as_of, "reconciliation": {"status": "PASS"}},
    )
    _write(
        tmp_path / "outputs" / "portfolio_history" / "reporting_snapshot.json",
        {"as_of": as_of, "report_date": trade_date, "status": "PASS"},
    )
    return trade_date, plan_path


def test_daily_audit_closes_full_decision_to_report_chain(tmp_path: Path) -> None:
    trade_date, _ = _fixture(tmp_path)
    result = build_daily_portfolio_audit(repo_root=tmp_path, trade_date=trade_date)
    assert result["status"] == "PASS"
    assert result["checks"] == {
        "decision_to_execution": "PASS",
        "execution_to_ownership": "PASS",
        "ownership_to_valuation": "PASS",
        "valuation_to_reporting": "PASS",
        "single_as_of": "PASS",
    }
    assert (
        tmp_path / "outputs" / "audit" / trade_date / "portfolio_audit.json"
    ).is_file()


def test_daily_audit_rejects_mixed_reporting_time(tmp_path: Path) -> None:
    trade_date, _ = _fixture(tmp_path)
    reporting = (
        tmp_path / "outputs" / "portfolio_history" / "reporting_snapshot.json"
    )
    _write(
        reporting,
        {
            "as_of": "2026-08-14T22:00:00Z",
            "report_date": trade_date,
            "status": "PASS",
        },
    )
    with pytest.raises(DailyPortfolioAuditError, match="reporting_as_of_mismatch"):
        build_daily_portfolio_audit(repo_root=tmp_path, trade_date=trade_date)


def test_daily_audit_rejects_tampered_exact_plan(tmp_path: Path) -> None:
    trade_date, plan_path = _fixture(tmp_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["plan_id"] = "plan:tampered"
    _write(plan_path, plan)
    with pytest.raises(DailyPortfolioAuditError, match="exact_plan_content_hash_invalid"):
        build_daily_portfolio_audit(repo_root=tmp_path, trade_date=trade_date)


def _convert_to_canonical_handoff(tmp_path: Path, trade_date: str, plan_path: Path) -> Path:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["run_id"] = "run:authority"
    body = dict(plan)
    body.pop("content_hash")
    plan["content_hash"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    handoff_path = (
        tmp_path
        / "outputs"
        / "paper_lane"
        / "plans"
        / "authority"
        / trade_date
        / "plan_authorized.json"
    )
    _write(
        handoff_path,
        {
            "schema_version": "caerus.authorized_execution_handoff.v1",
            "trade_date": trade_date,
            "exact_execution_plan": plan,
            "exact_execution_plan_hash": plan["content_hash"],
            "exact_execution_plan_id": plan["plan_id"],
            "exact_execution_authority_run_id": plan["run_id"],
        },
    )
    _write(
        plan_path,
        {
            "schema_version": "caerus.exact_execution_plan_pointer.v1",
            "trade_date": trade_date,
            "plan_id": plan["plan_id"],
            "plan_hash": plan["content_hash"],
            "json_path": str(handoff_path.relative_to(tmp_path)),
        },
    )
    execution_run_id = "run:submit"
    run_root = tmp_path / "outputs" / "paper_lane" / "runs" / execution_run_id
    _write(
        run_root / "execution_results.json",
        {
            "run_id": execution_run_id,
            "plan_id_received": plan["plan_id"],
            "plan_hash_received": plan["content_hash"],
            "plan_hash_validated": True,
            "authorization_validated": True,
        },
    )
    _write(
        tmp_path / "outputs" / "workflow" / trade_date / "execution.json",
        {
            "status": "success",
            "run_id": execution_run_id,
            "run_root": str(run_root.relative_to(tmp_path)),
        },
    )
    return handoff_path


def test_daily_audit_resolves_canonical_pointer_handoff_and_execution_result(
    tmp_path: Path,
) -> None:
    trade_date, plan_path = _fixture(tmp_path)
    handoff_path = _convert_to_canonical_handoff(tmp_path, trade_date, plan_path)

    result = build_daily_portfolio_audit(repo_root=tmp_path, trade_date=trade_date)

    assert result["status"] == "PASS"
    assert result["sources"]["exact_execution_plan_pointer"]["path"] == str(
        plan_path.relative_to(tmp_path)
    )
    assert result["sources"]["exact_execution_handoff"]["path"] == str(
        handoff_path.relative_to(tmp_path)
    )
    assert "execution_result" in result["sources"]


def test_daily_audit_rejects_pointer_outside_authority_boundary(tmp_path: Path) -> None:
    trade_date, plan_path = _fixture(tmp_path)
    _convert_to_canonical_handoff(tmp_path, trade_date, plan_path)
    pointer = json.loads(plan_path.read_text(encoding="utf-8"))
    pointer["json_path"] = "outputs/workflow/escape.json"
    _write(plan_path, pointer)

    with pytest.raises(DailyPortfolioAuditError, match="path_outside_boundary"):
        build_daily_portfolio_audit(repo_root=tmp_path, trade_date=trade_date)


def test_daily_audit_rejects_pointer_plan_identity_mismatch(tmp_path: Path) -> None:
    trade_date, plan_path = _fixture(tmp_path)
    _convert_to_canonical_handoff(tmp_path, trade_date, plan_path)
    pointer = json.loads(plan_path.read_text(encoding="utf-8"))
    pointer["plan_id"] = "plan:tampered"
    _write(plan_path, pointer)

    with pytest.raises(DailyPortfolioAuditError, match="pointer_identity_mismatch"):
        build_daily_portfolio_audit(repo_root=tmp_path, trade_date=trade_date)


def test_daily_audit_rejects_execution_result_plan_hash_mismatch(tmp_path: Path) -> None:
    trade_date, plan_path = _fixture(tmp_path)
    _convert_to_canonical_handoff(tmp_path, trade_date, plan_path)
    execution = json.loads(
        (tmp_path / "outputs" / "workflow" / trade_date / "execution.json").read_text(
            encoding="utf-8"
        )
    )
    result_path = tmp_path / execution["run_root"] / "execution_results.json"
    execution_result = json.loads(result_path.read_text(encoding="utf-8"))
    execution_result["plan_hash_received"] = "tampered"
    _write(result_path, execution_result)

    with pytest.raises(
        DailyPortfolioAuditError, match="exact_plan_execution_plan_hash_mismatch"
    ):
        build_daily_portfolio_audit(repo_root=tmp_path, trade_date=trade_date)


def _prospective_fixture(root: Path):
    from core.daily_portfolio_audit import _content_hash
    import csv
    ledger = root / 'outputs/ledger/paper'
    ledger.mkdir(parents=True)
    row = {'activity_id': 'old', 'symbol': 'AAPL', 'side': 'buy', 'quantity': 10,
           'broker_order_id': 'old-order', 'transaction_time_utc': '2026-08-13T14:00:00Z',
           'inventory_effects': [{'sleeve_id': 'legacy_unattributed', 'signed_quantity': 10}]}
    row['record_hash'] = _content_hash(row)
    raw = (json.dumps(row) + '\n').encode()
    (ledger / 'causal_fills.jsonl').write_bytes(raw)
    with (ledger / 'fills.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['activity_id', 'symbol', 'side', 'qty', 'order_id', 'transaction_time_utc'])
        writer.writeheader()
        writer.writerow({'activity_id': 'old', 'symbol': 'AAPL', 'side': 'buy', 'qty': 10,
                         'order_id': 'old-order', 'transaction_time_utc': row['transaction_time_utc']})
    (ledger / 'orders.jsonl').write_text('')
    as_of = '2026-08-14T23:15:00Z'
    positions = {'pulled_at_utc': as_of, 'positions': [{'symbol': 'AAPL', 'qty': 10}]}
    account = {'pulled_at_utc': as_of, 'account_id_hash': 'a' * 64}
    _write(ledger / 'positions_latest.json', positions)
    (ledger / 'account_snapshots.jsonl').write_text(json.dumps(account) + '\n')
    book = [{'symbol': 'AAPL', 'sleeve_id': 'caerus_orion', 'quantity': 6},
            {'symbol': 'AAPL', 'sleeve_id': 'caerus_aquila', 'quantity': 4}]
    contract = {'schema_version': 'caerus.ownership_cutover.v1', 'account_scope': 'PAPER',
                'account_id_hash': 'a' * 64, 'effective_at': as_of,
                'history_prefix_bytes': len(raw), 'history_sha256': hashlib.sha256(raw).hexdigest(),
                'history_fill_count': 1, 'opening_positions_snapshot': positions,
                'positions_snapshot_hash': _content_hash(positions), 'opening_account_snapshot': account,
                'account_snapshot_hash': _content_hash(account), 'opening_book': book}
    contract['content_hash'] = _content_hash(contract)
    _write(ledger / 'ownership_cutover.json', contract)
    ownership = {'as_of': as_of, 'account_id_hash': 'a' * 64,
                 'opening_contract_hash': contract['content_hash'], 'positions': book}
    ownership['content_hash'] = _content_hash(ownership)
    return ledger, ownership


def test_independent_cutover_audit_preserves_legacy_opening(tmp_path):
    from core.daily_portfolio_audit import _audit_cutover_ownership
    ledger, ownership = _prospective_fixture(tmp_path)
    before = (ledger / 'causal_fills.jsonl').read_bytes()
    assert 'ownership_cutover' in _audit_cutover_ownership(tmp_path, ownership)
    assert (ledger / 'causal_fills.jsonl').read_bytes() == before


@pytest.mark.parametrize('mutation', ['owner', 'history', 'account', 'broker'])
def test_independent_cutover_audit_rejects_forged_balanced_ownership(tmp_path, mutation):
    from core.daily_portfolio_audit import _audit_cutover_ownership, _content_hash
    ledger, ownership = _prospective_fixture(tmp_path)
    if mutation == 'owner':
        ownership['positions'][0]['quantity'] = 5
        ownership['positions'][1]['quantity'] = 5
        ownership.pop('content_hash')
        ownership['content_hash'] = _content_hash(ownership)
    elif mutation == 'history':
        path = ledger / 'causal_fills.jsonl'
        path.write_bytes(path.read_bytes().replace(b'legacy_unattributed', b'caerus_orion'))
    elif mutation == 'account':
        (ledger / 'account_snapshots.jsonl').write_text(json.dumps({'pulled_at_utc': ownership['as_of'], 'account_id_hash': 'b' * 64}) + '\n')
    else:
        _write(ledger / 'positions_latest.json', {'pulled_at_utc': ownership['as_of'], 'positions': [{'symbol': 'AAPL', 'qty': 11}]})
    with pytest.raises(DailyPortfolioAuditError, match='ownership_cutover_'):
        _audit_cutover_ownership(tmp_path, ownership)


@pytest.mark.parametrize('wrong_owner', [False, True])
def test_independent_forward_sell_replay_preserves_aquila(tmp_path, wrong_owner):
    from core.daily_portfolio_audit import _audit_cutover_ownership, _content_hash
    ledger, ownership = _prospective_fixture(tmp_path)
    plan = {'schema_version': 'caerus.execution_plan.v3', 'plan_id': 'forward',
            'account_scope': 'PAPER', 'account_id_hash': 'a' * 64,
            'sell_orders': [{'client_order_id': 'client-forward', 'symbol': 'AAPL', 'side': 'SELL',
                             'allocation_id': 'allocation-forward',
                             'sleeve_contributions': [{'sleeve_id': 'caerus_orion', 'allocation_fraction': 1}]}]}
    plan['content_hash'] = _content_hash(plan)
    _write(tmp_path / 'outputs/paper_lane/plans/forward.json', plan)
    row = {'activity_id': 'new', 'symbol': 'AAPL', 'side': 'sell', 'quantity': 2,
           'broker_order_id': 'new-order', 'client_order_id': 'client-forward',
           'transaction_time_utc': '2026-08-15T14:00:00Z', 'attribution_status': 'ATTRIBUTED',
           'plan_hash': plan['content_hash'], 'plan_id': 'forward', 'allocation_id': 'allocation-forward',
           'inventory_effects': [{'sleeve_id': 'caerus_aquila' if wrong_owner else 'caerus_orion', 'signed_quantity': -2}]}
    row['record_hash'] = _content_hash(row)
    with (ledger / 'causal_fills.jsonl').open('a') as handle:
        handle.write(json.dumps(row) + '\n')
    with (ledger / 'fills.csv').open('a') as handle:
        handle.write('new,AAPL,sell,2,new-order,2026-08-15T14:00:00Z\n')
    (ledger / 'orders.jsonl').write_text(json.dumps({'id': 'new-order', 'client_order_id': 'client-forward'}) + '\n')
    ownership.pop('content_hash')
    ownership['as_of'] = '2026-08-15T23:15:00Z'
    ownership['positions'][0]['quantity'] = 4
    ownership['content_hash'] = _content_hash(ownership)
    _write(ledger / 'positions_latest.json', {'pulled_at_utc': ownership['as_of'], 'positions': [{'symbol': 'AAPL', 'qty': 8}]})
    with (ledger / 'account_snapshots.jsonl').open('a') as handle:
        handle.write(json.dumps({'pulled_at_utc': ownership['as_of'], 'account_id_hash': 'a' * 64}) + '\n')
    if wrong_owner:
        with pytest.raises(DailyPortfolioAuditError, match='forward_effect_mismatch'):
            _audit_cutover_ownership(tmp_path, ownership)
    else:
        assert _audit_cutover_ownership(tmp_path, ownership)
