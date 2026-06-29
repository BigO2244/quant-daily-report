from __future__ import annotations

import json
from pathlib import Path

from core.execution_integrity import (
    validate_execution_integrity,
    write_execution_integrity_audit,
)


def _order(ticker: str, side: str, shares: float = 1.0) -> dict[str, object]:
    return {
        "ticker": ticker,
        "side": side,
        "shares": shares,
        "order_id": f"run:{ticker}:{side}",
    }


def _order_without_id(ticker: str, side: str, shares: float = 1.0) -> dict[str, object]:
    return {
        "ticker": ticker,
        "side": side,
        "shares": shares,
    }


def _intended(orders: list[dict[str, object]]) -> dict[str, object]:
    return {
        "report_date": "2026-05-27",
        "run_id": "run-fr031",
        "orders_intended_count": len(orders),
        "orders_intended": orders,
        "execution_enabled": True,
        "execution_blocked": False,
        "block_reasons": [],
    }


def _payload(orders: list[dict[str, object]], **extra: object) -> dict[str, object]:
    submitted_buy_count = sum(1 for order in orders if order["side"] == "BUY")
    submitted_sell_count = sum(1 for order in orders if order["side"] == "SELL")
    payload = {
        "trade_date": "2026-05-27",
        "run_id": "run-fr031",
        "execution_status": "EXECUTED",
        "trades": orders,
        "execution_eligible_trades_count": len(orders),
        "submitted_count": len(orders),
        "accepted_count": len(orders),
        "rejected_count": 0,
        "submitted_buy_count": submitted_buy_count,
        "submitted_sell_count": submitted_sell_count,
        "pending_buy_count": 0,
    }
    payload.update(extra)
    return payload


def _results(orders: list[dict[str, object]], **extra: object) -> dict[str, object]:
    payload = {
        "trade_date": "2026-05-27",
        "run_id": "run-fr031",
        "status": "EXECUTED",
        "submitted_count": len(orders),
        "accepted_count": len(orders),
        "rejected_count": 0,
        "broker_responses": [
            {"ticker": order["ticker"], "side": order["side"], "status": "ACCEPTED"}
            for order in orders
        ],
    }
    payload.update(extra)
    return payload


def test_mixed_buy_sell_intended_orders_preserved_in_execution_payload() -> None:
    orders = [_order("CVS", "SELL"), _order("ELV", "BUY"), _order("SLB", "BUY")]

    audit = validate_execution_integrity(
        trade_date="2026-05-27",
        run_id="run-fr031",
        intended_orders=_intended(orders),
        execution_payload=_payload(orders),
        execution_results=_results(orders),
        operator_summary={"terminal_status": "success"},
    )

    assert audit["status"] == "OK"
    assert audit["intended_buy_count"] == 2
    assert audit["execution_payload_buy_count"] == 2
    assert audit["missing_buy_orders"] == []


def test_generated_payload_order_ids_do_not_create_false_missing_buys() -> None:
    intended_orders = [
        _order_without_id("ABNB", "BUY", 4),
        _order_without_id("ELV", "SELL", 1),
    ]
    payload_orders = [
        _order("ABNB", "BUY", 4),
        _order("ELV", "SELL", 1),
    ]

    audit = validate_execution_integrity(
        trade_date="2026-05-27",
        run_id="run-fr031",
        intended_orders=_intended(intended_orders),
        execution_payload=_payload(payload_orders, execution_source="planned_payload_exact"),
        execution_results=_results(payload_orders),
        operator_summary={"terminal_status": "success"},
    )

    assert audit["status"] == "OK"
    assert audit["missing_intended_orders"] == []
    assert audit["missing_buy_orders"] == []
    assert audit["unexpected_payload_orders"] == []
    assert "intended_buy_missing_from_payload_with_exception" not in {
        finding["code"] for finding in audit["findings"]
    }


