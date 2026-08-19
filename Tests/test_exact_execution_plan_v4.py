from __future__ import annotations

import copy

import pytest

from authority.lane_exact_plan import (
    BROKER_SNAPSHOT_SCHEMA,
    LANE_EXACT_PLAN_SCHEMA,
    LaneExactPlanError,
    artifact_content_hash,
    build_lane_exact_execution_plan,
    lane_exact_plan_content_hash,
    read_lane_exact_execution_plan,
    validate_lane_exact_execution_plan,
    write_lane_exact_execution_plan,
)
from core.lane_allocator import allocate_lane, content_hash
from core.lane_risk_authority import build_lane_risk_package
from core.lane_target_authority import build_lane_target_package


TRADE_DATE = "2026-08-18"
SESSION_ID = "session:2026-08-18:0123456789abcdef01234567"
SESSION_HASH = "a" * 64
DEPLOYMENT_VERSION = "deployment:2026-08-18:generic-fixture"
ACCOUNT_ID_HASH = "d" * 64


def _decision(sleeve_id: str, targets: dict[str, float]) -> dict:
    row = {
        "schema_version": "caerus.sleeve_decision.v1",
        "trade_date": TRADE_DATE,
        "session_id": SESSION_ID,
        "session_hash": SESSION_HASH,
        "sleeve_id": sleeve_id,
        "display_name": sleeve_id,
        "strategy_type": "security_selection",
        "family": "fixture",
        "lifecycle_status": "shadow",
        "mode": "SHADOW",
        "outcome": "RECOMMENDATION",
        "capital_eligible": False,
        "execution_eligible": False,
        "effective_as_of": TRADE_DATE,
        "source_variant": "fixture",
        "source_cash_weight": 0.0,
        "target_rows": [
            {"symbol": symbol, "target_weight": weight}
            for symbol, weight in sorted(targets.items())
        ],
        "allocation_hint": None,
        "source_artifacts": [],
        "reason_codes": [],
        "message": "fixture",
    }
    seed = content_hash(row)
    row["decision_id"] = f"sleeve-decision:{TRADE_DATE}:{sleeve_id}:{seed[:24]}"
    row["content_hash"] = content_hash(row)
    return row


def _lane_policy(*, lane_kind: str = "PAPER", lane_id: str = "paper") -> dict:
    broker_environment = "alpaca_paper" if lane_kind == "PAPER" else "alpaca_live"
    return {
        "lane_id": lane_id,
        "lane_kind": lane_kind,
        "enabled": True,
        "deployment_version": DEPLOYMENT_VERSION,
        "performance_surface": f"REALIZED_{lane_kind}_LEDGER",
        "account_id_hash": ACCOUNT_ID_HASH,
        "broker_environment": broker_environment,
        "eligible_sleeves": [
            {
                "sleeve_id": "sleeve_alpha",
                "minimum_weight": 0.0,
                "maximum_weight": 1.0,
                "initial_weight": 0.6,
                "allocation_eligible": True,
                "execution_eligible": True,
                "observation_enabled": True,
            },
            {
                "sleeve_id": "sleeve_beta",
                "minimum_weight": 0.0,
                "maximum_weight": 1.0,
                "initial_weight": 0.4,
                "allocation_eligible": True,
                "execution_eligible": True,
                "observation_enabled": True,
            },
        ],
        "allocator_policy": {
            "allocator_id": "configured_fixture",
            "allocator_version": "configured_risk_budget_v1",
            "method": "configured_risk_budget",
            "unavailable_policy": "fail_closed",
            "target_cash_weight": 0.10,
        },
        "risk_policy": {"policy_id": "risk_fixture_v1"},
        "capital_policy": {
            "policy_id": "capital_fixture_v1",
            "capital_basis": "FULL_ACCOUNT_EQUITY",
        },
        "execution_policy": {
            "policy_id": "execution_fixture_v1",
            "order_type": "market",
            "time_in_force": "day",
            "allow_extended_hours": False,
            "allow_fractional_shares": False,
            "quantity_precision": 0,
            "price_precision": 4,
            "minimum_order_notional_usd": 1.0,
            "maximum_order_notional_usd": 2000.0,
            "maximum_total_buy_notional_usd": 2000.0,
            "maximum_orders": 20,
            "max_adverse_slippage_bps": 25.0,
            "max_risk_age_seconds": 300,
            "max_broker_snapshot_age_seconds": 300,
            "max_price_age_seconds": 300,
            "plan_ttl_seconds": 120,
            "snapshot_reconciliation_tolerance_usd": 0.01,
        },
        "reconciliation_policy": {"policy_id": "reconciliation_fixture_v1"},
    }


