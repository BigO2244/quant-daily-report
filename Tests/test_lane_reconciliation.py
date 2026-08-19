from __future__ import annotations

import copy
import hashlib

import pytest

from core.lane_oms import build_lane_oms_intents
from core.lane_reconciliation import (
    BROKER_FILL_EVIDENCE_SCHEMA,
    BROKER_ORDER_EVIDENCE_SCHEMA,
    ENDING_LANE_STATE_SCHEMA,
    LaneReconciliationError,
    build_lane_reconciliation,
    evidence_content_hash,
    seal_broker_fill_evidence,
    seal_broker_order_evidence,
    seal_ending_lane_state,
    validate_broker_order_evidence,
    validate_lane_reconciliation,
)
from Tests.test_exact_execution_plan_v4 import _plan


SOURCE_HASH = hashlib.sha256(b"broker-reconciliation-fixture").hexdigest()


def _scope(plan: dict) -> dict:
    return {
        "trade_date": plan["trade_date"],
        "account_id_hash": plan["account_id_hash"],
        "lane_id": plan["lane_id"],
        "lane_kind": plan["lane_kind"],
        "deployment_version": plan["deployment_version"],
        "plan_id": plan["plan_id"],
        "plan_hash": plan["content_hash"],
    }


def _order_evidence(
    plan: dict, order: dict, *, status: str, filled_quantity: float
) -> dict:
    return seal_broker_order_evidence(
        {
            "schema_version": BROKER_ORDER_EVIDENCE_SCHEMA,
            "observation_id": f"observation:{order['order_id']}",
            "observed_at": "2026-08-18T11:10:00+00:00",
            **_scope(plan),
            "order_id": order["order_id"],
            "client_order_id": order["client_order_id"],
            "broker_order_id": f"broker:{order['order_id']}",
            "status": status,
            "submitted_quantity": order["quantity"],
            "filled_quantity": filled_quantity,
            "source_hash": SOURCE_HASH,
        }
    )


def _fill(
    plan: dict,
    order: dict,
    *,
    quantity: float,
    suffix: str = "1",
    fee_amount: float = 0.0,
) -> dict:
    return seal_broker_fill_evidence(
        {
            "schema_version": BROKER_FILL_EVIDENCE_SCHEMA,
            "fill_id": f"fill:{order['order_id']}:{suffix}",
            "event_time": "2026-08-18T11:09:30+00:00",
            **_scope(plan),
            "order_id": order["order_id"],
            "client_order_id": order["client_order_id"],
            "broker_order_id": f"broker:{order['order_id']}",
            "symbol": order["symbol"],
            "side": order["side"],
            "quantity": quantity,
            "price": order["enforcement_price"],
            "fee_amount": fee_amount,
            "source_hash": SOURCE_HASH,
        }
    )


def _ending_state(plan: dict, fills: list[dict]) -> dict:
    positions = {
        row["symbol"]: float(row["quantity"]) for row in plan["starting_positions"]
    }
    cash = float(plan["starting_cash"])
    for fill in fills:
        direction = 1.0 if fill["side"] == "BUY" else -1.0
        positions[fill["symbol"]] = positions.get(fill["symbol"], 0.0) + direction * float(fill["quantity"])
        gross = float(fill["quantity"]) * float(fill["price"])
        cash += (
            -gross - float(fill["fee_amount"])
            if fill["side"] == "BUY"
            else gross - float(fill["fee_amount"])
        )
    positions = {symbol: qty for symbol, qty in positions.items() if qty > 1e-8}
    marks = {row["symbol"]: float(row["price"]) for row in plan["price_marks"]}
    position_rows = [
        {
            "symbol": symbol,
            "quantity": quantity,
            "mark": marks[symbol],
            "market_value": quantity * marks[symbol],
            "source_hash": SOURCE_HASH,
        }
        for symbol, quantity in sorted(positions.items())
    ]
    equity = cash + sum(row["market_value"] for row in position_rows)
    return seal_ending_lane_state(
        {
            "schema_version": ENDING_LANE_STATE_SCHEMA,
            "state_id": f"ending-state:{plan['lane_id']}",
            "as_of": "2026-08-18T11:11:00+00:00",
            **_scope(plan),
            "cash": cash,
            "equity": equity,
            "positions": position_rows,
            "source_hash": SOURCE_HASH,
        }
    )


def _full_evidence(plan: dict) -> tuple[list[dict], list[dict], dict]:
    planned = [*plan["sell_orders"], *plan["buy_orders"]]
    fills = [_fill(plan, order, quantity=order["quantity"]) for order in planned]
    orders = [
        _order_evidence(
            plan, order, status="FILLED", filled_quantity=order["quantity"]
        )
        for order in planned
    ]
    return orders, fills, _ending_state(plan, fills)


