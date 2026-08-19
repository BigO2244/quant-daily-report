from __future__ import annotations

import copy
import hashlib

import pytest

from core.accounting_journal import ACCOUNTING_JOURNAL_ENTRY_SCHEMA, seal_journal_entry
from core.deployment_policy import DEPLOYMENT_POLICY_SCHEMA, seal_deployment_policy_payload
from core.generic_live_v1_posttrade import (
    build_and_finalize_generic_live_v1_production_posttrade,
)
from Tests.test_exact_execution_plan_v4 import _plan
from Tests.test_lane_reconciliation import _full_evidence


def _posting(identity, account, debit, credit, sleeve):
    return {
        "posting_id": identity, "ledger_account": account, "sleeve_id": sleeve,
        "currency": "USD", "debit_amount": debit, "credit_amount": credit,
    }


def _fixture(tmp_path):
    plan, _, _, _ = _plan(lane_kind="LIVE", lane_id="generic-live-v1")
    orders, fills, ending = _full_evidence(plan)
    source = hashlib.sha256(b"opening-live-state").hexdigest()
    scope = {
        "trade_date": plan["trade_date"], "account_id_hash": plan["account_id_hash"],
        "lane_id": plan["lane_id"], "lane_kind": "LIVE",
        "deployment_version": plan["deployment_version"], "sleeve_id": "sleeve_beta",
        "counterparty_sleeve_id": None, "attribution_status": "ATTRIBUTED",
        "performance_surface": "FACTUAL_LIVE", "economic_authority": "BROKER_RECONCILED",
    }
    opening_cash = seal_journal_entry({
        "schema_version": ACCOUNTING_JOURNAL_ENTRY_SCHEMA,
        "journal_entry_id": "opening-live-cash", "event_type": "OPENING_CAPITAL",
        "event_time": "2026-08-18T11:00:00+00:00", **scope, "symbol": None,
        "quantity": 0.0, "price": 0.0, "gross_amount": plan["starting_cash"],
        "fee_amount": 0.0, "net_amount": plan["starting_cash"], "session_id": None,
        "decision_id": None, "allocation_id": None, "plan_id": None,
        "broker_order_id": None, "fill_id": None, "source_hash": source,
        "postings": [
            _posting("opening-live-cash-asset", "ASSET:CASH", plan["starting_cash"], 0.0, "sleeve_beta"),
            _posting("opening-live-cash-equity", "EQUITY:OPENING_CAPITAL", 0.0, plan["starting_cash"], "sleeve_beta"),
        ],
    })
    starting = plan["starting_positions"][0]
    opening_position = seal_journal_entry({
        "schema_version": ACCOUNTING_JOURNAL_ENTRY_SCHEMA,
        "journal_entry_id": "opening-live-position", "event_type": "CORPORATE_ACTION",
        "event_time": "2026-08-18T11:00:01+00:00", **scope,
        "symbol": starting["symbol"], "quantity": starting["quantity"], "price": 0.0,
        "gross_amount": 0.0, "fee_amount": 0.0, "net_amount": 0.0,
        "session_id": None, "decision_id": None, "allocation_id": None,
        "plan_id": None, "broker_order_id": None, "fill_id": None,
        "source_hash": source,
        "postings": [
            _posting("opening-live-position-qty", "MEMO:SECURITY_QUANTITY", 0.0, 0.0, "sleeve_beta"),
            _posting("opening-live-position-action", "MEMO:CORPORATE_ACTION", 0.0, 0.0, "sleeve_beta"),
        ],
    })
    lane = {
        "lane_id": "generic-live-v1", "lane_kind": "LIVE", "enabled": True,
        "account_id_hash": plan["account_id_hash"], "broker_environment": "alpaca_live",
        "performance_surface": "FACTUAL_LIVE",
        "eligible_sleeves": [
            {
                "sleeve_id": sleeve, "minimum_weight": 0.0, "maximum_weight": 1.0,
                "initial_weight": weight, "allocation_eligible": True,
                "execution_eligible": True, "observation_enabled": True,
            }
            for sleeve, weight in (("sleeve_alpha", 0.6), ("sleeve_beta", 0.4))
        ],
        "allocator_policy": {"policy_id": "configured_risk_budget_v1"},
        "risk_policy": {"policy_id": "strict-risk-v1"},
        "capital_policy": {"owner_approved_ceiling": 460.0},
        "execution_policy": {"policy_id": "generic-v4"},
        "reconciliation_policy": {"policy_id": "strict-v1"},
    }
    policy = seal_deployment_policy_payload({
        "schema_version": DEPLOYMENT_POLICY_SCHEMA,
        "deployment_version": plan["deployment_version"], "status": "ACTIVE",
        "approved_by": "Brett Olson", "owner_decision_id": "live-v1-owner",
        "approved_at": "2026-08-18T20:00:00Z", "effective_session": "2026-08-18",
        "prior_deployment_version": "live-v0", "rollback_deployment_version": "live-safe",
        "lanes": [lane],
    })
    deployment_state = {
        "active": {"deployment_version": plan["deployment_version"], "state": "ACTIVE", "source_hash": policy["content_hash"]},
        "prior": {"deployment_version": "live-v0", "state": "SUPERSEDED", "source_hash": "1" * 64},
        "rollback": {"deployment_version": "live-safe", "state": "ROLLBACK_READY", "source_hash": "2" * 64},
    }
    return {
        "submission_result": {"content_hash": "3" * 64}, "exact_plan": plan,
        "order_lifecycle": {"content_hash": "4" * 64}, "broker_orders": orders,
        "broker_fills": fills, "ending_state": ending,
        "existing_journal_entries": [opening_cash, opening_position], "prior_valuations": [],
        "deployment_policy": policy, "known_sleeve_ids": ["sleeve_alpha", "sleeve_beta"],
        "deployment_state": deployment_state,
        "capital": {"capital_ceiling_usd": 460.0, "effective_deployable_capital_usd": 460.0, "source_hash": "5" * 64},
        "other_lane_audits": [], "reconciled_at": "2026-08-18T11:12:00+00:00",
        "valuation_date": "2026-08-18", "finalized_at": "2026-08-18T22:00:00Z",
        "reporting_artifact_directory": tmp_path / "reporting",
        "rearm_state_path": tmp_path / "gate.json", "base_result_path": tmp_path / "base" / "result.json",
        "closure_result_path": tmp_path / "closure" / "result.json",
        "rollback_handler": lambda trigger: {
            "status": "ROLLED_BACK_ARMED", "trigger": trigger,
            "paper_bytes_unchanged": True, "cron_exact_line_removed": True,
            "config_action": "ALREADY_ABSENT", "rearm_hash": "6" * 64,
        },
    }