def _target(policy: dict) -> dict:
    decisions = [
        _decision("sleeve_alpha", {"AAPL": 0.5, "MSFT": 0.5}),
        _decision("sleeve_beta", {"AAPL": 0.25, "NVDA": 0.75}),
    ]
    batch = {
        "schema_version": "caerus.sleeve_decision_batch.v1",
        "trade_date": TRADE_DATE,
        "session_id": SESSION_ID,
        "session_hash": SESSION_HASH,
        "decisions": decisions,
        "content_hash": content_hash(decisions),
    }
    allocation = allocate_lane(
        decision_batch=batch,
        lane_policy=policy,
        deployment_version=DEPLOYMENT_VERSION,
        allocated_at="2026-08-18T11:03:00+00:00",
    )
    return build_lane_target_package(
        lane_allocation=allocation,
        decision_batch=batch,
        sealed_at="2026-08-18T11:04:00+00:00",
    )


def _snapshot(target: dict, policy: dict, *, with_position: bool = True) -> dict:
    positions = []
    cash = 1000.0
    if with_position:
        nvda = next(row for row in target["target_rows"] if row["symbol"] == "NVDA")
        contribution = copy.deepcopy(nvda["sleeve_contributions"][0])
        contribution["quantity"] = 10.0
        positions = [
            {
                "symbol": "NVDA",
                "quantity": 10.0,
                "sleeve_contributions": [contribution],
            }
        ]
        cash = 500.0
    body = {
        "schema_version": BROKER_SNAPSHOT_SCHEMA,
        "snapshot_id": f"broker-snapshot:{policy['lane_id']}:{TRADE_DATE}:fixture",
        "trade_date": TRADE_DATE,
        "captured_at": "2026-08-18T11:06:00+00:00",
        "account_id_hash": ACCOUNT_ID_HASH,
        "broker_environment": policy["broker_environment"],
        "currency": "USD",
        "equity": 1000.0,
        "cash": cash,
        "positions": positions,
        "price_marks": [
            {"symbol": "AAPL", "price": 100.0, "as_of": "2026-08-18T11:06:00+00:00"},
            {"symbol": "MSFT", "price": 50.0, "as_of": "2026-08-18T11:06:00+00:00"},
            {"symbol": "NVDA", "price": 50.0, "as_of": "2026-08-18T11:06:00+00:00"},
        ],
    }
    body["content_hash"] = artifact_content_hash(body)
    return body


def _sources(
    *,
    lane_kind: str = "PAPER",
    lane_id: str = "paper",
    risk_decision: str = "APPROVE",
) -> tuple[dict, dict, dict]:
    policy = _lane_policy(lane_kind=lane_kind, lane_id=lane_id)
    target = _target(policy)
    snapshot = _snapshot(target, policy)
    arguments = {
        "lane_target_package": target,
        "account_state_hash": snapshot["content_hash"],
        "decision": risk_decision,
        "evaluated_at": "2026-08-18T11:07:00+00:00",
    }
    if risk_decision == "REJECT":
        arguments["reason_codes"] = ["ACCOUNT_STATE_UNSAFE"]
    elif risk_decision == "CONSTRAIN":
        approved_rows = copy.deepcopy(target["target_rows"])
        approved_rows = [row for row in approved_rows if row["symbol"] != "NVDA"]
        arguments.update(
            {
                "reason_codes": ["CONCENTRATION_LIMIT"],
                "constraints": {"maximum_symbol_weight": 0.40},
                "approved_cash_weight": 1.0
                - sum(float(row["target_weight"]) for row in approved_rows),
                "approved_target_rows": approved_rows,
            }
        )
    risk = build_lane_risk_package(**arguments)
    return risk, snapshot, policy


