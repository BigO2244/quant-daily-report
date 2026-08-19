from __future__ import annotations

import copy
import hashlib
import json

import pytest

from authority.lane_exact_plan import canonical_json
from core.generic_live_dynamic_account import build_generic_live_dynamic_account_observation
from core.generic_live_dynamic_operational_proofs import (
    GenericLiveDynamicOperationalProofError,
    build_generic_live_dynamic_operational_proofs,
    validate_generic_live_dynamic_operational_proofs,
)
import Tests.test_generic_live_v1_activation as activation_fixture


def _sha(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _inputs():
    account_id = "synthetic-dynamic-account"
    account_hash = hashlib.sha256(account_id.encode()).hexdigest()
    old_hash = activation_fixture.OBSERVATION["account_id_hash"]
    activation_fixture.OBSERVATION["account_id_hash"] = account_hash
    try:
        plan = activation_fixture._plan(activation_fixture._decision())
    finally:
        activation_fixture.OBSERVATION["account_id_hash"] = old_hash
    raw = json.dumps({
        "id": account_id, "status": "ACTIVE", "trading_blocked": False,
        "account_blocked": False, "equity": plan["starting_equity"],
        "cash": plan["starting_cash"],
        "long_market_value": plan["starting_equity"] - plan["starting_cash"],
        "short_market_value": 0, "pending_transfer_in": 0,
        "pending_transfer_out": 0,
    }, sort_keys=True, separators=(",", ":")).encode()
    account = build_generic_live_dynamic_account_observation(
        raw_account_response=raw, observed_at="2026-08-25T13:30:20+00:00",
    )
    pipelines = {}
    for name in ("order_lifecycle", "reconciliation", "accounting", "reporting", "rollback_rearm"):
        row = {
            "schema_version": f"caerus.generic_live_{name}_readiness.v1",
            "status": "GREEN", "account_id_hash": plan["account_id_hash"],
            "plan_hash": plan["content_hash"],
        }
        row["content_hash"] = _sha(row)
        pipelines[name] = row
    trusted = {name: row["content_hash"] for name, row in pipelines.items()}
    runtime_facts = {
        "legacy_executor_disabled": True, "legacy_kill_switch_armed": True,
        "generic_kill_switch_armed": True, "generic_schedule_installed": True,
        "generic_submission_adapter_deployed": True,
    }
    raw_runtime = canonical_json(runtime_facts).encode()
    return plan, account, raw, pipelines, trusted, raw_runtime


def _proofs():
    plan, account, raw, pipelines, trusted, raw_runtime = _inputs()
    orders = [*plan["sell_orders"], *plan["buy_orders"]]
    asset = {"symbol": orders[0]["symbol"], "status": "active", "tradable": True} if orders else None
    raw_positions = canonical_json(plan["starting_positions"]).encode()
    raw_open_orders = canonical_json([]).encode()
    raw_asset = canonical_json(asset).encode()
    proof = build_generic_live_dynamic_operational_proofs(
        generated_at="2026-08-25T13:30:40+00:00", exact_plan=plan,
        account_observation=account, raw_account_response=raw,
        positions_observed_at="2026-08-25T13:30:25+00:00",
        raw_positions_response=raw_positions,
        open_orders_observed_at="2026-08-25T13:30:28+00:00",
        raw_open_orders_response=raw_open_orders,
        asset_observed_at="2026-08-25T13:30:30+00:00", raw_asset_response=raw_asset,
        deployed_sha="a" * 40, trusted_deployed_sha="a" * 40,
        raw_runtime_evidence=raw_runtime,
        trusted_runtime_hash=hashlib.sha256(raw_runtime).hexdigest(),
        pipeline_evidence=pipelines,
        trusted_pipeline_hashes=trusted, as_of="2026-08-25T13:31:00+00:00",
    )
    return proof, plan, account, raw, raw_positions, raw_open_orders, raw_asset, raw_runtime, trusted


def test_operational_proofs_are_causally_bound_to_raw_broker_and_plan_evidence():
    proof, plan, account, raw, raw_positions, raw_open_orders, raw_asset, raw_runtime, trusted = _proofs()
    assert validate_generic_live_dynamic_operational_proofs(
        proof, exact_plan=plan, account_observation=account, raw_account_response=raw,
        raw_positions_response=raw_positions, raw_open_orders_response=raw_open_orders,
        raw_asset_response=raw_asset, raw_runtime_evidence=raw_runtime,
        trusted_deployed_sha="a" * 40,
        trusted_runtime_hash=hashlib.sha256(raw_runtime).hexdigest(),
        trusted_pipeline_hashes=trusted, as_of="2026-08-25T13:31:00+00:00",
    )["plan_hash"] == plan["content_hash"]


def test_self_resealed_all_green_or_impossible_chronology_fails_closed():
    proof, plan, account, raw, raw_positions, raw_open_orders, raw_asset, raw_runtime, trusted = _proofs()
    changed = copy.deepcopy(proof)
    changed["pipeline_evidence"]["reporting"]["content_hash"] = "f" * 64
    changed["content_hash"] = _sha({k: v for k, v in changed.items() if k != "content_hash"})
    with pytest.raises(GenericLiveDynamicOperationalProofError, match="independently pinned"):
        validate_generic_live_dynamic_operational_proofs(
            changed, exact_plan=plan, account_observation=account, raw_account_response=raw,
            raw_positions_response=raw_positions, raw_open_orders_response=raw_open_orders,
            raw_asset_response=raw_asset, raw_runtime_evidence=raw_runtime,
            trusted_deployed_sha="a" * 40,
            trusted_runtime_hash=hashlib.sha256(raw_runtime).hexdigest(),
            trusted_pipeline_hashes=trusted, as_of="2026-08-25T13:31:00+00:00",
        )
    changed = copy.deepcopy(proof)
    changed["generated_at"] = "2020-01-01T00:00:00+00:00"
    changed["content_hash"] = _sha({k: v for k, v in changed.items() if k != "content_hash"})
    with pytest.raises(GenericLiveDynamicOperationalProofError, match="generation is not current"):
        validate_generic_live_dynamic_operational_proofs(
            changed, exact_plan=plan, account_observation=account, raw_account_response=raw,
            raw_positions_response=raw_positions, raw_open_orders_response=raw_open_orders,
            raw_asset_response=raw_asset, raw_runtime_evidence=raw_runtime,
            trusted_deployed_sha="a" * 40,
            trusted_runtime_hash=hashlib.sha256(raw_runtime).hexdigest(),
            trusted_pipeline_hashes=trusted, as_of="2026-08-25T13:31:00+00:00",
        )


def test_raw_positions_open_orders_and_asset_must_match_plan():
    proof, plan, account, raw, raw_positions, raw_open_orders, raw_asset, raw_runtime, trusted = _proofs()
    for field, mutation, error in (
        ("positions_evidence", [{"symbol": "OTHER", "quantity": 1}], "positions source hash differs"),
        ("open_orders_evidence", [{"id": "open"}], "open-orders source hash differs"),
        ("asset_evidence", {"symbol": "OTHER", "status": "active", "tradable": True}, "asset source hash differs"),
    ):
        changed = copy.deepcopy(proof)
        key = "row" if field == "asset_evidence" else "rows"
        changed[field][key] = mutation
        changed["content_hash"] = _sha({k: v for k, v in changed.items() if k != "content_hash"})
        with pytest.raises(GenericLiveDynamicOperationalProofError, match=error):
            validate_generic_live_dynamic_operational_proofs(
                changed, exact_plan=plan, account_observation=account, raw_account_response=raw,
                raw_positions_response=raw_positions, raw_open_orders_response=raw_open_orders,
                raw_asset_response=raw_asset, raw_runtime_evidence=raw_runtime,
                trusted_deployed_sha="a" * 40,
                trusted_runtime_hash=hashlib.sha256(raw_runtime).hexdigest(),
                trusted_pipeline_hashes=trusted, as_of="2026-08-25T13:31:00+00:00",
            )


def test_fully_resealed_deploy_runtime_and_broker_sources_cannot_replace_trusted_bytes():
    proof, plan, account, raw, raw_positions, raw_open_orders, raw_asset, raw_runtime, trusted = _proofs()
    changed = copy.deepcopy(proof)
    changed["deployed_sha"] = "b" * 40
    changed["runtime_evidence"]["source_hash"] = "f" * 64
    changed["content_hash"] = _sha({k: v for k, v in changed.items() if k != "content_hash"})
    with pytest.raises(GenericLiveDynamicOperationalProofError, match="protected pin"):
        validate_generic_live_dynamic_operational_proofs(
            changed, exact_plan=plan, account_observation=account, raw_account_response=raw,
            raw_positions_response=raw_positions, raw_open_orders_response=raw_open_orders,
            raw_asset_response=raw_asset, raw_runtime_evidence=raw_runtime,
            trusted_deployed_sha="a" * 40,
            trusted_runtime_hash=hashlib.sha256(raw_runtime).hexdigest(),
            trusted_pipeline_hashes=trusted, as_of="2026-08-25T13:31:00+00:00",
        )
