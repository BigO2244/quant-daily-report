from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from authority.lane_exact_plan import (
    BROKER_SNAPSHOT_SCHEMA,
    artifact_content_hash,
    build_lane_exact_execution_plan,
)
from core.generic_live_v1_activation import (
    GenericLiveV1ActivationError,
    build_generic_live_v1_activation_preflight,
    recompute_generic_live_v1_activation_preflight,
    validate_generic_live_v1_activation_preflight,
)
from core.lane_allocator import allocate_lane
from core.lane_risk_authority import build_lane_risk_package
from core.lane_target_authority import build_lane_target_package
from core.sleeve_decision import build_sleeve_decision_batch, seal_sleeve_decision


ROOT = Path(__file__).resolve().parents[1]
OWNER = json.loads(
    (ROOT / "docs/evidence/generic_live_v1_lyra_owner_decision_2026-08-19.json").read_text()
)
OBSERVATION = json.loads(
    (ROOT / "docs/evidence/generic_live_account_observation_2026-08-18.json").read_text()
)
DEPLOYED = "1b397d004b4d75bbcc1a7efb0e1b2ad55613fdac"
EXPECTED = "6f9719c2d02ff5013237186950aa95eec945c290"


def _proofs(**changes: object) -> dict:
    body = {
        "deployed_sha": DEPLOYED,
        "expected_deployed_sha": EXPECTED,
        "legacy_executor_disabled": True,
        "legacy_kill_switch_armed": True,
        "generic_kill_switch_armed": True,
        "generic_schedule_installed": False,
        "generic_submission_adapter_deployed": False,
        "broker_read_preflight_green": True,
        "open_order_count": 0,
        "rollback_rearm_proven": False,
        "order_lifecycle_pipeline_green": False,
        "reconciliation_pipeline_green": False,
        "accounting_pipeline_green": False,
        "reporting_pipeline_green": False,
        "source_hashes": [hashlib.sha256(b"read-only-vm-baseline").hexdigest()],
    }
    body.update(changes)
    return body


def _decision() -> dict:
    body = {
        "schema_version": "caerus.sleeve_decision.v2",
        "trade_date": "2026-08-19",
        "session_id": "session:2026-08-19:lyra-live-v1",
        "session_hash": "a" * 64,
        "sleeve_id": "caerus_lyra",
        "outcome": "RECOMMENDATION",
        "confidence": 0.8,
        "forecast_risk": {"annualized_volatility": 0.15},
        "capacity": {"maximum_capital_usd": 100000.0},
        "expected_turnover": 0.25,
        "liquidity_status": "PASS",
        "source_method": "lyra-v2-fixture",
        "decision_grade": "READY",
        "target_rows": [{"symbol": "AAPL", "target_weight": 1.0}],
        "reason_codes": ["TEST_FIXTURE"],
        "source_artifacts": [],
        "decision_id": "sleeve-decision:v2:2026-08-19:caerus_lyra:fixture",
    }
    return seal_sleeve_decision(body)