def _plan(**source_kwargs) -> tuple[dict, dict, dict, dict]:
    risk, snapshot, policy = _sources(**source_kwargs)
    plan = build_lane_exact_execution_plan(
        lane_risk_package=risk,
        broker_snapshot=snapshot,
        governed_lane_policy=policy,
        planned_at="2026-08-18T11:08:00+00:00",
    )
    return plan, risk, snapshot, policy


@pytest.mark.parametrize(
    ("lane_kind", "lane_id"),
    [("PAPER", "paper"), ("LIVE", "live-small")],
)
def test_same_contract_is_lane_and_strategy_neutral(lane_kind: str, lane_id: str) -> None:
    plan, risk, snapshot, policy = _plan(lane_kind=lane_kind, lane_id=lane_id)

    assert plan["schema_version"] == LANE_EXACT_PLAN_SCHEMA
    assert plan["lane_kind"] == lane_kind
    assert plan["lane_id"] == lane_id
    assert plan["status"] == "ADVISORY"
    assert plan["execution_authority"] is False
    assert plan["risk_package_hash"] == risk["content_hash"]
    assert plan["broker_snapshot_hash"] == snapshot["content_hash"]
    assert plan["execution_policy_hash"] == risk["execution_policy_hash"]
    assert validate_lane_exact_execution_plan(
        plan,
        lane_risk_package=risk,
        broker_snapshot=snapshot,
        governed_lane_policy=policy,
        as_of="2026-08-18T11:09:00+00:00",
    ) == []


def test_plan_derives_exact_orders_from_risk_target_and_broker_truth() -> None:
    plan, _, _, _ = _plan()

    assert [(row["symbol"], row["quantity"]) for row in plan["sell_orders"]] == [
        ("NVDA", 5.0)
    ]
    assert [(row["symbol"], row["quantity"]) for row in plan["buy_orders"]] == [
        ("AAPL", 3.0),
        ("MSFT", 5.0),
    ]
    assert plan["expected_posttrade_cash"] == 200.0
    assert {
        row["symbol"]: row["quantity"] for row in plan["expected_posttrade_positions"]
    } == {"AAPL": 3.0, "MSFT": 5.0, "NVDA": 5.0}


def test_full_account_nav_scales_targets_to_fractional_quantities() -> None:
    policy = _lane_policy(lane_kind="LIVE", lane_id="generic-live-v1")
    policy["execution_policy"].update({
        "allow_fractional_shares": True,
        "quantity_precision": 6,
        "minimum_order_notional_usd": 1.0,
        "maximum_order_notional_usd": 437.855,
        "maximum_total_buy_notional_usd": 437.855,
    })
    target = _target(policy)
    snapshot = _snapshot(target, policy, with_position=False)
    snapshot["equity"] = 460.90
    snapshot["cash"] = 460.90
    snapshot["content_hash"] = artifact_content_hash(snapshot)
    risk = build_lane_risk_package(
        lane_target_package=target,
        account_state_hash=snapshot["content_hash"],
        decision="APPROVE",
        evaluated_at="2026-08-18T11:07:00+00:00",
    )

    plan = build_lane_exact_execution_plan(
        lane_risk_package=risk,
        broker_snapshot=snapshot,
        governed_lane_policy=policy,
        planned_at="2026-08-18T11:08:00+00:00",
    )

    assert plan["capital_basis"] == "FULL_ACCOUNT_EQUITY"
    assert plan["deployable_capital"] == 460.90
    assert plan["constraints"]["allow_fractional_shares"] is True
    assert any(
        float(order["quantity"]) != int(float(order["quantity"]))
        for order in plan["buy_orders"]
    )
    assert sum(float(order["notional"]) for order in plan["buy_orders"]) <= 437.855


