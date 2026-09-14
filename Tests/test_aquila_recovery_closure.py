from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.aquila_recovery_closure import (
    AquilaRecoveryClosureError,
    covered_parent_plan_hashes,
)
from core.portfolio_operating_model import content_hash
from authority.exact_plan import compute_starting_state_hash
from scripts.build_aquila_daily_source import AquilaContractError, _monthly_state


DAY = "2026-09-11"
EPOCH = "2026-09-11T1045ET"
ACCOUNT = "a" * 64
P0 = "1" * 64
P1 = "2" * 64
P2 = "3" * 64


def _write(path: Path, payload: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sealed(payload: dict) -> dict:
    body = dict(payload)
    body["content_hash"] = content_hash(body)
    return body


def _fixture(root: Path) -> dict:
    intermediate_positions, intermediate_cash = [{"symbol": "P0", "quantity": 1}], 10.0
    final_positions, final_cash = [{"symbol": "P1", "quantity": 2}], 20.0
    intermediate_hash = compute_starting_state_hash(intermediate_positions, intermediate_cash)
    final_hash = compute_starting_state_hash(final_positions, final_cash)
    parent_rows = []
    for index, digest in enumerate((P0, P1)):
        namespace = "canonical" if index == 0 else "2026-09-11T1005ET"
        client_ids = [f"p{index}-{n}" for n in range(5 if index == 0 else 3)]
        rel = f"outputs/paper_lane/plans/authority/{DAY}/p{index}.json"
        envelope_path = root / rel
        envelope_sha = _write(
            envelope_path,
            {
                "exact_execution_plan": {
                    "account_id_hash": ACCOUNT,
                    "plan_id": f"plan-{index}",
                    "content_hash": digest,
                    "starting_state_hash": "start-0" if index == 0 else intermediate_hash,
                },
                "exact_execution_plan_hash": digest,
                "trade_date": DAY,
            },
        )
        parent_rows.append(
            {
                "exact_plan_path": rel,
                "exact_plan_file_sha256": envelope_sha,
                "plan_id": f"plan-{index}",
                "plan_hash": digest,
                "wal_namespace": namespace,
                "expected_client_order_ids": client_ids,
            }
        )
    p0_refs = []
    for client_id in parent_rows[0]["expected_client_order_ids"]:
        intent = root / f"outputs/paper_lane/submission_wal/{DAY}/intents/{client_id}.json"
        resolution = root / f"outputs/paper_lane/submission_wal/{DAY}/resolutions/{client_id}/r.json"
        p0_refs.append(
            {
                "intent_path": str(intent.relative_to(root)),
                "intent_sha256": _write(intent, {"client_order_id": client_id}),
                "resolutions": [
                    {"path": str(resolution.relative_to(root)), "sha256": _write(resolution, {"client": client_id})}
                ],
            }
        )
    p1_refs = []
    for client_id in parent_rows[1]["expected_client_order_ids"]:
        intent = root / f"outputs/paper_lane/submission_wal/epochs/2026-09-11T1005ET/{DAY}/intents/{client_id}.json"
        resolution = root / f"outputs/paper_lane/submission_wal/epochs/2026-09-11T1005ET/{DAY}/resolutions/{client_id}/r.json"
        p1_refs.append(
            {
                "intent_path": str(intent.relative_to(root)),
                "intent_sha256": _write(intent, {"client_order_id": client_id}),
                "resolutions": [
                    {"path": str(resolution.relative_to(root)), "sha256": _write(resolution, {"client": client_id})}
                ],
            }
        )
    p0_proof = {
        "reconciliation_status": "TERMINAL_FAILURE_STATE_RECONCILED", "plan_hash": P0, "plan_id": "plan-0",
        "starting_state_hash": "start-0", "final_state_hash": intermediate_hash,
        "final_positions": intermediate_positions, "final_cash": intermediate_cash,
    }
    p0_bridge = _sealed({
        "original_plan_hash": P0, "original_plan_id": "plan-0", "trade_date": DAY, "account_id_hash": ACCOUNT,
        "economic_proof": p0_proof, "economic_proof_hash": content_hash(p0_proof),
        "current_state_hash": intermediate_hash, "current_positions": intermediate_positions, "current_cash": intermediate_cash,
        "frozen_ownership_hash": "frozen", "frozen_ownership_sha256": "frozen-sha", "wal_files": p0_refs,
    })
    proof = {
        "reconciliation_status": "TERMINAL_FAILURE_STATE_RECONCILED", "plan_hash": P1, "plan_id": "plan-1",
        "paper_drill_epoch": "2026-09-11T1005ET", "starting_state_hash": intermediate_hash,
        "final_state_hash": final_hash, "final_positions": final_positions, "final_cash": final_cash,
        "broker_fills": [{"client_order_id": client} for client in parent_rows[1]["expected_client_order_ids"]],
    }
    quantity_contract = _sealed({"ownership_snapshot_hash": "frozen", "ownership_snapshot_sha256": "frozen-sha"})
    bridge = _sealed(
        {
            "schema_version": "caerus.aquila_recovery_chain.v1",
            "epoch": EPOCH,
            "trade_date": DAY,
            "account_id_hash": ACCOUNT,
            "approved_target_hash": "target",
            "parents": parent_rows,
            "p0_bridge": p0_bridge,
            "p1_economic_proof": proof,
            "p1_economic_proof_hash": content_hash(proof),
            "p1_wal_files": p1_refs,
            "p1_applied_fills": proof["broker_fills"],
            "current_state_hash": final_hash, "current_positions": final_positions, "current_cash": final_cash,
            "quantity_contract_hash": quantity_contract["content_hash"],
            "frozen_ownership_hash": "frozen", "frozen_ownership_sha256": "frozen-sha",
            "original_plans_incomplete": True,
        }
    )
    policy = {
        "schema_version": "caerus.paper_intraday_drill_policy.v1",
        "trade_date": DAY,
        "paper_only": True,
        "live_eligible": False,
        "allowed_epochs": [EPOCH],
        "safety_contract": {
            "account_date_mutex_remains_global": True,
            "epoch_reuse_is_idempotent_only": True,
            "unresolved_prior_epoch_blocks_submission": True,
            "wal_and_claim_records_are_append_only": True,
            "normal_hours_market_orders_after_close_prohibited": True,
        },
        "ownership_bridge_chain": {"approved_target_hash": "target", "parents": parent_rows},
    }
    _write(root / f"config/paper_intraday_drill_policy_{DAY}.json", policy)
    return {
        "trade_date": DAY,
        "account_id_hash": ACCOUNT,
        "starting_state_hash": final_hash,
        "constraints": {
            "paper_drill_epoch": EPOCH,
            "paper_drill_live_eligible": False,
            "aquila_quantity_authority": {
                "recovery_ownership_bridge": bridge, "quantity_contract": quantity_contract,
                "ownership_snapshot_sha256": "frozen-sha",
            },
        },
    }


def test_successor_chain_closes_only_its_two_hash_bound_parents(tmp_path: Path) -> None:
    plan = _fixture(tmp_path)
    assert covered_parent_plan_hashes(repo_root=tmp_path, successful_plan=plan, trade_date="2026-09-11") == frozenset({P0, P1})


def test_tampered_parent_or_wal_artifact_cannot_close_a_failure(tmp_path: Path) -> None:
    plan = _fixture(tmp_path)
    path = tmp_path / "outputs/paper_lane/plans/authority/2026-09-11/p1.json"
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(AquilaRecoveryClosureError, match="envelope hash"):
        covered_parent_plan_hashes(repo_root=tmp_path, successful_plan=plan, trade_date="2026-09-11")


def test_unbound_or_malformed_successor_bridge_is_not_a_closure(tmp_path: Path) -> None:
    plan = _fixture(tmp_path)
    bridge = plan["constraints"]["aquila_quantity_authority"]["recovery_ownership_bridge"]
    bridge["parents"] = bridge["parents"][:1]
    with pytest.raises(AquilaRecoveryClosureError):
        covered_parent_plan_hashes(repo_root=tmp_path, successful_plan=plan, trade_date="2026-09-11")


def test_failure_after_successor_is_not_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A successor bridge cannot erase a later failed execution attempt."""
    successor = _fixture(tmp_path)
    successor.update({"content_hash": P2, "plan_id": "successor", "account_scope": "PAPER"})
    successor["constraints"]["aquila_quantity_authority"]["quantity_contract"].update({
        "action": "MONTHLY_REBALANCE",
        "formation_id": "f",
        "formation_hash": "h",
        "formation_session": DAY,
    })
    quantity = successor["constraints"]["aquila_quantity_authority"]["quantity_contract"]
    quantity["content_hash"] = content_hash({key: value for key, value in quantity.items() if key != "content_hash"})
    bridge = successor["constraints"]["aquila_quantity_authority"]["recovery_ownership_bridge"]
    bridge["quantity_contract_hash"] = quantity["content_hash"]
    bridge["content_hash"] = content_hash({key: value for key, value in bridge.items() if key != "content_hash"})
    failed = {
        "content_hash": P0,
        "plan_id": "failed",
        "account_id_hash": ACCOUNT,
        "account_scope": "PAPER",
        "constraints": {"aquila_quantity_authority": {"quantity_contract": {"action": "HOLD_NO_REBALANCE"}}},
    }

    def write_run(name: str, plan: dict, stamp: str, *, success: bool) -> None:
        directory = tmp_path / "outputs/paper_lane/runs" / name
        _write(directory / "execution_payload.json", {
            "exact_execution_plan": plan, "exact_execution_plan_hash": plan["content_hash"],
            "mode": "PAPER", "trade_date": DAY, "generated_at": stamp, "run_id": name,
        })
        _write(directory / "execution_results.json", {
            "status": "SUBMITTED" if success else "FAILED", "mode": "PAPER", "trade_date": DAY, "run_id": name,
        })
        _write(directory / "live_pilot_operator_summary.json", {
            "terminal_outcome": "RECONCILED_SUCCESS" if success else "FAILED", "plan_hash_received": plan["content_hash"],
            "plan_id_received": plan["plan_id"], "plan_hash_validated": True, "authorization_validated": True,
            "dry_run": False, "mode": "PAPER", "trade_date": DAY, "run_id": name,
        })

    write_run("success", successor, "2026-09-11T15:54:00+00:00", success=True)
    write_run("later_failure", failed, "2026-09-11T16:00:00+00:00", success=False)
    monkeypatch.setattr(
        "authority.exact_plan.exact_execution_plan_from_dict",
        lambda plan, expected_account_scope: SimpleNamespace(
            content_hash=plan["content_hash"], plan_id=plan["plan_id"],
            account_id_hash=plan["account_id_hash"], trade_date=DAY,
        ),
    )
    with pytest.raises(AquilaContractError, match="unresolved Aquila execution"):
        _monthly_state(
            tmp_path,
            {"account_id_hash": ACCOUNT, "as_of": "2026-09-11T17:00:00+00:00"},
            {},
            "2026-09-14T12:00:00+00:00",
        )


@pytest.mark.parametrize('mutation', ['account', 'epoch', 'empty_wal', 'proof_identity', 'proof_hash', 'wal_bytes', 'resolution_bytes', 'extra_resolution', 'extra_intent', 'missing_policy', 'policy_parent', 'p0_status', 'p0_start', 'p0_final', 'p1_status', 'p1_final', 'p1_applied_fills', 'quantity_binding'])
def test_each_recovery_proof_boundary_fails_closed(tmp_path, mutation):
    plan = _fixture(tmp_path)
    bridge = plan['constraints']['aquila_quantity_authority']['recovery_ownership_bridge']
    ref = bridge['p1_wal_files'][0]
    policy_path = tmp_path / f'config/paper_intraday_drill_policy_{DAY}.json'
    if mutation == 'account': bridge['account_id_hash'] = 'b'*64
    elif mutation == 'epoch': bridge['epoch'] = '2026-09-11T1100ET'
    elif mutation == 'empty_wal': bridge['p1_wal_files'] = []
    elif mutation == 'proof_identity':
        bridge['p1_economic_proof']['plan_hash'] = 'f'*64
        bridge['p1_economic_proof_hash'] = content_hash(bridge['p1_economic_proof'])
    elif mutation == 'proof_hash': bridge['p1_economic_proof_hash'] = 'f'*64
    elif mutation == 'wal_bytes': (tmp_path / ref['intent_path']).write_text('{}')
    elif mutation == 'resolution_bytes': (tmp_path / ref['resolutions'][0]['path']).write_text('{}')
    elif mutation == 'extra_resolution': _write((tmp_path / ref['resolutions'][0]['path']).with_name('extra.json'), {})
    elif mutation == 'extra_intent': _write((tmp_path / ref['intent_path']).with_name('extra.json'), {'client_order_id':'extra'})
    elif mutation == 'missing_policy': policy_path.unlink()
    elif mutation == 'policy_parent':
        policy = json.loads(policy_path.read_text());policy['ownership_bridge_chain']['parents'][0]['plan_hash'] = 'f'*64;_write(policy_path, policy)
    elif mutation in {'p0_status', 'p0_start', 'p0_final'}:
        proof = bridge['p0_bridge']['economic_proof']
        if mutation == 'p0_status': proof['reconciliation_status'] = 'UNRECONCILED'
        elif mutation == 'p0_start': proof['starting_state_hash'] = 'f'*64
        else: proof['final_state_hash'] = 'f'*64
        bridge['p0_bridge']['economic_proof_hash'] = content_hash(proof)
        bridge['p0_bridge']['content_hash'] = content_hash({k:v for k,v in bridge['p0_bridge'].items() if k != 'content_hash'})
    elif mutation in {'p1_status', 'p1_final'}:
        if mutation == 'p1_status': bridge['p1_economic_proof']['reconciliation_status'] = 'UNRECONCILED'
        else: bridge['p1_economic_proof']['final_state_hash'] = 'f'*64
        bridge['p1_economic_proof_hash'] = content_hash(bridge['p1_economic_proof'])
    elif mutation == 'p1_applied_fills': bridge['p1_applied_fills'] = []
    elif mutation == 'quantity_binding': bridge['quantity_contract_hash'] = 'f'*64
    # Recompute only the outer hash so semantic checks, not merely checksum checks, are exercised.
    bridge.pop('content_hash');bridge['content_hash'] = content_hash(bridge)
    with pytest.raises(AquilaRecoveryClosureError):
        covered_parent_plan_hashes(repo_root=tmp_path, successful_plan=plan, trade_date=DAY)