def _plan(decision: dict) -> dict:
    batch = build_sleeve_decision_batch(
        decisions=[decision], generated_at="2026-08-19T13:29:00+00:00"
    )
    policy = {
        "lane_id": "generic-live-v1",
        "lane_kind": "LIVE",
        "enabled": True,
        "deployment_version": "deployment:generic-live-v1:fixture",
        "performance_surface": "REALIZED_LIVE_LEDGER",
        "account_id_hash": OBSERVATION["account_id_hash"],
        "broker_environment": "alpaca_live",
        "eligible_sleeves": [{
            "sleeve_id": "caerus_lyra", "minimum_weight": 1.0,
            "maximum_weight": 1.0, "initial_weight": 1.0,
            "allocation_eligible": True, "execution_eligible": True,
            "observation_enabled": True,
        }],
        "allocator_policy": {
            "allocator_id": "configured_live_v1", "allocator_version": "configured_risk_budget_v1",
            "method": "configured_risk_budget", "unavailable_policy": "fail_closed",
            "target_cash_weight": 0.05,
        },
        "risk_policy": {"policy_id": "generic-live-v1-risk"},
        "capital_policy": {
            "policy_id": "generic-live-v1-capital", "capital_basis": "FULL_ACCOUNT_EQUITY",
            "capital_ceiling": 460.0,
        },
        "execution_policy": {
            "policy_id": "generic-live-v1-execution", "order_type": "limit",
            "time_in_force": "day", "allow_extended_hours": False,
            "allow_fractional_shares": False, "quantity_precision": 0,
            "price_precision": 4, "minimum_order_notional_usd": 100.0,
            "maximum_order_notional_usd": 437.0,
            "maximum_total_buy_notional_usd": 437.0, "maximum_orders": 1,
            "max_adverse_slippage_bps": 25.0, "max_risk_age_seconds": 300,
            "max_broker_snapshot_age_seconds": 300, "max_price_age_seconds": 300,
            "plan_ttl_seconds": 120, "snapshot_reconciliation_tolerance_usd": 0.01,
        },
        "reconciliation_policy": {"policy_id": "generic-live-v1-reconciliation"},
    }
    allocation = allocate_lane(
        decision_batch=batch, lane_policy=policy,
        deployment_version=policy["deployment_version"],
        allocated_at="2026-08-19T13:29:10+00:00",
    )
    target = build_lane_target_package(
        lane_allocation=allocation, decision_batch=batch,
        sealed_at="2026-08-19T13:29:20+00:00",
    )
    snapshot = {
        "schema_version": BROKER_SNAPSHOT_SCHEMA,
        "snapshot_id": "broker-snapshot:generic-live-v1:2026-08-19:fixture",
        "trade_date": "2026-08-19", "captured_at": "2026-08-19T13:29:30+00:00",
        "account_id_hash": OBSERVATION["account_id_hash"],
        "broker_environment": "alpaca_live", "currency": "USD",
        "equity": 460.9, "cash": 460.9, "positions": [],
        "price_marks": [{"symbol": "AAPL", "price": 100.0, "as_of": "2026-08-19T13:29:30+00:00"}],
    }
    snapshot["content_hash"] = artifact_content_hash(snapshot)
    risk = build_lane_risk_package(
        lane_target_package=target, account_state_hash=snapshot["content_hash"],
        decision="APPROVE", evaluated_at="2026-08-19T13:29:40+00:00",
    )
    return build_lane_exact_execution_plan(
        lane_risk_package=risk, broker_snapshot=snapshot,
        governed_lane_policy=policy, planned_at="2026-08-19T13:29:50+00:00",
    )


def test_current_vm_facts_are_precisely_blocked_and_never_authoritative() -> None:
    result = build_generic_live_v1_activation_preflight(
        owner_decision=OWNER,
        live_account_observation=OBSERVATION,
        operational_proofs=_proofs(),
        evaluated_at="2026-08-19T02:45:00+00:00",
    )

    assert result["status"] == "BLOCKED"
    assert {
        "DEPLOYED_SHA_MISMATCH", "GENERIC_SCHEDULE_NOT_INSTALLED",
        "GENERIC_SUBMISSION_ADAPTER_NOT_DEPLOYED", "LYRA_V2_DECISION_MISSING",
        "EXACT_V4_PLAN_MISSING", "ROLLBACK_REARM_NOT_PROVEN",
        "RECONCILIATION_PIPELINE_NOT_GREEN", "ACCOUNTING_PIPELINE_NOT_GREEN",
        "REPORTING_PIPELINE_NOT_GREEN",
    }.issubset(result["reason_codes"])
    assert result["legacy_executor_reachable"] is False
    assert result["broker_write_performed"] is False
    assert result["execution_authority"] is False
    assert validate_generic_live_v1_activation_preflight(result) == result


def test_persisted_activation_preflight_is_sealed_and_blocked() -> None:
    payload = json.loads(
        (ROOT / "docs/evidence/generic_live_v1_activation_preflight_2026-08-19.json").read_text()
    )
    checked = validate_generic_live_v1_activation_preflight(payload)
    assert checked["status"] == "BLOCKED"
    assert checked["broker_write_performed"] is False


