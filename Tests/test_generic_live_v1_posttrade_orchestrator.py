from __future__ import annotations

import copy
import hashlib
import json

import pytest

from core import generic_live_v1_posttrade as subject
from core import generic_live_v1_submission as submission_subject
from core.accounting_journal import ACCOUNTING_JOURNAL_ENTRY_SCHEMA, seal_journal_entry
from core.deployment_policy import DEPLOYMENT_POLICY_SCHEMA, seal_deployment_policy_payload
from core.generic_live_v1_posttrade import build_and_finalize_generic_live_v1_production_posttrade
from core.lane_reconciliation import (
    BROKER_FILL_EVIDENCE_SCHEMA, BROKER_ORDER_EVIDENCE_SCHEMA,
    ENDING_LANE_STATE_SCHEMA, seal_broker_fill_evidence,
    seal_broker_order_evidence, seal_ending_lane_state,
)
from Tests.test_generic_live_v1_submission import (
    Broker, _disarm, _ready, execute_generic_live_v1_session,
    seal_generic_live_v1_order_lifecycle,
)
from Tests.test_generic_live_v1_activation import (
    EXPECTED, OBSERVATION, OWNER, _decision, _plan, _proofs,
)
from core.generic_live_v1_activation import build_generic_live_v1_activation_preflight
from authority.lane_exact_plan import canonical_json

SOURCE = hashlib.sha256(b"generic-live-v1-posttrade-broker").hexdigest()


def _scope(plan):
    return {
        "trade_date": plan["trade_date"], "account_id_hash": plan["account_id_hash"],
        "lane_id": plan["lane_id"], "lane_kind": plan["lane_kind"],
        "deployment_version": plan["deployment_version"], "plan_id": plan["plan_id"],
        "plan_hash": plan["content_hash"],
    }


def _broker_evidence(plan, broker_order_id, *, status="FILLED", quantity=None):
    order = [*plan["sell_orders"], *plan["buy_orders"]][0]
    filled = float(order["quantity"] if quantity is None else quantity)
    order_evidence = seal_broker_order_evidence({
        "schema_version": BROKER_ORDER_EVIDENCE_SCHEMA,
        "observation_id": f"observation:{order['order_id']}",
        "observed_at": "2026-08-19T13:35:00+00:00", **_scope(plan),
        "order_id": order["order_id"], "client_order_id": order["client_order_id"],
        "broker_order_id": broker_order_id, "status": status,
        "submitted_quantity": order["quantity"], "filled_quantity": filled,
        "source_hash": SOURCE,
    })
    fills = []
    if filled:
        fills.append(seal_broker_fill_evidence({
            "schema_version": BROKER_FILL_EVIDENCE_SCHEMA,
            "fill_id": f"fill:{order['order_id']}:1",
            "event_time": "2026-08-19T13:34:30+00:00", **_scope(plan),
            "order_id": order["order_id"], "client_order_id": order["client_order_id"],
            "broker_order_id": broker_order_id, "symbol": order["symbol"],
            "side": order["side"], "quantity": filled,
            "price": order["enforcement_price"], "fee_amount": 0.0,
            "source_hash": SOURCE,
        }))
    positions = {row["symbol"]: float(row["quantity"]) for row in plan["starting_positions"]}
    cash = float(plan["starting_cash"])
    for fill in fills:
        direction = 1.0 if fill["side"] == "BUY" else -1.0
        positions[fill["symbol"]] = positions.get(fill["symbol"], 0.0) + direction * float(fill["quantity"])
        gross = float(fill["quantity"]) * float(fill["price"])
        cash += -gross if fill["side"] == "BUY" else gross
    marks = {row["symbol"]: float(row["price"]) for row in plan["price_marks"]}
    position_rows = [
        {"symbol": symbol, "quantity": qty, "mark": marks[symbol],
         "market_value": qty * marks[symbol], "source_hash": SOURCE}
        for symbol, qty in sorted(positions.items()) if qty > 1e-8
    ]
    ending = seal_ending_lane_state({
        "schema_version": ENDING_LANE_STATE_SCHEMA,
        "state_id": "ending-state:generic-live-v1:2026-08-19",
        "as_of": "2026-08-19T20:00:00+00:00", **_scope(plan),
        "cash": cash, "equity": cash + sum(row["market_value"] for row in position_rows),
        "positions": position_rows, "source_hash": SOURCE,
    })
    return [order_evidence], fills, ending


