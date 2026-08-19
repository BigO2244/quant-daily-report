from __future__ import annotations

import copy
import datetime as dt
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
    require_generic_live_v1_owner_current_at_execution,
    validate_generic_live_v1_activation_preflight,
)
from core.generic_lyra_v2_producer import (
    GENERIC_LYRA_CAPTURE_RESULT_SCHEMA,
    GENERIC_LYRA_SOURCE_METHOD,
    build_generic_lyra_v2_readiness,
    validate_generic_lyra_v2_capture_result,
)
from core.lyra_governed_evidence import (
    CAPACITY_FORMULA,
    LIQUIDITY_FORMULA,
    RISK_FORMULA,
    TURNOVER_FORMULA,
    LYRA_RISK_POLICY_SCHEMA,
    LYRA_RISK_POLICY_PROPOSAL_SCHEMA,
    LYRA_RISK_POLICY_OWNER_DECISION_SCHEMA,
    build_lyra_capacity_evidence,
    build_lyra_forecast_risk_evidence,
    build_lyra_governed_session_snapshot,
    build_lyra_liquidity_evidence,
    build_lyra_market_data_snapshot,
    governed_evidence_source_artifacts,
)
from core.lyra_target_selection import build_lyra_target_selection_evidence
from core.lane_allocator import allocate_lane
from core.lane_risk_authority import build_lane_risk_package
from core.lane_target_authority import build_lane_target_package
from core.sleeve_decision import build_sleeve_decision_batch, seal_sleeve_decision
from core.governed_xnys_calendar import previous_xnys_session
from core.generic_lyra_v2_raw_sources import (
    GENERIC_LYRA_RAW_RECOMPUTE_SCHEMA,
    GENERIC_LYRA_RAW_SOURCE_NAMES,
    validate_generic_lyra_v2_raw_source_recompute,
)


ROOT = Path(__file__).resolve().parents[1]
OWNER = json.loads(
    (ROOT / "docs/evidence/generic_live_v1_lyra_owner_decision_2026-08-19.json").read_text()
)
OWNER = copy.deepcopy(OWNER)
OWNER["effective_session"] = "2026-08-25"
OWNER["expires_at"] = "2026-08-26T20:00:00+00:00"
OWNER["content_hash"] = artifact_content_hash(OWNER)
OBSERVATION = json.loads(
    (ROOT / "docs/evidence/generic_live_account_observation_2026-08-18.json").read_text()
)
# Execution-path fixtures model an account already holding one stale Lyra lot,
# leaving exactly one owner-compliant SELL for lifecycle tests.
OBSERVATION = copy.deepcopy(OBSERVATION)
OBSERVATION["cash"] = "60.90"
OBSERVATION["content_hash"] = artifact_content_hash(OBSERVATION)
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


