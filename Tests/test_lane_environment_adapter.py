from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from authority.lane_exact_plan import canonical_json
from core.lane_accounting_dry_run import run_lane_accounting_dry_run
from core.lane_environment_adapter import (
    CURRENT_LIVE_CONFIG_REFERENCES,
    LEGACY_REMOVAL_CANDIDATES,
    REQUIRED_OWNER_PREFLIGHTS,
    LaneEnvironmentAdapterError,
    build_generic_live_cutover_preflight,
    build_lane_environment_binding,
    build_live_cutover_read_only_inventory,
    validate_generic_live_cutover_preflight,
    validate_lane_environment_binding,
)
from core.lane_execution_dry_run import (
    build_lane_execution_safety_evidence,
    run_lane_execution_dry_run,
)
from core.lane_oms import build_lane_oms_intents
from core.lane_reconciliation import build_lane_reconciliation
from core.owner_decision import OWNER_DECISION_SCHEMA, seal_owner_decision_payload
from Tests.test_exact_execution_plan_v4 import _plan
from Tests.test_lane_reconciliation import _full_evidence


def _source(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _descriptor(plan: dict, *, adapter_version: str = "generic-v1") -> dict:
    prefix = "CAERUS_LIVE_PILOT" if plan["lane_kind"] == "LIVE" else "CAERUS_PAPER_LANE"
    gates = {
        "kill_switch": f"{prefix}_KILL_SWITCH",
        "owner_approval": f"{prefix}_APPROVED",
        "submission_approval": f"{prefix}_SUBMIT_APPROVED",
        "account_pin": f"{prefix}_ACCOUNT_ID_HASH",
        "deployment_sha": "CAERUS_DEPLOYMENT_SHA",
        "open_orders": "BROKER_OPEN_ORDER_SNAPSHOT",
        "leverage": "LANE_LEVERAGE_DISABLED",
        "shorting": "LANE_SHORTING_DISABLED",
        "capital_ceiling": f"{prefix}_CAPITAL_CAP",
    }
    return {
        "adapter_id": "caerus-generic-alpaca-lane",
        "adapter_version": adapter_version,
        "endpoint_class": plan["lane_kind"],
        "broker_environment": plan["broker_environment"],
        "credential_reference_hash": _source(f"credentials:{plan['lane_kind']}"),
        "configuration_reference_hash": _source(f"config:{plan['lane_kind']}"),
        "gate_references": gates,
    }


def _reconciliation(plan: dict) -> dict:
    orders, fills, state = _full_evidence(plan)
    return build_lane_reconciliation(
        exact_plan=plan,
        wal_intents=build_lane_oms_intents(plan),
        broker_orders=orders,
        broker_fills=fills,
        ending_state=state,
        reconciled_at="2026-08-18T11:12:00+00:00",
    )


def _owner_decision(live_plan: dict, live_binding: dict, **changes: object) -> dict:
    body = {
        "schema_version": OWNER_DECISION_SCHEMA,
        "owner_decision_id": "owner-decision:generic-live-cutover",
        "recommendation_id": "recommendation:generic-live-cutover",
        "recommendation_hash": _source("generic-live-cutover-recommendation"),
        "decision": "APPROVE",
        "owner": "Brett Olson",
        "decided_at": "2026-08-18T10:00:00+00:00",
        "effective_session": live_plan["trade_date"],
        "approved_policy_patch": {
            "generic_lane_cutover": {
                "lane_id": live_plan["lane_id"],
                "lane_kind": "LIVE",
                "deployment_version": live_plan["deployment_version"],
                "adapter_binding_hash": live_binding["content_hash"],
                "legacy_live_executor_import_allowed": False,
            }
        },
        "capital_ceiling": 1000.0,
        "risk_limits": {"no_leverage": True, "no_shorting": True},
        "preflight_requirements": sorted(REQUIRED_OWNER_PREFLIGHTS),
        "rollback_deployment_version": "deployment:rollback:live-small",
        "expires_at": "2026-08-18T15:00:00+00:00",
    }
    body.update(changes)
    return seal_owner_decision_payload(body)


def _green_inputs(tmp_path) -> dict:
    paper_plan, _, _, _ = _plan(lane_kind="PAPER", lane_id="paper")
    live_plan, _, _, _ = _plan(lane_kind="LIVE", lane_id="live-small")
    paper_binding = build_lane_environment_binding(
        exact_plan=paper_plan,
        adapter_descriptor=_descriptor(paper_plan),
        bound_at="2026-08-18T11:08:10+00:00",
    )
    live_binding = build_lane_environment_binding(
        exact_plan=live_plan,
        adapter_descriptor=_descriptor(live_plan),
        bound_at="2026-08-18T11:08:10+00:00",
    )
    safety = build_lane_execution_safety_evidence(
        exact_plan=live_plan,
        checked_at="2026-08-18T11:08:30+00:00",
        source_hashes=[_source("live-read-only-preflight")],
    )
    execution = run_lane_execution_dry_run(
        exact_plan=live_plan, safety_evidence=safety
    )
    reconciliation = _reconciliation(live_plan)
    accounting = run_lane_accounting_dry_run(
        exact_plan=live_plan,
        reconciliation=reconciliation,
        journal_path=tmp_path / "projected-live-journal.jsonl",
    )
    live_env = tmp_path / "live_pilot.env"
    live_env.write_text(
        "CAERUS_LIVE_PILOT_KILL_SWITCH=1\n"
        "CAERUS_LIVE_PILOT_APPROVED=0\n"
        "CAERUS_LIVE_PILOT_SUBMIT_APPROVED=0\n"
        "CAERUS_LIVE_PILOT_CRON_APPROVED=0\n"
        "CAERUS_LIVE_PILOT_SCHEDULE_ENABLED=0\n"
        "CAERUS_LIVE_PILOT_CAPITAL_CAP=1000\n"
        "CAERUS_LIVE_PILOT_MAX_ORDERS=20\n"
        "ALPACA_API_SECRET_KEY=must-not-appear\n"
    )
    inventory = build_live_cutover_read_only_inventory(
        repository_root=Path(__file__).resolve().parents[1],
        live_env_path=live_env,
        observed_at="2026-08-18T11:13:30+00:00",
    )
    return {
        "paper_exact_plan": paper_plan,
        "paper_binding": paper_binding,
        "live_exact_plan": live_plan,
        "live_binding": live_binding,
        "safety_evidence": safety,
        "execution_rehearsal": execution,
        "reconciliation": reconciliation,
        "accounting_rehearsal": accounting,
        "owner_decision": _owner_decision(live_plan, live_binding),
        "read_only_inventory": inventory,
        "evaluated_at": "2026-08-18T11:14:00+00:00",
    }


@pytest.mark.parametrize(("lane_kind", "lane_id"), [("PAPER", "paper"), ("LIVE", "live-small")])
def test_same_generic_binding_contract_is_lane_neutral(lane_kind: str, lane_id: str) -> None:
    plan, _, _, _ = _plan(lane_kind=lane_kind, lane_id=lane_id)
    binding = build_lane_environment_binding(
        exact_plan=plan,
        adapter_descriptor=_descriptor(plan),
        bound_at="2026-08-18T11:08:10+00:00",
    )

    assert binding["endpoint_class"] == lane_kind
    assert binding["adapter_contract"] == "CAERUS_GENERIC_LANE_V4"
    assert binding["submission_enabled"] is False
    assert binding["legacy_live_executor_imported"] is False
    assert binding["runtime_cutover_status"] == "NOT_YET_CUT_OVER"
    assert binding["execution_authority"] is False
    assert validate_lane_environment_binding(binding, exact_plan=plan) == binding


def test_green_live_cutover_preflight_is_still_non_authoritative_and_no_write(tmp_path) -> None:
    inputs = _green_inputs(tmp_path)

    result = build_generic_live_cutover_preflight(**inputs)

    assert result["status"] == "READY_FOR_SEPARATE_ACTIVATION"
    assert all(value == "PASS" for value in result["gate_results"].values())
    assert result["broker_call_performed"] is False
    assert result["broker_write_performed"] is False
    assert result["configuration_mutated"] is False
    assert result["execution_authority"] is False
    assert result["activation_authority"] is False
    assert result["legacy_removal_candidates"] == list(LEGACY_REMOVAL_CANDIDATES)
    assert result["current_config_references"] == CURRENT_LIVE_CONFIG_REFERENCES
    assert result["paper_runtime_status"] == "LEGACY_UNCHANGED_NOT_YET_CUT_OVER"
    assert result["legacy_live_executor_status"] == "DISABLED_UNCHANGED"
    assert "must-not-appear" not in canonical_json(result)
    assert not (tmp_path / "projected-live-journal.jsonl").exists()
    assert validate_generic_live_cutover_preflight(result) == result


def test_different_paper_live_adapter_path_is_a_hard_failure(tmp_path) -> None:
    inputs = _green_inputs(tmp_path)
    inputs["live_binding"] = build_lane_environment_binding(
        exact_plan=inputs["live_exact_plan"],
        adapter_descriptor=_descriptor(inputs["live_exact_plan"], adapter_version="live-special-v1"),
        bound_at="2026-08-18T11:08:10+00:00",
    )
    inputs["owner_decision"] = _owner_decision(
        inputs["live_exact_plan"], inputs["live_binding"]
    )

    with pytest.raises(LaneEnvironmentAdapterError, match="generic paths differ"):
        build_generic_live_cutover_preflight(**inputs)


def test_owner_scope_and_capital_terms_fail_closed_as_blockers(tmp_path) -> None:
    inputs = _green_inputs(tmp_path)
    inputs["owner_decision"] = _owner_decision(
        inputs["live_exact_plan"], inputs["live_binding"], capital_ceiling=100.0
    )

    result = build_generic_live_cutover_preflight(**inputs)

    assert result["status"] == "BLOCKED"
    assert "OWNER_CAPITAL_CEILING_EXCEEDED" in result["reason_codes"]
    assert result["gate_results"]["owner_authorization"] == "BLOCK"


def test_missing_live_env_inventory_blocks_cutover_without_changing_legacy(tmp_path) -> None:
    inputs = _green_inputs(tmp_path)
    inputs["read_only_inventory"] = build_live_cutover_read_only_inventory(
        repository_root=Path(__file__).resolve().parents[1],
        live_env_path=tmp_path / "absent-live.env",
        observed_at="2026-08-18T11:13:30+00:00",
    )

    result = build_generic_live_cutover_preflight(**inputs)

    assert result["status"] == "BLOCKED"
    assert "LIVE_ENV_REFERENCE_ABSENT" in result["reason_codes"]
    assert result["read_only_inventory"]["legacy_executor_disabled"] is True
    assert result["legacy_live_executor_status"] == "DISABLED_UNCHANGED"


def test_resealed_authority_or_binding_tamper_is_rejected(tmp_path) -> None:
    inputs = _green_inputs(tmp_path)
    result = build_generic_live_cutover_preflight(**inputs)
    tampered = copy.deepcopy(result)
    tampered["activation_authority"] = True
    body = {key: value for key, value in tampered.items() if key != "content_hash"}
    tampered["content_hash"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    with pytest.raises(LaneEnvironmentAdapterError, match="cannot mutate or grant authority"):
        validate_generic_live_cutover_preflight(tampered)

    binding = copy.deepcopy(inputs["live_binding"])
    binding["broker_environment"] = "alpaca_paper"
    body = {key: value for key, value in binding.items() if key != "content_hash"}
    binding["content_hash"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    with pytest.raises(LaneEnvironmentAdapterError, match="identity mismatch|differs from plan"):
        validate_lane_environment_binding(binding, exact_plan=inputs["live_exact_plan"])