def _opening_journal(plan):
    amount = float(plan["starting_equity"])
    rows = [seal_journal_entry({
        "schema_version": ACCOUNTING_JOURNAL_ENTRY_SCHEMA,
        "journal_entry_id": "opening-live-cash", "event_type": "OPENING_CAPITAL",
        "event_time": "2026-08-18T13:00:00+00:00", "trade_date": "2026-08-18",
        "account_id_hash": plan["account_id_hash"], "lane_id": "generic-live-v1",
        "lane_kind": "LIVE", "deployment_version": plan["deployment_version"],
        "sleeve_id": "caerus_lyra", "counterparty_sleeve_id": None,
        "attribution_status": "ATTRIBUTED", "performance_surface": "FACTUAL_LIVE",
        "economic_authority": "BROKER_RECONCILED", "symbol": None,
        "quantity": 0.0, "price": 0.0, "gross_amount": amount,
        "fee_amount": 0.0, "net_amount": amount, "session_id": None,
        "decision_id": None, "allocation_id": None, "plan_id": None,
        "broker_order_id": None, "fill_id": None, "source_hash": SOURCE,
        "postings": [
            {"posting_id": "opening-live-cash-asset", "ledger_account": "ASSET:CASH",
             "sleeve_id": "caerus_lyra", "currency": "USD", "debit_amount": amount, "credit_amount": 0.0},
            {"posting_id": "opening-live-cash-equity", "ledger_account": "EQUITY:OPENING_CAPITAL",
             "sleeve_id": "caerus_lyra", "currency": "USD", "debit_amount": 0.0, "credit_amount": amount},
        ],
    })]
    marks = {row["symbol"]: float(row["price"]) for row in plan["price_marks"]}
    for index, position in enumerate(plan["starting_positions"], start=1):
        quantity = float(position["quantity"])
        price = marks[position["symbol"]]
        gross = quantity * price
        contribution = position["sleeve_contributions"][0]
        rows.append(seal_journal_entry({
            "schema_version": ACCOUNTING_JOURNAL_ENTRY_SCHEMA,
            "journal_entry_id": f"opening-live-position-{index}",
            "event_type": "BUY", "event_time": "2026-08-18T14:00:00+00:00",
            "trade_date": "2026-08-18", "account_id_hash": plan["account_id_hash"],
            "lane_id": "generic-live-v1", "lane_kind": "LIVE",
            "deployment_version": plan["deployment_version"],
            "sleeve_id": contribution["sleeve_id"], "counterparty_sleeve_id": None,
            "attribution_status": "ATTRIBUTED", "performance_surface": "FACTUAL_LIVE",
            "economic_authority": "BROKER_RECONCILED", "symbol": position["symbol"],
            "quantity": quantity, "price": price, "gross_amount": gross,
            "fee_amount": 0.0, "net_amount": -gross,
            "session_id": "session:opening-live-v1",
            "decision_id": contribution["decision_id"],
            "allocation_id": "allocation:opening-live-v1",
            "plan_id": "plan:opening-live-v1", "broker_order_id": f"broker:opening:{index}",
            "fill_id": f"fill:opening:{index}", "source_hash": SOURCE,
            "postings": [
                {"posting_id": f"opening-security-{index}", "ledger_account": "ASSET:SECURITY",
                 "sleeve_id": contribution["sleeve_id"], "currency": "USD",
                 "debit_amount": gross, "credit_amount": 0.0},
                {"posting_id": f"opening-position-cash-{index}", "ledger_account": "ASSET:CASH",
                 "sleeve_id": contribution["sleeve_id"], "currency": "USD",
                 "debit_amount": 0.0, "credit_amount": gross},
            ],
        }))
    return rows