def test_builds_and_persists_no_fallback_causal_chain(tmp_path, monkeypatch) -> None:
    import core.generic_live_v1_posttrade as subject

    monkeypatch.setattr(subject, "finalize_generic_live_v1_posttrade", lambda **kwargs: {
        "content_hash": "7" * 64, "status": "GREEN_REARMED",
        "finalized_at": kwargs["finalized_at"], "rollback_required": False,
    })
    result = build_and_finalize_generic_live_v1_production_posttrade(**_fixture(tmp_path))
    artifacts = list((tmp_path / "reporting").glob("*.json"))
    assert result["status"] == "GREEN_REARMED"
    assert len(artifacts) >= 7
    assert result["reconciliation_hash"] in {path.stem for path in artifacts}
    assert result["performance_hash"] in {path.stem for path in artifacts}


@pytest.mark.parametrize("field", ["broker_orders", "broker_fills", "ending_state"])
def test_stale_or_unrelated_broker_evidence_rolls_back(tmp_path, monkeypatch, field) -> None:
    arguments = _fixture(tmp_path)
    broken = copy.deepcopy(arguments[field])
    if isinstance(broken, list):
        broken[0]["account_id_hash"] = "f" * 64
    else:
        broken["account_id_hash"] = "f" * 64
    arguments[field] = broken
    observed = []
    arguments["rollback_handler"] = lambda trigger: observed.append(trigger) or {
        "status": "ROLLED_BACK_ARMED", "trigger": trigger,
        "paper_bytes_unchanged": True, "cron_exact_line_removed": True,
        "config_action": "ALREADY_ABSENT", "rearm_hash": "6" * 64,
    }
    with pytest.raises(Exception):
        build_and_finalize_generic_live_v1_production_posttrade(**arguments)
    assert observed == ["RECONCILIATION_BREAK"]