def _capture() -> dict:
    symbols = ["AAPL", "GOOG", "META", "MSFT", "NVDA"]
    target_rows = [{"symbol": symbol, "target_weight": 0.2} for symbol in symbols]
    freeze = {
        "schema_version": "caerus.governed_universe_freeze.v1",
        "freeze_id": "governed-universe:lyra-live-v1:2026-08-18:test",
        "generated_at": "2026-08-18T00:00:00+00:00",
        "effective_from": "2026-08-18T00:00:00+00:00",
        "no_retroactive_use_before": "2026-08-18",
        "source_path": "data/universe.csv", "source_revision": "1" * 40,
        "source_sha256": "1" * 64, "member_count": len(symbols),
        "ordered_members_sha256": hashlib.sha256(
            json.dumps(symbols, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "membership_economics_changed": False, "prospective_only": True,
        "execution_authority": False,
    }
    freeze["content_hash"] = artifact_content_hash(freeze)
    dates = ["2026-08-24"]
    while len(dates) < 253:
        dates.append(previous_xnys_session(dates[-1]))
    dates.reverse()
    full_rows = [{
        "date": day, "ticker": symbol,
        "close": 100.0 * ((1.0 + 0.003 - index * 0.0001) ** day_index),
        "volume": 2_000_000.0 + index * 10_000,
    } for day_index, day in enumerate(dates) for index, symbol in enumerate(symbols)]
    selection = build_lyra_target_selection_evidence(
        execution_session="2026-08-25", signal_as_of="2026-08-24",
        captured_at="2026-08-25T12:55:00+00:00",
        source_path="outputs/research/flow_detection_v1/price_panel.parquet",
        source_sha256="1" * 64, universe_freeze_hash=freeze["content_hash"],
        universe_source_hash="1" * 64, frozen_universe_symbols=symbols,
        price_rows=full_rows,
    )
    market = build_lyra_market_data_snapshot(
        trade_date="2026-08-25", data_as_of="2026-08-24",
        captured_at="2026-08-25T12:55:00+00:00",
        source_path="outputs/research/flow_detection_v1/price_panel.parquet",
        source_sha256="1" * 64, required_symbols=symbols,
        price_rows=full_rows,
    )
    risk_policy_proposal = {
        "schema_version": LYRA_RISK_POLICY_PROPOSAL_SCHEMA,
        "proposal_id": "lyra-risk-policy-proposal:activation-test-v1",
        "proposed_at": "2026-08-18T22:00:00+00:00",
        "proposed_by": "CAERUS_OPERATING_MODEL_MIGRATION",
        "policy_terms": {
            "sleeve_id": "caerus_lyra", "metric": "annualized_volatility",
            "formula_id": RISK_FORMULA, "lookback_sessions": 20,
            "minimum_price_observations": 21, "annualization_factor": 252,
            "liquidity_formula_id": LIQUIDITY_FORMULA,
            "liquidity_lookback_sessions": 20,
            "minimum_mean_dollar_volume_usd": 20_000_000.0,
            "maximum_order_participation_rate": 0.01,
            "maximum_liquidation_participation_rate": 0.05,
            "capacity_formula_id": CAPACITY_FORMULA,
            "minimum_capacity_multiple": 20.0,
            "capital_reference_usd": 460.0,
            "turnover_formula_id": TURNOVER_FORMULA,
            "calendar_policy_id": "XNYS_US_EQUITIES_HOLIDAY_RULES_V1",
            "effective_from": "2026-08-18", "execution_authority": False,
            "activation_authority": False,
        },
        "execution_authority": False, "activation_authority": False,
    }
    risk_policy_proposal["content_hash"] = artifact_content_hash(
        risk_policy_proposal
    )
    risk_policy_owner_decision = {
        "schema_version": LYRA_RISK_POLICY_OWNER_DECISION_SCHEMA,
        "owner_decision_id": "owner-decision:lyra-risk-policy:activation-test-v1",
        "proposal_id": risk_policy_proposal["proposal_id"],
        "proposal_hash": risk_policy_proposal["content_hash"],
        "decision": "APPROVE", "owner": "Brett Olson",
        "decided_at": "2026-08-19T00:00:00+00:00",
        "expires_at": "2026-08-26T20:00:00+00:00",
        "execution_authority": False, "activation_authority": False,
    }
    risk_policy_owner_decision["content_hash"] = artifact_content_hash(
        risk_policy_owner_decision
    )
    live_owner_decision = copy.deepcopy(OWNER)
    live_owner_decision["approved_policy_patch"].update({
        "lyra_evidence_policy_proposal_hash": risk_policy_proposal["content_hash"],
        "lyra_evidence_policy_owner_decision_hash": (
            risk_policy_owner_decision["content_hash"]
        ),
        "lyra_evidence_policy_terms": risk_policy_proposal["policy_terms"],
    })
    live_owner_decision["content_hash"] = artifact_content_hash(
        live_owner_decision
    )
    risk_policy = {
        "schema_version": LYRA_RISK_POLICY_SCHEMA,
        "policy_id": "lyra-risk-policy:test-owner-approved-v1",
        "status": "APPROVED", "sleeve_id": "caerus_lyra",
        "metric": "annualized_volatility", "formula_id": RISK_FORMULA,
        "lookback_sessions": 20, "minimum_price_observations": 21,
        "annualization_factor": 252,
        "liquidity_formula_id": LIQUIDITY_FORMULA,
        "liquidity_lookback_sessions": 20,
        "minimum_mean_dollar_volume_usd": 20_000_000.0,
        "maximum_order_participation_rate": 0.01,
        "maximum_liquidation_participation_rate": 0.05,
        "capacity_formula_id": CAPACITY_FORMULA,
        "minimum_capacity_multiple": 20.0,
        "capital_reference_usd": 460.0,
        "turnover_formula_id": TURNOVER_FORMULA,
        "calendar_policy_id": "XNYS_US_EQUITIES_HOLIDAY_RULES_V1",
        "approved_by": "OWNER",
        "approved_at": "2026-08-19T00:00:00+00:00",
        "effective_from": "2026-08-18",
        "owner_decision_hash": risk_policy_owner_decision["content_hash"],
        "live_owner_decision_hash": live_owner_decision["content_hash"],
        "execution_authority": False,
    }
    risk_policy["content_hash"] = artifact_content_hash(risk_policy)
    session = build_lyra_governed_session_snapshot(
        trade_date="2026-08-25", execution_session="2026-08-25",
        signal_as_of="2026-08-24", effective_target_date="2026-08-24",
        as_of="2026-08-25T12:56:00+00:00",
        captured_at="2026-08-25T12:57:00+00:00",
        source_session_id="session:2026-08-25:lyra-live-v1",
        source_session_hash="a" * 64, evaluation_file_hash="b" * 64,
        legacy_decision_file_hash="c" * 64, legacy_lyra_decision_hash="3" * 64,
        lyra_source_hash="d" * 64,
        prior_lyra_source_hash="e" * 64,
        universe_freeze_hash=freeze["content_hash"],
        universe_source_hash="1" * 64,
        market_data_snapshot_hash=market["content_hash"],
        target_selection_evidence_hash=selection["content_hash"],
        forecast_risk_policy_hash=risk_policy["content_hash"],
        forecast_risk_policy_proposal_hash=risk_policy_proposal["content_hash"],
        forecast_risk_policy_owner_decision_hash=(
            risk_policy_owner_decision["content_hash"]
        ),
    )
    risk = build_lyra_forecast_risk_evidence(
        session_snapshot=session, market_data_snapshot=market,
        target_rows=target_rows, risk_policy=risk_policy,
        risk_policy_proposal=risk_policy_proposal,
        risk_policy_owner_decision=risk_policy_owner_decision,
        target_selection_evidence=selection,
    )
    liquidity = build_lyra_liquidity_evidence(
        session_snapshot=session, market_data_snapshot=market,
        target_rows=target_rows,
        governed_policy=risk_policy,
        governed_policy_proposal=risk_policy_proposal,
        governed_policy_owner_decision=risk_policy_owner_decision,
    )
    capacity = build_lyra_capacity_evidence(liquidity_evidence=liquidity)
    sources = governed_evidence_source_artifacts(
        session_snapshot=session, market_data_snapshot=market,
        forecast_risk=risk, capacity=capacity,
    )
    sources.extend([
        {"artifact_type": "source_session_manifest", "schema_version": "caerus.session_manifest.v1", "content_hash": "a" * 64, "sleeve_id": "caerus_lyra"},
        {"artifact_type": "legacy_evaluation_file", "schema_version": "caerus_all_sleeve_evaluation_v1", "content_hash": "b" * 64, "sleeve_id": "caerus_lyra"},
        {"artifact_type": "legacy_decision_file", "schema_version": "caerus.sleeve_decision_batch.v1", "content_hash": "c" * 64, "sleeve_id": "caerus_lyra"},
        {"artifact_type": "legacy_lyra_decision", "schema_version": "caerus.sleeve_decision.v1", "content_hash": "3" * 64, "sleeve_id": "caerus_lyra"},
        {"artifact_type": "current_lyra_shadow_source", "schema_version": "legacy_shadow_snapshot_json", "content_hash": "d" * 64, "sleeve_id": "caerus_lyra"},
        {"artifact_type": "prior_lyra_shadow_source", "schema_version": "legacy_shadow_snapshot_json", "content_hash": "e" * 64, "sleeve_id": "caerus_lyra"},
        {"artifact_type": "governed_universe_freeze", "schema_version": freeze["schema_version"], "content_hash": freeze["content_hash"], "sleeve_id": "caerus_lyra"},
        {"artifact_type": "governed_universe_bytes", "schema_version": "csv", "content_hash": "1" * 64, "sleeve_id": "caerus_lyra"},
        {"artifact_type": "live_owner_policy_anchor", "schema_version": "caerus.owner_decision.v1", "content_hash": live_owner_decision["content_hash"], "sleeve_id": "caerus_lyra"},
    ])
    body = {
        "schema_version": "caerus.sleeve_decision.v2",
        "trade_date": "2026-08-25",
        "session_id": session["session_id"],
        "session_hash": session["content_hash"],
        "sleeve_id": "caerus_lyra",
        "outcome": "RECOMMENDATION",
        "confidence": 1.0,
        "forecast_risk": risk,
        "capacity": capacity,
        "expected_turnover": 0.0,
        "liquidity_status": "PASS",
        "source_method": GENERIC_LYRA_SOURCE_METHOD,
        "decision_grade": "READY",
        "target_rows": target_rows,
        "reason_codes": sorted({
            "PROSPECTIVE_GOVERNED_EVIDENCE_TRANSITION",
            "LEGACY_EVALUATION_NOT_RELABELED",
            "CONFIDENCE_IS_COMPLETE_GOVERNED_EVIDENCE",
            "FORECAST_RISK_20D_FORMULA_BOUND",
            "LIQUIDITY_20D_FORMULA_BOUND",
            "CAPACITY_5PCT_ADV_FORMULA_BOUND",
            "TURNOVER_FULL_L1_FORMULA_BOUND",
            f"RISK_FORMULA:{RISK_FORMULA}",
            f"LIQUIDITY_FORMULA:{LIQUIDITY_FORMULA}",
            f"CAPACITY_FORMULA:{CAPACITY_FORMULA}",
            f"TURNOVER_FORMULA:{TURNOVER_FORMULA}",
        }),
        "source_artifacts": sorted(sources, key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":"))),
        "decision_id": "pending",
    }
    body["decision_id"] = (
        "sleeve-decision:v2:2026-08-25:caerus_lyra:"
        + artifact_content_hash(body)[:24]
    )
    decision = seal_sleeve_decision(body)
    readiness = build_generic_lyra_v2_readiness(
        trade_date="2026-08-25", evaluated_at="2026-08-25T12:57:00+00:00",
        session_snapshot=session, decision=decision,
        evidence_hashes=[
            risk["content_hash"], liquidity["content_hash"], capacity["content_hash"],
            market["content_hash"], selection["content_hash"], risk_policy["content_hash"],
            risk_policy_proposal["content_hash"],
            risk_policy_owner_decision["content_hash"],
            live_owner_decision["content_hash"],
        ],
    )
    result = {
        "schema_version": GENERIC_LYRA_CAPTURE_RESULT_SCHEMA,
        "trade_date": "2026-08-25", "execution_session": "2026-08-25",
        "signal_as_of": "2026-08-24", "effective_target_date": "2026-08-24",
        "captured_at": "2026-08-25T12:57:00+00:00", "status": "READY_NO_SUBMIT",
        "market_data_snapshot": market, "target_selection_evidence": selection,
        "forecast_risk_policy": risk_policy,
        "forecast_risk_policy_proposal": risk_policy_proposal,
        "forecast_risk_policy_owner_decision": risk_policy_owner_decision,
        "live_owner_decision": live_owner_decision,
        "universe_freeze": freeze,
        "universe_members": symbols, "prior_target_rows": target_rows,
        "session_snapshot": session, "forecast_risk": risk,
        "liquidity": liquidity, "capacity": capacity, "decision": decision,
        "readiness": readiness, "write_enabled": False,
        "broker_call_performed": False, "broker_write_performed": False,
        "submission_allowed": False, "execution_authority": False,
        "activation_authority": False,
    }
    result["content_hash"] = artifact_content_hash(result)
    return validate_generic_lyra_v2_capture_result(result)