def _policy(plan):
    lane = {
        "lane_id": "generic-live-v1", "lane_kind": "LIVE", "enabled": True,
        "account_id_hash": plan["account_id_hash"], "broker_environment": "alpaca_live",
        "performance_surface": "FACTUAL_LIVE",
        "eligible_sleeves": [{
            "sleeve_id": "caerus_lyra", "minimum_weight": 1.0, "maximum_weight": 1.0,
            "initial_weight": 1.0, "allocation_eligible": True,
            "execution_eligible": True, "observation_enabled": True,
        }],
        "allocator_policy": {"policy_id": "configured-live-v1"},
        "risk_policy": {"policy_id": "generic-live-v1-risk"},
        "capital_policy": {"capital_ceiling_usd": 460.0},
        "execution_policy": {"policy_id": "generic-v4-limit"},
        "reconciliation_policy": {"policy_id": "strict-v1"},
    }
    return seal_deployment_policy_payload({
        "schema_version": DEPLOYMENT_POLICY_SCHEMA,
        "deployment_version": plan["deployment_version"], "status": "ACTIVE",
        "approved_by": "Brett Olson", "owner_decision_id": "live-v1-owner",
        "approved_at": "2026-08-18T20:00:00Z", "effective_session": "2026-08-19",
        "prior_deployment_version": "live-v0", "rollback_deployment_version": "live-safe",
        "lanes": [lane],
    })


def _rollback(trigger):
    return {
        "status": "ROLLED_BACK_ARMED", "trigger": trigger,
        "paper_bytes_unchanged": True, "cron_exact_line_removed": True,
        "config_action": "ALREADY_ABSENT", "rearm_hash": "6" * 64,
    }


def _session_fixture(tmp_path, *, outcome="FILLED"):
    preflight, plan = _ready()
    order = [*plan["sell_orders"], *plan["buy_orders"]][0]

    terminal_status = {
        "FILLED": "filled",
        "PARTIAL_CANCELED": "partially_filled",
        "REJECTED": "rejected",
    }[outcome]

    class AlignedBroker(Broker):
        def submit_generic_live_v4_limit_order(self, **kwargs):
            row = super().submit_generic_live_v4_limit_order(**kwargs)
            self.by_id.pop(row["id"])
            row["id"] = f"broker:{order['order_id']}"
            self.by_id[row["id"]] = row
            return row

    gate = tmp_path / "gate.json"
    _disarm(gate, preflight, plan)
    submission = execute_generic_live_v1_session(
        activation_preflight=preflight, exact_plan=plan,
        executed_at="2026-08-19T13:31:00+00:00", submit_enabled=True,
        broker=AlignedBroker(terminal_status=terminal_status),
        wal_directory=tmp_path / "wal",
        rearm_state_path=gate, result_path=tmp_path / "submission.json",
        poll_interval_seconds=0,
    )
    evidence_status, evidence_quantity = {
        "FILLED": ("FILLED", None),
        "PARTIAL_CANCELED": ("CANCELED", float(order["quantity"]) / 2.0),
        "REJECTED": ("REJECTED", 0.0),
    }[outcome]
    broker_orders, broker_fills, ending = _broker_evidence(
        plan, submission["broker_order"]["broker_order_id"],
        status=evidence_status, quantity=evidence_quantity,
    )
    lifecycle = seal_generic_live_v1_order_lifecycle(
        submission_result=submission, observed_at="2026-08-19T20:00:00+00:00",
        broker_order_evidence_hash=broker_orders[0]["content_hash"],
        broker_fill_evidence_hashes=[row["content_hash"] for row in broker_fills],
    )
    policy = _policy(plan)
    return {
        "submission_result": submission, "exact_plan": plan,
        "order_lifecycle": lifecycle, "broker_orders": broker_orders,
        "broker_fills": broker_fills, "ending_state": ending,
        "existing_journal_entries": _opening_journal(plan), "prior_valuations": [],
        "deployment_policy": policy, "known_sleeve_ids": ["caerus_lyra"],
        "deployment_state": {
            "active": {"deployment_version": plan["deployment_version"], "state": "ACTIVE", "source_hash": policy["content_hash"]},
            "prior": {"deployment_version": "live-v0", "state": "SUPERSEDED", "source_hash": "1" * 64},
            "rollback": {"deployment_version": "live-safe", "state": "ROLLBACK_READY", "source_hash": "2" * 64},
        },
        "capital": {"capital_ceiling_usd": 460.0, "effective_deployable_capital_usd": 460.0, "source_hash": "5" * 64},
        "other_lane_audits": [], "reconciled_at": "2026-08-19T20:01:00+00:00",
        "valuation_date": "2026-08-19", "finalized_at": "2026-08-19T20:02:00+00:00",
        "reporting_artifact_directory": tmp_path / "reporting",
        "rearm_state_path": gate, "base_result_path": tmp_path / "base" / "result.json",
        "closure_result_path": tmp_path / "closure" / "result.json",
        "rollback_handler": _rollback,
    }