@pytest.mark.parametrize(
    ("lane_kind", "lane_id"),
    [("PAPER", "paper"), ("LIVE", "live-small")],
)
def test_full_execution_reconciles_plan_wal_fills_positions_cash_and_nav(
    lane_kind: str, lane_id: str
) -> None:
    plan, _, _, _ = _plan(lane_kind=lane_kind, lane_id=lane_id)
    orders, fills, state = _full_evidence(plan)
    reconciliation = build_lane_reconciliation(
        exact_plan=plan,
        wal_intents=build_lane_oms_intents(plan),
        broker_orders=orders,
        broker_fills=fills,
        ending_state=state,
        reconciled_at="2026-08-18T11:12:00+00:00",
    )

    assert reconciliation["status"] == "PASS"
    assert reconciliation["halt_required"] is False
    assert reconciliation["escalation_required"] is False
    assert reconciliation["accounting_ready"] is True
    assert reconciliation["position_reconciliation"]["status"] == "PASS"
    assert reconciliation["cash_reconciliation"]["status"] == "PASS"
    assert reconciliation["nav_reconciliation"]["status"] == "PASS"
    assert reconciliation["lane_kind"] == lane_kind
    assert validate_lane_reconciliation(reconciliation) == reconciliation
    assert validate_lane_reconciliation(reconciliation, exact_plan=plan) == reconciliation


def test_reconciled_fills_are_sleeve_split_and_accounting_ready() -> None:
    plan, _, _, _ = _plan()
    orders, fills, state = _full_evidence(plan)
    reconciliation = build_lane_reconciliation(
        exact_plan=plan,
        wal_intents=build_lane_oms_intents(plan),
        broker_orders=orders,
        broker_fills=fills,
        ending_state=state,
        reconciled_at="2026-08-18T11:12:00+00:00",
    )
    aapl_order = next(row for row in plan["buy_orders"] if row["symbol"] == "AAPL")
    aapl_rows = [
        row for row in reconciliation["reconciled_fills"] if row["symbol"] == "AAPL"
    ]

    assert len(aapl_rows) == len(aapl_order["sleeve_contributions"])
    assert sum(row["quantity"] for row in aapl_rows) == pytest.approx(aapl_order["quantity"])
    assert sum(row["gross_amount"] for row in aapl_rows) == pytest.approx(aapl_order["notional"])
    assert {row["sleeve_id"] for row in aapl_rows} == {
        row["sleeve_id"] for row in aapl_order["sleeve_contributions"]
    }
    assert all(row["session_id"] == plan["session_id"] for row in aapl_rows)
    assert all(row["allocation_id"] == plan["allocation_id"] for row in aapl_rows)
    assert all(row["plan_hash"] == plan["content_hash"] for row in aapl_rows)
    assert all(row["net_amount"] < 0 for row in aapl_rows)


def test_terminal_partial_execution_reconciles_but_halts_and_escalates() -> None:
    plan, _, _, _ = _plan()
    planned = [*plan["sell_orders"], *plan["buy_orders"]]
    sell = planned[0]
    aapl = next(row for row in planned if row["symbol"] == "AAPL")
    msft = next(row for row in planned if row["symbol"] == "MSFT")
    fills = [
        _fill(plan, sell, quantity=sell["quantity"]),
        _fill(plan, aapl, quantity=1.0),
    ]
    orders = [
        _order_evidence(plan, sell, status="FILLED", filled_quantity=sell["quantity"]),
        _order_evidence(plan, aapl, status="CANCELED", filled_quantity=1.0),
        _order_evidence(plan, msft, status="REJECTED", filled_quantity=0.0),
    ]
    reconciliation = build_lane_reconciliation(
        exact_plan=plan,
        wal_intents=build_lane_oms_intents(plan),
        broker_orders=orders,
        broker_fills=fills,
        ending_state=_ending_state(plan, fills),
        reconciled_at="2026-08-18T11:12:00+00:00",
    )

    assert reconciliation["status"] == "PARTIAL"
    assert reconciliation["halt_required"] is True
    assert reconciliation["escalation_required"] is True
    assert reconciliation["accounting_ready"] is True
    assert reconciliation["reason_codes"] == ["TERMINAL_PARTIAL_EXECUTION_RECONCILED"]


def test_terminal_total_rejection_is_reconciled_without_fabricated_fills() -> None:
    plan, _, _, _ = _plan()
    planned = [*plan["sell_orders"], *plan["buy_orders"]]
    orders = [
        _order_evidence(plan, order, status="REJECTED", filled_quantity=0.0)
        for order in planned
    ]
    reconciliation = build_lane_reconciliation(
        exact_plan=plan,
        wal_intents=build_lane_oms_intents(plan),
        broker_orders=orders,
        broker_fills=[],
        ending_state=_ending_state(plan, []),
        reconciled_at="2026-08-18T11:12:00+00:00",
    )

    assert reconciliation["status"] == "REJECTED"
    assert reconciliation["accounting_ready"] is False
    assert reconciliation["reconciled_fills"] == []
    assert reconciliation["halt_required"] is True