# The prospective fixture's exact session owner decision is the independently
# pinned trust anchor used by activation and all execution-adjacent tests.
OWNER = _capture()["live_owner_decision"]


def _decision() -> dict:
    return _capture()["decision"]


def _raw_source_recompute(capture: dict | None = None) -> dict:
    capture = capture or _capture()
    proof = {
        "schema_version": GENERIC_LYRA_RAW_RECOMPUTE_SCHEMA,
        "status": "PASS_NO_WRITE",
        "execution_session": capture["execution_session"],
        "expected_capture_hash": capture["content_hash"],
        "recomputed_capture_hash": capture["content_hash"],
        "source_files": [
            {
                "name": name,
                "path": f"/protected/lyra/{name}",
                "sha256": hashlib.sha256(name.encode("utf-8")).hexdigest(),
            }
            for name in sorted(GENERIC_LYRA_RAW_SOURCE_NAMES)
        ],
        "write_enabled": False,
        "broker_call_performed": False,
        "broker_write_performed": False,
        "submission_allowed": False,
        "execution_authority": False,
        "activation_authority": False,
    }
    proof["content_hash"] = artifact_content_hash(proof)
    return validate_generic_lyra_v2_raw_source_recompute(
        proof, expected_capture=capture
    )


def _plan(decision: dict, *, already_at_target: bool = False) -> dict:
    batch = build_sleeve_decision_batch(
        decisions=[decision], generated_at="2026-08-25T13:29:00+00:00"
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
            "capital_ceiling_usd": 460.0,
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
        allocated_at="2026-08-25T13:29:10+00:00",
    )
    target = build_lane_target_package(
        lane_allocation=allocation, decision_batch=batch,
        sealed_at="2026-08-25T13:29:20+00:00",
    )
    contribution = copy.deepcopy(target["target_rows"][0]["sleeve_contributions"][0])
    contribution["quantity"] = 4.0
    positions = [{
        "symbol": "OLD", "quantity": 4.0,
        "sleeve_contributions": [contribution],
    }]
    cash = 60.9
    if already_at_target:
        positions = []
        for row in target["target_rows"]:
            held = copy.deepcopy(row["sleeve_contributions"][0])
            held["quantity"] = 4.0
            positions.append({
                "symbol": row["symbol"], "quantity": 4.0,
                "sleeve_contributions": [held],
            })
        cash = 60.9
    snapshot = {
        "schema_version": BROKER_SNAPSHOT_SCHEMA,
        "snapshot_id": "broker-snapshot:generic-live-v1:2026-08-25:fixture",
        "trade_date": "2026-08-25", "captured_at": "2026-08-25T13:29:30+00:00",
        "account_id_hash": OBSERVATION["account_id_hash"],
        "broker_environment": "alpaca_live", "currency": "USD",
        "equity": 460.9, "cash": cash, "positions": positions,
        "price_marks": [
            {"symbol": row["symbol"], "price": 20.0 if already_at_target else 100.0,
             "as_of": "2026-08-25T13:29:30+00:00"}
            for row in decision["target_rows"]
        ] + ([] if already_at_target else [{
            "symbol": "OLD", "price": 100.0,
            "as_of": "2026-08-25T13:29:30+00:00",
        }]),
    }
    snapshot["content_hash"] = artifact_content_hash(snapshot)
    risk = build_lane_risk_package(
        lane_target_package=target, account_state_hash=snapshot["content_hash"],
        decision="APPROVE", evaluated_at="2026-08-25T13:29:40+00:00",
    )
    return build_lane_exact_execution_plan(
        lane_risk_package=risk, broker_snapshot=snapshot,
        governed_lane_policy=policy, planned_at="2026-08-25T13:29:50+00:00",
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
    capture = _capture()
    decision = capture["decision"]
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
        evaluated_at="2026-08-25T13:30:00+00:00",
        lyra_decision=decision,
        lyra_capture_result=capture,
        lyra_raw_source_recompute=_raw_source_recompute(capture),
        exact_plan=plan,
    )

    assert result["status"] == "READY_TO_DISARM_FOR_SESSION"
    assert result["reason_codes"] == ["ALL_OWNER_APPROVED_LIVE_V1_GATES_GREEN"]
    assert result["lyra_decision_hash"] == decision["content_hash"]
    assert result["lyra_raw_source_recompute_hash"] == _raw_source_recompute(
        capture
    )["content_hash"]
    assert result["exact_plan_hash"] == plan["content_hash"]
    assert result["execution_authority"] is False