def _no_trade_fixture(tmp_path):
    from Tests.test_generic_live_v1_activation import _capture
    capture = _capture()
    decision = capture["decision"]
    plan = _plan(decision, already_at_target=True)
    assert not plan["buy_orders"] and not plan["sell_orders"]
    observation = copy.deepcopy(OBSERVATION)
    observation["cash"] = "60.90"
    observation.pop("content_hash")
    observation["content_hash"] = hashlib.sha256(
        canonical_json(observation).encode("utf-8")
    ).hexdigest()
    preflight = build_generic_live_v1_activation_preflight(
        owner_decision=OWNER, live_account_observation=observation,
        operational_proofs=_proofs(
            deployed_sha=EXPECTED, generic_schedule_installed=True,
            generic_submission_adapter_deployed=True, rollback_rearm_proven=True,
            order_lifecycle_pipeline_green=True, reconciliation_pipeline_green=True,
            accounting_pipeline_green=True, reporting_pipeline_green=True,
        ),
        evaluated_at="2026-08-19T13:30:00+00:00",
        lyra_decision=decision, lyra_capture_result=capture, exact_plan=plan,
    )

    class HoldingBroker(Broker):
        def __init__(self):
            super().__init__(cash="60.9", buying_power="60.9")

        def get_positions(self):
            return [
                {"symbol": row["symbol"], "qty": str(row["quantity"])}
                for row in plan["starting_positions"]
            ]

    gate = tmp_path / "gate.json"
    _disarm(gate, preflight, plan)
    submission = execute_generic_live_v1_session(
        activation_preflight=preflight, exact_plan=plan, lyra_decision=decision,
        executed_at="2026-08-19T13:31:00+00:00", submit_enabled=True,
        broker=HoldingBroker(), wal_directory=tmp_path / "wal",
        rearm_state_path=gate, result_path=tmp_path / "submission.json",
        poll_interval_seconds=0,
    )
    ending = seal_ending_lane_state({
        "schema_version": ENDING_LANE_STATE_SCHEMA,
        "state_id": "ending-state:generic-live-v1:2026-08-19:no-trade",
        "as_of": "2026-08-19T20:00:00+00:00", **_scope(plan),
        "cash": 60.9, "equity": 460.9,
        "positions": [{
            "symbol": row["symbol"], "quantity": row["quantity"],
            "mark": 20.0, "market_value": row["quantity"] * 20.0,
            "source_hash": SOURCE,
        } for row in plan["starting_positions"]],
        "source_hash": SOURCE,
    })
    lifecycle = seal_generic_live_v1_order_lifecycle(
        submission_result=submission, observed_at="2026-08-19T20:00:00+00:00",
        broker_order_evidence_hash=None, broker_fill_evidence_hashes=[],
    )
    policy = _policy(plan)
    return {
        "submission_result": submission, "exact_plan": plan,
        "order_lifecycle": lifecycle, "broker_orders": [], "broker_fills": [],
        "ending_state": ending, "existing_journal_entries": _opening_journal(plan),
        "prior_valuations": [], "deployment_policy": policy,
        "known_sleeve_ids": ["caerus_lyra"],
        "deployment_state": {
            "active": {"deployment_version": plan["deployment_version"], "state": "ACTIVE", "source_hash": policy["content_hash"]},
            "prior": {"deployment_version": "live-v0", "state": "SUPERSEDED", "source_hash": "1" * 64},
            "rollback": {"deployment_version": "live-safe", "state": "ROLLBACK_READY", "source_hash": "2" * 64},
        },
        "capital": {"capital_ceiling_usd": 460.0, "effective_deployable_capital_usd": 460.0, "source_hash": "5" * 64},
        "other_lane_audits": [], "reconciled_at": "2026-08-19T20:01:00+00:00",
        "valuation_date": "2026-08-19", "finalized_at": "2026-08-19T20:02:00+00:00",
        "reporting_artifact_directory": tmp_path / "reporting",
        "rearm_state_path": gate, "base_result_path": tmp_path / "base" / "result.json",
        "closure_result_path": tmp_path / "closure" / "result.json",
        "rollback_handler": _rollback,
    }