def test_every_green_gate_binds_exact_lyra_decision_and_v4_plan() -> None:
    decision = _decision()
    plan = _plan(decision)
    result = build_generic_live_v1_activation_preflight(
        owner_decision=OWNER,
        live_account_observation=OBSERVATION,
        operational_proofs=_proofs(
            deployed_sha=EXPECTED, generic_schedule_installed=True,
            generic_submission_adapter_deployed=True, rollback_rearm_proven=True,
            order_lifecycle_pipeline_green=True, reconciliation_pipeline_green=True,
            accounting_pipeline_green=True, reporting_pipeline_green=True,
        ),
        evaluated_at="2026-08-19T13:30:00+00:00",
        lyra_decision=decision,
        exact_plan=plan,
    )

    assert result["status"] == "READY_TO_DISARM_FOR_SESSION"
    assert result["reason_codes"] == ["ALL_OWNER_APPROVED_LIVE_V1_GATES_GREEN"]
    assert result["lyra_decision_hash"] == decision["content_hash"]
    assert result["exact_plan_hash"] == plan["content_hash"]
    assert result["execution_authority"] is False


def test_ready_preflight_recomputes_exactly_from_all_protected_sources() -> None:
    decision = _decision()
    plan = _plan(decision)
    proofs = _proofs(
        deployed_sha=EXPECTED, generic_schedule_installed=True,
        generic_submission_adapter_deployed=True, rollback_rearm_proven=True,
        order_lifecycle_pipeline_green=True, reconciliation_pipeline_green=True,
        accounting_pipeline_green=True, reporting_pipeline_green=True,
    )
    preflight = build_generic_live_v1_activation_preflight(
        owner_decision=OWNER, live_account_observation=OBSERVATION,
        operational_proofs=proofs, evaluated_at="2026-08-19T13:30:00+00:00",
        lyra_decision=decision, exact_plan=plan,
    )
    assert recompute_generic_live_v1_activation_preflight(
        expected_preflight=preflight, owner_decision=OWNER,
        live_account_observation=OBSERVATION, operational_proofs=proofs,
        lyra_decision=decision, exact_plan=plan,
    ) == preflight
    forged_sources = copy.deepcopy(proofs)
    forged_sources["reporting_pipeline_green"] = False
    with pytest.raises(GenericLiveV1ActivationError, match="does not exactly recompute"):
        recompute_generic_live_v1_activation_preflight(
            expected_preflight=preflight, owner_decision=OWNER,
            live_account_observation=OBSERVATION,
            operational_proofs=forged_sources,
            lyra_decision=decision, exact_plan=plan,
        )


def test_non_lyra_or_tampered_owner_scope_fails_closed() -> None:
    tampered = copy.deepcopy(OWNER)
    tampered["approved_policy_patch"]["eligible_sleeve_ids"] = ["caerus_orion"]
    tampered["content_hash"] = OWNER["content_hash"]
    with pytest.raises(Exception, match="content_hash mismatch"):
        build_generic_live_v1_activation_preflight(
            owner_decision=tampered,
            live_account_observation=OBSERVATION,
            operational_proofs=_proofs(),
            evaluated_at="2026-08-19T02:45:00+00:00",
        )


def test_fabricated_ready_status_is_rejected() -> None:
    result = build_generic_live_v1_activation_preflight(
        owner_decision=OWNER, live_account_observation=OBSERVATION,
        operational_proofs=_proofs(), evaluated_at="2026-08-19T02:45:00+00:00",
    )
    forged = copy.deepcopy(result)
    forged["status"] = "READY_TO_DISARM_FOR_SESSION"
    forged["reason_codes"] = ["ALL_OWNER_APPROVED_LIVE_V1_GATES_GREEN"]
    with pytest.raises(GenericLiveV1ActivationError, match="every gate green"):
        validate_generic_live_v1_activation_preflight(forged)