def test_ready_preflight_recomputes_exactly_from_all_protected_sources() -> None:
    capture = _capture()
    decision = capture["decision"]
    plan = _plan(decision)
    proofs = _proofs(
        deployed_sha=EXPECTED, generic_schedule_installed=True,
        generic_submission_adapter_deployed=True, rollback_rearm_proven=True,
        order_lifecycle_pipeline_green=True, reconciliation_pipeline_green=True,
        accounting_pipeline_green=True, reporting_pipeline_green=True,
    )
    preflight = build_generic_live_v1_activation_preflight(
        owner_decision=OWNER, live_account_observation=OBSERVATION,
        operational_proofs=proofs, evaluated_at="2026-08-25T13:30:00+00:00",
        lyra_decision=decision, lyra_capture_result=capture,
        lyra_raw_source_recompute=_raw_source_recompute(capture), exact_plan=plan,
    )
    assert recompute_generic_live_v1_activation_preflight(
        expected_preflight=preflight, owner_decision=OWNER,
        live_account_observation=OBSERVATION, operational_proofs=proofs,
        lyra_decision=decision, lyra_capture_result=capture,
        lyra_raw_source_recompute=_raw_source_recompute(capture), exact_plan=plan,
    ) == preflight
    forged_sources = copy.deepcopy(proofs)
    forged_sources["reporting_pipeline_green"] = False
    with pytest.raises(GenericLiveV1ActivationError, match="does not exactly recompute"):
        recompute_generic_live_v1_activation_preflight(
            expected_preflight=preflight, owner_decision=OWNER,
            live_account_observation=OBSERVATION,
            operational_proofs=forged_sources,
            lyra_decision=decision, lyra_capture_result=capture,
            lyra_raw_source_recompute=_raw_source_recompute(capture),
            exact_plan=plan,
        )
    forged_raw = _raw_source_recompute(capture)
    forged_raw["source_files"][0]["path"] = "/protected/other-source"
    forged_raw["content_hash"] = artifact_content_hash(forged_raw)
    with pytest.raises(GenericLiveV1ActivationError, match="does not exactly recompute"):
        recompute_generic_live_v1_activation_preflight(
            expected_preflight=preflight, owner_decision=OWNER,
            live_account_observation=OBSERVATION, operational_proofs=proofs,
            lyra_decision=decision, lyra_capture_result=capture,
            lyra_raw_source_recompute=forged_raw, exact_plan=plan,
        )