def test_every_order_preserves_causal_sleeve_and_decision_lineage() -> None:
    plan, risk, snapshot, _ = _plan()
    aapl_order = next(row for row in plan["buy_orders"] if row["symbol"] == "AAPL")
    risk_aapl = next(row for row in risk["approved_target_rows"] if row["symbol"] == "AAPL")
    assert [row["sleeve_id"] for row in aapl_order["sleeve_contributions"]] == [
        row["sleeve_id"] for row in risk_aapl["sleeve_contributions"]
    ]
    assert {
        (row["sleeve_id"], row["decision_id"], row["decision_hash"])
        for row in aapl_order["sleeve_contributions"]
    } == {
        (row["sleeve_id"], row["decision_id"], row["decision_hash"])
        for row in risk_aapl["sleeve_contributions"]
    }
    sell = plan["sell_orders"][0]
    assert sell["sleeve_contributions"][0]["decision_id"] == (
        snapshot["positions"][0]["sleeve_contributions"][0]["decision_id"]
    )
    assert sum(row["order_quantity"] for row in sell["sleeve_contributions"]) == 5.0


def test_constrained_risk_target_is_the_only_target_used() -> None:
    plan, risk, _, _ = _plan(risk_decision="CONSTRAIN")

    assert plan["risk_decision"] == "CONSTRAIN"
    assert plan["approved_target_hash"] == risk["approved_target_hash"]
    assert {row["symbol"] for row in plan["expected_posttrade_positions"]} == {
        "AAPL",
        "MSFT",
    }
    assert plan["sell_orders"][0]["symbol"] == "NVDA"
    assert plan["sell_orders"][0]["quantity"] == 10.0


def test_reject_risk_never_builds_a_plan() -> None:
    risk, snapshot, policy = _sources(risk_decision="REJECT")
    with pytest.raises(LaneExactPlanError, match="Risk REJECT"):
        build_lane_exact_execution_plan(
            lane_risk_package=risk,
            broker_snapshot=snapshot,
            governed_lane_policy=policy,
            planned_at="2026-08-18T11:08:00+00:00",
        )


@pytest.mark.parametrize("source", ["risk", "snapshot", "policy"])
def test_tampered_or_mismatched_sources_fail_closed(source: str) -> None:
    risk, snapshot, policy = _sources()
    if source == "risk":
        risk["constraints"]["forged"] = True
    elif source == "snapshot":
        snapshot["cash"] = 499.0
        snapshot["content_hash"] = artifact_content_hash(snapshot)
    else:
        policy["execution_policy"]["maximum_orders"] = 21

    with pytest.raises(LaneExactPlanError, match="invalid|hash|differ|bind"):
        build_lane_exact_execution_plan(
            lane_risk_package=risk,
            broker_snapshot=snapshot,
            governed_lane_policy=policy,
            planned_at="2026-08-18T11:08:00+00:00",
        )


def test_stale_risk_snapshot_prices_and_expired_plan_fail_closed() -> None:
    risk, snapshot, policy = _sources()
    with pytest.raises(LaneExactPlanError, match="stale"):
        build_lane_exact_execution_plan(
            lane_risk_package=risk,
            broker_snapshot=snapshot,
            governed_lane_policy=policy,
            planned_at="2026-08-18T11:20:00+00:00",
        )

    plan = build_lane_exact_execution_plan(
        lane_risk_package=risk,
        broker_snapshot=snapshot,
        governed_lane_policy=policy,
        planned_at="2026-08-18T11:08:00+00:00",
    )
    failures = validate_lane_exact_execution_plan(
        plan, as_of="2026-08-18T11:11:00+00:00"
    )
    assert failures and "stale" in failures[0]


def test_plan_tampering_is_detected_even_if_content_hash_is_resealed() -> None:
    plan, risk, snapshot, policy = _plan()
    tampered = copy.deepcopy(plan)
    tampered["buy_orders"][0]["quantity"] += 1.0
    tampered["buy_orders"][0]["notional"] += 100.0
    tampered["content_hash"] = lane_exact_plan_content_hash(tampered)

    failures = validate_lane_exact_execution_plan(
        tampered,
        lane_risk_package=risk,
        broker_snapshot=snapshot,
        governed_lane_policy=policy,
    )
    assert failures
    assert any(
        marker in failures[0]
        for marker in ("contribution quantities", "order_id", "plan_id")
    )


def test_strict_immutable_write_and_read(tmp_path) -> None:
    plan, _, _, _ = _plan()
    path = tmp_path / "plan-v4.json"
    write_lane_exact_execution_plan(path, plan)

    assert read_lane_exact_execution_plan(path) == plan
    with pytest.raises(LaneExactPlanError, match="already exists"):
        write_lane_exact_execution_plan(path, plan)
