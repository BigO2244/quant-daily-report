from __future__ import annotations

import copy
import json
from pathlib import Path

from core import execution_equality_gate as gate


def _hash(orders: list[dict[str, object]], *, pricing_asof: str = "2026-05-27") -> str:
    value, _envelope, _serialized = gate.hash_order_set(
        orders,
        planning_price_basis="PREV_CLOSE",
        pricing_asof=pricing_asof,
    )
    return value


def test_order_set_hash_is_order_insensitive() -> None:
    orders_a = [
        {"ticker": "MSFT", "side": "BUY", "shares": 2},
        {"ticker": "AAPL", "side": "SELL", "shares": 1},
    ]
    orders_b = list(reversed(orders_a))

    assert _hash(orders_a) == _hash(orders_b)


def test_ticker_symbol_aliases_unknown_fields_and_quantity_format_are_stable() -> None:
    planned = [
        {
            "ticker": "AAPL",
            "side": "BUY",
            "shares": "1.0000",
            "entry_price": "200.00",
            "model_reason": "ignored",
        }
    ]
    submission = [
        {
            "symbol": "AAPL",
            "side": "buy",
            "qty": 1,
            "order_type": "MKT",
            "time_in_force": "DAY",
            "client_order_id": "ignored",
            "filled_qty": "ignored",
        }
    ]

    assert _hash(planned) == _hash(submission)


def test_identity_field_perturbation_changes_hash() -> None:
    base = [{"ticker": "AAPL", "side": "BUY", "shares": 1}]
    changed = [{"ticker": "AAPL", "side": "BUY", "shares": 2}]

    assert _hash(base) != _hash(changed)


def test_normalization_does_not_mutate_inputs() -> None:
    orders = [{"ticker": "AAPL", "side": "BUY", "shares": "1.00", "unknown": {"a": 1}}]
    before = copy.deepcopy(orders)

    _hash(orders)

    assert orders == before


def test_observe_decision_matching_plan_would_proceed() -> None:
    artifact = gate.evaluate_observe_decision(
        planned_orders=[{"ticker": "AAPL", "side": "BUY", "shares": 1, "entry_price": 200}],
        submission_orders=[{"symbol": "AAPL", "side": "BUY", "quantity": "1.0", "order_type": "MKT"}],
        execution_source="planned_payload_exact",
        planning_price_basis="PREV_CLOSE",
        pricing_asof_planned="2026-05-27",
        pricing_asof_context="2026-05-27",
    )

    assert artifact["decision"] == gate.DECISION_WOULD_PROCEED
    assert artifact["would_block"] is False
    assert artifact["hashes_equal"] is True


def test_observe_decision_quantity_mismatch_would_halt_hash_mismatch() -> None:
    artifact = gate.evaluate_observe_decision(
        planned_orders=[{"ticker": "AAPL", "side": "BUY", "shares": 1}],
        submission_orders=[{"ticker": "AAPL", "side": "BUY", "quantity": 2}],
        execution_source="planned_payload_exact",
        planning_price_basis="PREV_CLOSE",
        pricing_asof_planned="2026-05-27",
        pricing_asof_context="2026-05-27",
    )

    assert artifact["decision"] == gate.DECISION_HASH_MISMATCH
    assert artifact["would_block"] is True
    assert artifact["hashes_equal"] is False
    assert artifact["first_divergence"]["changed_fields"] == ["quantity"]


def test_observe_decision_source_mismatch_takes_precedence() -> None:
    artifact = gate.evaluate_observe_decision(
        planned_orders=[{"ticker": "AAPL", "side": "BUY", "shares": 1}],
        submission_orders=[{"ticker": "AAPL", "side": "BUY", "quantity": 2}],
        execution_source="rebuilt_from_signals",
        planning_price_basis="PREV_CLOSE",
        pricing_asof_planned="2026-05-27",
        pricing_asof_context="2026-05-26",
    )

    assert artifact["decision"] == gate.DECISION_SOURCE_MISMATCH
    assert artifact["halt_reason"] == "execution_source_mismatch"


def test_observe_decision_pricing_asof_mismatch_precedes_hash_mismatch() -> None:
    artifact = gate.evaluate_observe_decision(
        planned_orders=[{"ticker": "AAPL", "side": "BUY", "shares": 1}],
        submission_orders=[{"ticker": "AAPL", "side": "BUY", "quantity": 2}],
        execution_source="planned_payload_exact",
        planning_price_basis="PREV_CLOSE",
        pricing_asof_planned="2026-05-27",
        pricing_asof_context="2026-05-26",
    )

    assert artifact["decision"] == gate.DECISION_PRICING_ASOF_MISMATCH
    assert artifact["halt_reason"] == "pricing_asof_mismatch"


def test_observe_decision_internal_error_precedes_other_states(monkeypatch) -> None:
    def _raise(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(gate, "hash_order_set", _raise)

    artifact = gate.evaluate_observe_decision(
        planned_orders=[{"ticker": "AAPL", "side": "BUY", "shares": 1}],
        submission_orders=[{"ticker": "AAPL", "side": "BUY", "quantity": 2}],
        execution_source="rebuilt_from_signals",
        planning_price_basis="PREV_CLOSE",
        pricing_asof_planned="2026-05-27",
        pricing_asof_context="2026-05-26",
    )

    assert artifact["decision"] == gate.DECISION_OBSERVE_ERROR
    assert artifact["would_block"] is None
    assert artifact["observe_error"]["message"] == "boom"


def test_equality_gate_artifacts_and_operator_summary_block(tmp_path: Path) -> None:
    json_path, md_path, artifact = gate.write_equality_gate_observe_artifacts(
        run_root=tmp_path,
        planned_orders=[{"ticker": "AAPL", "side": "BUY", "shares": 1}],
        submission_orders=[{"ticker": "AAPL", "side": "BUY", "quantity": 1}],
        execution_source="planned_payload_exact",
        planning_price_basis="PREV_CLOSE",
        pricing_asof_planned="2026-05-27",
        pricing_asof_context="2026-05-27",
        run_id="run-1",
        trade_date="2026-05-28",
        artifact_refs={"planned_execution_payload": "outputs/precompute/2026-05-28/planned_execution_payload.json"},
    )

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    block = gate.operator_summary_block_from_artifact(artifact, artifact_ref=json_path)

    assert payload["schema_version"] == gate.SCHEMA_VERSION
    assert payload["mode"] == "observe"
    assert payload["enforced"] is False
    assert payload["submission_proceeded"] is True
    assert payload["decision"] == gate.DECISION_WOULD_PROCEED
    assert md_path.exists()
    assert block["decision"] == gate.DECISION_WOULD_PROCEED
    assert block["artifact_ref"] == str(json_path)