@pytest.mark.parametrize(
    ("evidence_kind", "expected_blocker"),
    [
        ("risk", "LYRA_V2_DECISION_FORECAST_RISK_INVALID"),
        ("liquidity", "LYRA_V2_DECISION_LIQUIDITY_EVIDENCE_INVALID"),
        ("capacity", "LYRA_V2_DECISION_CAPACITY_INVALID"),
    ],
)
def test_resealed_governed_evidence_cannot_bypass_activation(
    evidence_kind: str, expected_blocker: str,
) -> None:
    original = _decision()
    plan = _plan(original)
    forged = copy.deepcopy(original)
    if evidence_kind == "risk":
        evidence = forged["forecast_risk"]
        evidence["annualized_volatility"] += 0.01
        evidence["content_hash"] = artifact_content_hash(evidence)
    elif evidence_kind == "liquidity":
        evidence = forged["capacity"]["liquidity_evidence"]
        evidence["symbol_results"][0]["mean_dollar_volume_20"] += 1.0
        evidence["content_hash"] = artifact_content_hash(evidence)
        forged["capacity"]["content_hash"] = artifact_content_hash(forged["capacity"])
    else:
        evidence = forged["capacity"]
        evidence["maximum_deployable_capital_usd"] += 1.0
        evidence["content_hash"] = artifact_content_hash(evidence)
    forged = seal_sleeve_decision(forged)
    result = build_generic_live_v1_activation_preflight(
        owner_decision=OWNER, live_account_observation=OBSERVATION,
        operational_proofs=_proofs(
            deployed_sha=EXPECTED, generic_schedule_installed=True,
            generic_submission_adapter_deployed=True, rollback_rearm_proven=True,
            order_lifecycle_pipeline_green=True, reconciliation_pipeline_green=True,
            accounting_pipeline_green=True, reporting_pipeline_green=True,
        ),
        evaluated_at="2026-08-25T13:30:00+00:00",
        lyra_decision=forged, exact_plan=plan,
    )
    assert result["status"] == "BLOCKED"
    assert expected_blocker in result["reason_codes"]
    assert result["gate_results"]["lyra_v2_decision_green"] is False