def test_real_unmocked_full_fill_closes_entire_chain(tmp_path) -> None:
    result = build_and_finalize_generic_live_v1_production_posttrade(
        **_session_fixture(tmp_path)
    )
    artifacts = list((tmp_path / "reporting").glob("*.json"))
    assert result["status"] == "GREEN_REARMED"
    assert result["rollback_required"] is False
    assert result["reconciliation_hash"] in {path.stem for path in artifacts}
    assert result["performance_hash"] in {path.stem for path in artifacts}
    assert (tmp_path / "base" / "result.json").exists()
    assert (tmp_path / "closure" / "result.json").exists()


def test_real_no_trade_session_closes_as_factual_without_inventing_economics(tmp_path) -> None:
    arguments = _no_trade_fixture(tmp_path)
    result = build_and_finalize_generic_live_v1_production_posttrade(**arguments)
    artifacts = [json.loads(path.read_text()) for path in (tmp_path / "reporting").glob("*.json")]
    reconciliation = next(
        row for row in artifacts
        if row.get("schema_version") == "caerus.lane_reconciliation.v1"
    )
    assert result["status"] == "GREEN_REARMED"
    assert reconciliation["status"] == "PASS"
    assert reconciliation["accounting_ready"] is False
    assert reconciliation["reconciled_fills"] == []
    assert not any(
        row.get("source_hash") == reconciliation["content_hash"]
        for row in arguments["existing_journal_entries"]
    )


@pytest.mark.parametrize(
    ("outcome", "expected_reconciliation_status", "expects_journal_addition"),
    [
        ("PARTIAL_CANCELED", "PARTIAL", True),
        ("REJECTED", "REJECTED", False),
    ],
)
def test_real_order_breaks_close_with_suppressed_truth_and_rollback(
    tmp_path, outcome, expected_reconciliation_status, expects_journal_addition,
) -> None:
    arguments = _session_fixture(tmp_path, outcome=outcome)
    observed = []
    arguments["rollback_handler"] = (
        lambda trigger: observed.append(trigger) or _rollback(trigger)
    )
    result = build_and_finalize_generic_live_v1_production_posttrade(**arguments)

    artifacts = [
        json.loads(path.read_text())
        for path in (tmp_path / "reporting").glob("*.json")
    ]
    reconciliation = next(
        row for row in artifacts
        if row.get("schema_version") == "caerus.lane_reconciliation.v1"
    )
    daily = next(
        row for row in artifacts
        if row.get("schema_version") == "caerus.daily_lane_audit.v1"
    )
    assert result["status"] == "ROLLBACK_REQUIRED_REARMED"
    assert result["rollback_required"] is True
    assert reconciliation["status"] == expected_reconciliation_status
    assert bool(reconciliation["reconciled_fills"]) is expects_journal_addition
    assert daily["status"] == "BLOCKED"
    assert all(row["claim_status"] == "SUPPRESSED" for row in daily["return_claims"])
    assert observed == ["ORDER_BREAK"]