@pytest.mark.parametrize("break_kind", ["missing_order", "nonterminal", "cash"])
def test_ambiguity_or_economic_break_is_unresolved_and_never_accounting_ready(
    break_kind: str,
) -> None:
    plan, _, _, _ = _plan()
    orders, fills, state = _full_evidence(plan)
    if break_kind == "missing_order":
        orders = orders[:-1]
        fills = fills[:-1]
        state = _ending_state(plan, fills)
    elif break_kind == "nonterminal":
        order = [*plan["sell_orders"], *plan["buy_orders"]][0]
        orders[0] = _order_evidence(
            plan, order, status="ACCEPTED", filled_quantity=0.0
        )
        fills = [row for row in fills if row["order_id"] != order["order_id"]]
        state = _ending_state(plan, fills)
    else:
        state = copy.deepcopy(state)
        state["cash"] += 1.0
        state["equity"] += 1.0
        state["content_hash"] = evidence_content_hash(state)

    reconciliation = build_lane_reconciliation(
        exact_plan=plan,
        wal_intents=build_lane_oms_intents(plan),
        broker_orders=orders,
        broker_fills=fills,
        ending_state=state,
        reconciled_at="2026-08-18T11:12:00+00:00",
    )

    assert reconciliation["status"] == "UNRESOLVED"
    assert reconciliation["halt_required"] is True
    assert reconciliation["escalation_required"] is True
    assert reconciliation["accounting_ready"] is False
    assert reconciliation["reconciled_fills"] == []


def test_tampered_evidence_hash_and_scope_fail_before_reconciliation() -> None:
    plan, _, _, _ = _plan()
    orders, fills, state = _full_evidence(plan)
    fills[0]["quantity"] += 1.0
    with pytest.raises(LaneReconciliationError, match="content_hash mismatch"):
        build_lane_reconciliation(
            exact_plan=plan,
            wal_intents=build_lane_oms_intents(plan),
            broker_orders=orders,
            broker_fills=fills,
            ending_state=state,
            reconciled_at="2026-08-18T11:12:00+00:00",
        )


@pytest.mark.parametrize(
    ("status", "filled", "message"),
    [
        ("FILLED", 0.0, "completely filled"),
        ("REJECTED", 1.0, "cannot have a fill"),
        ("PARTIALLY_FILLED", 0.0, "strict partial"),
        ("ACCEPTED", 1.0, "cannot declare filled"),
    ],
)
def test_broker_order_status_and_quantity_must_be_consistent(
    status: str, filled: float, message: str
) -> None:
    plan, _, _, _ = _plan()
    order = [*plan["sell_orders"], *plan["buy_orders"]][0]
    valid = _order_evidence(plan, order, status="FILLED", filled_quantity=order["quantity"])
    valid["status"] = status
    valid["filled_quantity"] = filled
    valid["content_hash"] = evidence_content_hash(valid)

    with pytest.raises(LaneReconciliationError, match=message):
        validate_broker_order_evidence(valid)


def test_resealed_nested_summary_and_source_lineage_tampering_fail() -> None:
    plan, _, _, _ = _plan()
    orders, fills, state = _full_evidence(plan)
    reconciliation = build_lane_reconciliation(
        exact_plan=plan,
        wal_intents=build_lane_oms_intents(plan),
        broker_orders=orders,
        broker_fills=fills,
        ending_state=state,
        reconciled_at="2026-08-18T11:12:00+00:00",
    )

    malformed = copy.deepcopy(reconciliation)
    malformed["intended_orders"][0]["broker_status"] = "FILLED"
    malformed["content_hash"] = evidence_content_hash(malformed)
    with pytest.raises(LaneReconciliationError, match="fields mismatch"):
        validate_lane_reconciliation(malformed)

    wrong_source = copy.deepcopy(reconciliation)
    wrong_source["source_hashes"]["broker_fills"][0] = SOURCE_HASH
    wrong_source["content_hash"] = evidence_content_hash(wrong_source)
    with pytest.raises(LaneReconciliationError, match="source broker_fills lineage"):
        validate_lane_reconciliation(wrong_source)


def test_exact_plan_validation_blocks_resealed_sleeve_reassignment() -> None:
    plan, _, _, _ = _plan()
    orders, fills, state = _full_evidence(plan)
    reconciliation = build_lane_reconciliation(
        exact_plan=plan,
        wal_intents=build_lane_oms_intents(plan),
        broker_orders=orders,
        broker_fills=fills,
        ending_state=state,
        reconciled_at="2026-08-18T11:12:00+00:00",
    )
    forged = copy.deepcopy(reconciliation)
    forged_row = forged["reconciled_fills"][0]
    forged_row["sleeve_id"] = "forged_sleeve"
    forged_row["content_hash"] = evidence_content_hash(forged_row)
    forged["content_hash"] = evidence_content_hash(forged)

    # A standalone reader can verify artifact self-consistency; the accounting
    # bridge must supply the exact plan to prove causal attribution.
    assert validate_lane_reconciliation(forged) == forged
    with pytest.raises(LaneReconciliationError, match="absent from exact order"):
        validate_lane_reconciliation(forged, exact_plan=plan)