@pytest.mark.parametrize(
    "mutation", [
        "freeze_timing", "universe_members", "session", "market", "target_rank",
        "risk_policy_owner",
    ]
)
def test_protected_capture_source_mutations_fail_activation(mutation: str) -> None:
    capture = _capture()
    decision = capture["decision"]
    plan = _plan(decision)
    forged = copy.deepcopy(capture)
    if mutation == "freeze_timing":
        forged["universe_freeze"]["effective_from"] = "2026-08-20T00:00:00+00:00"
        forged["universe_freeze"]["content_hash"] = artifact_content_hash(
            forged["universe_freeze"]
        )
    elif mutation == "universe_members":
        forged["universe_members"] = list(reversed(forged["universe_members"]))
    elif mutation == "session":
        forged["session_snapshot"]["signal_as_of"] = "2026-08-17"
        forged["session_snapshot"]["content_hash"] = artifact_content_hash(
            forged["session_snapshot"]
        )
    elif mutation == "market":
        forged["market_data_snapshot"]["rows"][0]["close"] += 1.0
        forged["market_data_snapshot"]["content_hash"] = artifact_content_hash(
            forged["market_data_snapshot"]
        )
    elif mutation == "target_rank":
        forged["target_selection_evidence"]["ranked_candidates"][0]["momentum_score"] += 1.0
        forged["target_selection_evidence"]["content_hash"] = artifact_content_hash(
            forged["target_selection_evidence"]
        )
    else:
        forged["forecast_risk_policy_owner_decision"]["owner"] = "NOT_THE_OWNER"
        forged["forecast_risk_policy_owner_decision"]["content_hash"] = (
            artifact_content_hash(forged["forecast_risk_policy_owner_decision"])
        )
    forged["content_hash"] = artifact_content_hash(forged)
    result = build_generic_live_v1_activation_preflight(
        owner_decision=OWNER, live_account_observation=OBSERVATION,
        operational_proofs=_proofs(
            deployed_sha=EXPECTED, generic_schedule_installed=True,
            generic_submission_adapter_deployed=True, rollback_rearm_proven=True,
            order_lifecycle_pipeline_green=True, reconciliation_pipeline_green=True,
            accounting_pipeline_green=True, reporting_pipeline_green=True,
        ),
        evaluated_at="2026-08-25T13:30:00+00:00",
        lyra_decision=decision, lyra_capture_result=forged, exact_plan=plan,
    )
    assert result["status"] == "BLOCKED"
    assert "LYRA_V2_CAPTURE_INVALID" in result["reason_codes"]


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


def test_owner_approval_must_still_be_current_at_execution() -> None:
    require_generic_live_v1_owner_current_at_execution(
        owner_decision=OWNER, executed_at="2026-08-25T13:31:00+00:00",
    )
    with pytest.raises(GenericLiveV1ActivationError, match="expired before"):
        require_generic_live_v1_owner_current_at_execution(
            owner_decision=OWNER, executed_at="2026-08-26T20:00:01+00:00",
        )