@pytest.mark.parametrize("field", ["broker_orders", "broker_fills", "ending_state"])
def test_stale_or_unrelated_broker_evidence_rolls_back(tmp_path, field) -> None:
    arguments = _session_fixture(tmp_path)
    broken = copy.deepcopy(arguments[field])
    if isinstance(broken, list):
        broken[0]["account_id_hash"] = "f" * 64
    else:
        broken["account_id_hash"] = "f" * 64
    arguments[field] = broken
    observed = []
    arguments["rollback_handler"] = lambda trigger: observed.append(trigger) or _rollback(trigger)
    with pytest.raises(Exception):
        build_and_finalize_generic_live_v1_production_posttrade(**arguments)
    assert observed == ["RECONCILIATION_BREAK"]


@pytest.mark.parametrize(
    ("stage", "expected_trigger"),
    [
        ("submission_validation", "ORDER_BREAK"),
        ("lifecycle_validation", "ORDER_BREAK"),
        ("reconciliation_build", "RECONCILIATION_BREAK"),
        ("accounting_build", "ACCOUNTING_BREAK"),
        ("reporting_build", "REPORTING_BREAK"),
        ("artifact_persistence", "REPORTING_BREAK"),
        ("base_result_persistence", "REPORTING_BREAK"),
        ("closure_result_persistence", "REPORTING_BREAK"),
        ("malformed_finalized_at", "REPORTING_BREAK"),
    ],
)
def test_every_raw_build_validation_and_persistence_boundary_rolls_back_once(
    tmp_path, monkeypatch, stage, expected_trigger,
) -> None:
    arguments = _session_fixture(tmp_path)
    observed = []
    arguments["rollback_handler"] = (
        lambda trigger: observed.append(trigger) or _rollback(trigger)
    )

    def fail(*_args, **_kwargs):
        raise OSError(f"injected {stage} failure")

    if stage == "submission_validation":
        monkeypatch.setattr(subject, "validate_generic_live_v1_submission_result", fail)
    elif stage == "lifecycle_validation":
        monkeypatch.setattr(subject, "validate_generic_live_v1_order_lifecycle", fail)
    elif stage == "reconciliation_build":
        monkeypatch.setattr(subject, "build_lane_reconciliation", fail)
    elif stage == "accounting_build":
        monkeypatch.setattr(subject, "build_reconciled_fill_journal_entries", fail)
    elif stage == "reporting_build":
        monkeypatch.setattr(subject, "build_lane_factual_reporting_inputs", fail)
    elif stage == "artifact_persistence":
        monkeypatch.setattr(subject, "_write_exclusive", fail)
    elif stage == "base_result_persistence":
        original = submission_subject._write_exclusive

        def fail_base(path, payload):
            if path == arguments["base_result_path"]:
                fail()
            return original(path, payload)

        monkeypatch.setattr(submission_subject, "_write_exclusive", fail_base)
    elif stage == "closure_result_persistence":
        original = subject._write_exclusive

        def fail_closure(path, payload):
            if path == arguments["closure_result_path"]:
                fail()
            return original(path, payload)

        monkeypatch.setattr(subject, "_write_exclusive", fail_closure)
    elif stage == "malformed_finalized_at":
        arguments["finalized_at"] = "not-a-time"

    with pytest.raises(Exception):
        build_and_finalize_generic_live_v1_production_posttrade(**arguments)
    assert observed == [expected_trigger]