def test_intended_buy_missing_from_payload_produces_fail() -> None:
    intended_orders = [_order("CVS", "SELL"), _order("ELV", "BUY"), _order("SLB", "BUY")]
    payload_orders = [_order("CVS", "SELL")]

    audit = validate_execution_integrity(
        trade_date="2026-05-27",
        run_id="run-fr031",
        intended_orders=_intended(intended_orders),
        execution_payload=_payload(
            payload_orders,
            pending_buy_count=2,
            submitted_buy_count=0,
        ),
        execution_results=_results(payload_orders),
        operator_summary={"terminal_status": "success"},
    )

    assert audit["status"] == "FAIL"
    codes = {finding["code"] for finding in audit["findings"]}
    assert "intended_buy_missing_from_payload" in codes


def test_pending_buys_without_submitted_buys_cannot_be_clean_success() -> None:
    audit = validate_execution_integrity(
        trade_date="2026-05-27",
        run_id="run-fr031",
        intended_orders=_intended([_order("ELV", "BUY")]),
        execution_payload=_payload(
            [],
            execution_status="EXECUTED",
            execution_eligible_trades_count=0,
            submitted_count=0,
            accepted_count=0,
            pending_buy_count=1,
            submitted_buy_count=0,
        ),
        execution_results=_results([], submitted_count=0, accepted_count=0),
        operator_summary={"terminal_status": "success"},
    )

    assert audit["status"] == "FAIL"
    assert "pending_buys_without_submitted_buys" in {
        finding["code"] for finding in audit["findings"]
    }


def test_broker_response_count_mismatch_is_detected() -> None:
    orders = [_order("ELV", "BUY"), _order("SLB", "BUY")]

    audit = validate_execution_integrity(
        trade_date="2026-05-27",
        run_id="run-fr031",
        intended_orders=_intended(orders),
        execution_payload=_payload(orders),
        execution_results=_results(
            orders,
            broker_responses=[{"ticker": "ELV", "side": "BUY", "status": "ACCEPTED"}],
        ),
        operator_summary={"terminal_status": "success"},
    )

    assert audit["status"] == "WARN"
    assert "broker_response_count_mismatch" in {
        finding["code"] for finding in audit["findings"]
    }


def test_buy_only_continuation_with_intended_buys_and_submitted_buys_passes() -> None:
    intended_orders = [_order("CVS", "SELL"), _order("ELV", "BUY"), _order("SLB", "BUY")]
    payload_orders = [_order("ELV", "BUY"), _order("SLB", "BUY")]

    audit = validate_execution_integrity(
        trade_date="2026-05-27",
        run_id="run-fr031",
        intended_orders=_intended(intended_orders),
        execution_payload=_payload(
            payload_orders,
            continuation_mode="buy_only",
            continuation_source="intended_orders",
            continuation_intended_orders_path="outputs/broker/intended_orders_2026-05-27.json",
        ),
        execution_results=_results(payload_orders),
        operator_summary={"terminal_status": "success", "continuation_mode": "buy_only"},
    )

    assert audit["status"] == "OK"
    assert audit["continuation_mode"] == "buy_only"
    assert audit["continuation_side"] == "BUY"
    assert audit["missing_buy_orders"] == []


def test_cash_drift_above_threshold_produces_warning() -> None:
    orders = [_order("ELV", "BUY")]

    audit = validate_execution_integrity(
        trade_date="2026-05-27",
        run_id="run-fr031",
        intended_orders=_intended(orders),
        execution_payload=_payload(
            orders,
            cash_target_weight=0.05,
            achieved_cash_weight=0.12,
        ),
        execution_results=_results(orders),
        operator_summary={"terminal_status": "success"},
        cash_drift_threshold=0.025,
    )

    assert audit["status"] == "WARN"
    assert audit["cash_drift_warning"] is True
    assert "cash_target_drift" in {finding["code"] for finding in audit["findings"]}


def test_post_sell_rebudget_resized_and_suppressed_buys_are_not_missing() -> None:
    intended_orders = [
        _order_without_id("GE", "SELL", 1),
        _order_without_id("C", "SELL", 2),
        _order_without_id("CVS", "SELL", 2),
        _order_without_id("EOG", "BUY", 6),
        _order_without_id("SPG", "BUY", 3),
        _order_without_id("VZ", "BUY", 11),
        _order_without_id("ABBV", "BUY", 2),
        _order_without_id("JCI", "BUY", 3),
        _order_without_id("MS", "BUY", 1),
    ]
    payload_orders = [
        _order_without_id("GE", "SELL", 1),
        _order_without_id("C", "SELL", 2),
        _order_without_id("CVS", "SELL", 2),
        _order("EOG", "BUY", 2.087315765),
        _order("SPG", "BUY", 2.976602846),
    ]
    post_sell_rebudget = {
        "final_buy_orders_submitted": [
            {"ticker": "EOG", "side": "BUY", "shares": 2.087315765, "notional": 276.78, "reason": "post_sell_rebudget_capital_clipped"},
            {"ticker": "SPG", "side": "BUY", "shares": 2.976602846, "notional": 675.36, "reason": "post_sell_rebudget_capital_clipped"},
        ],
        "skipped_buy_orders": [
            {"ticker": "ABBV", "side": "BUY", "shares": 2, "notional": 371.50, "block_reason": "buy_blocked_insufficient_buying_power"},
            {"ticker": "JCI", "side": "BUY", "shares": 3, "notional": 318.00, "block_reason": "buy_blocked_insufficient_buying_power"},
            {"ticker": "MS", "side": "BUY", "shares": 1, "notional": 141.00, "block_reason": "buy_blocked_insufficient_buying_power"},
            {"ticker": "VZ", "side": "BUY", "shares": 11, "notional": 498.00, "block_reason": "buy_blocked_insufficient_buying_power"},
        ],
    }
    readiness = {
        "per_trade_diagnostics": {
            "skipped": [
                {"ticker": "MO", "side": "SELL", "requested_quantity": 1, "skip_reason": "min_notional", "stage": "execution_filter"},
                {"ticker": "NEE", "side": "SELL", "requested_quantity": 1, "skip_reason": "min_notional", "stage": "execution_filter"},
            ]
        }
    }

    audit = validate_execution_integrity(
        trade_date="2026-06-29",
        run_id="2026-06-29T093507-0400_965aa63",
        intended_orders=_intended(intended_orders),
        execution_payload=_payload(payload_orders, execution_source="post_sell_rebudget"),
        execution_results=_results(payload_orders),
        operator_summary={"terminal_status": "success"},
        post_sell_rebudget=post_sell_rebudget,
        readiness_certification=readiness,
    )

    assert audit["status"] == "OK"
    assert audit["lineage_count_reconciliation"] == "EXPLAINED"
    assert audit["missing_buy_orders"] == []
    assert audit["unexpected_payload_orders"] == []
    assert {row["ticker"] for row in audit["resized_buy_orders"]} == {"EOG", "SPG"}
    assert {row["ticker"] for row in audit["suppressed_buy_orders"]} == {"ABBV", "JCI", "MS", "VZ"}
    assert {row["ticker"] for row in audit["filtered_orders"]} == {"MO", "NEE"}
    assert "intended_payload_count_mismatch_with_exception" not in {
        finding["code"] for finding in audit["findings"]
    }


def test_write_execution_integrity_audit_writes_deterministic_artifact(tmp_path: Path) -> None:
    run_root = tmp_path / "outputs" / "runs" / "run-fr031"
    intended_path = run_root / "broker" / "intended_orders_2026-05-27.json"
    intended_path.parent.mkdir(parents=True)
    orders = [_order("ELV", "BUY")]
    intended_path.write_text(json.dumps(_intended(orders)), encoding="utf-8")
    (run_root / "execution_payload.json").write_text(json.dumps(_payload(orders)), encoding="utf-8")
    (run_root / "execution_results.json").write_text(json.dumps(_results(orders)), encoding="utf-8")
    (run_root / "operator_summary.json").write_text(
        json.dumps({"terminal_status": "success"}),
        encoding="utf-8",
    )

    out_path = write_execution_integrity_audit(
        run_root=run_root,
        trade_date="2026-05-27",
        run_id="run-fr031",
    )

    audit = json.loads(out_path.read_text(encoding="utf-8"))
    assert out_path == run_root / "audit" / "execution_integrity.json"
    assert audit["status"] == "OK"
    assert audit["source_artifacts"]["execution_payload"].endswith("execution_payload.json")
