from __future__ import annotations

import json
from pathlib import Path

from core.execution_target_attainment import build_execution_target_attainment


TRADE_DATE = "2026-06-29"
RUN_ID = "2026-06-29T093507-0400_965aa63"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_target_attainment_separates_resized_suppressed_and_missing_buys(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    run_root = outputs / "runs" / RUN_ID
    payload_orders = [
        {"ticker": "GE", "side": "SELL", "shares": 1, "price": 250.0, "status": "FILLED"},
        {"ticker": "C", "side": "SELL", "shares": 2, "price": 95.0, "status": "FILLED"},
        {"ticker": "CVS", "side": "SELL", "shares": 2, "price": 62.0, "status": "FILLED"},
        {"ticker": "EOG", "side": "BUY", "shares": 2.087315765, "price": 132.60, "status": "FILLED"},
        {"ticker": "SPG", "side": "BUY", "shares": 2.976602846, "price": 226.89, "status": "FILLED"},
    ]
    intended_orders = [
        {"ticker": "GE", "side": "SELL", "shares": 1, "price": 250.0},
        {"ticker": "C", "side": "SELL", "shares": 2, "price": 95.0},
        {"ticker": "CVS", "side": "SELL", "shares": 2, "price": 62.0},
        {"ticker": "EOG", "side": "BUY", "shares": 6, "price": 132.60},
        {"ticker": "SPG", "side": "BUY", "shares": 3, "price": 226.89},
        {"ticker": "VZ", "side": "BUY", "shares": 11, "price": 45.27},
        {"ticker": "ABBV", "side": "BUY", "shares": 2, "price": 185.75},
        {"ticker": "JCI", "side": "BUY", "shares": 3, "price": 106.00},
        {"ticker": "MS", "side": "BUY", "shares": 1, "price": 141.00},
    ]

    _write_json(
        run_root / "execution_payload.json",
        {
            "trade_date": TRADE_DATE,
            "run_id": RUN_ID,
            "execution_status": "EXECUTED",
            "submitted_count": 5,
            "accepted_count": 5,
            "rejected_count": 0,
            "submitted_buy_count": 2,
            "submitted_sell_count": 3,
            "cash_target_weight": 0.05,
            "trades": payload_orders,
        },
    )
    _write_json(
        run_root / "execution_results.json",
        {
            "trade_date": TRADE_DATE,
            "run_id": RUN_ID,
            "status": "EXECUTED",
            "submitted_count": 5,
            "accepted_count": 5,
            "rejected_count": 0,
            "filled_buy_count": 2,
            "broker_responses": payload_orders,
        },
    )
    _write_json(
        run_root / "operator_summary.json",
        {"trade_date": TRADE_DATE, "run_id": RUN_ID, "terminal_status": "success"},
    )
    _write_json(
        run_root / "broker" / f"intended_orders_{TRADE_DATE}.json",
        {
            "report_date": TRADE_DATE,
            "orders_intended_count": len(intended_orders),
            "orders_intended": intended_orders,
        },
    )
    _write_json(
        run_root / "broker" / f"post_sell_rebudget_{TRADE_DATE}.json",
        {
            "trade_date": TRADE_DATE,
            "target_cash_weight": 0.05,
            "estimated_ending_cash": 542.00,
            "post_sell_equity": 10860.00,
            "final_submitted_buy_notional": 952.14,
            "final_buy_orders_submitted": [
                {
                    "ticker": "EOG",
                    "side": "BUY",
                    "shares": 2.087315765,
                    "price": 132.60,
                    "notional": 276.78,
                    "reason": "post_sell_rebudget_capital_clipped",
                },
                {
                    "ticker": "SPG",
                    "side": "BUY",
                    "shares": 2.976602846,
                    "price": 226.89,
                    "notional": 675.36,
                    "reason": "post_sell_rebudget_capital_clipped",
                },
            ],
            "skipped_buy_orders": [
                {
                    "ticker": "ABBV",
                    "side": "BUY",
                    "shares": 2,
                    "price": 185.75,
                    "notional": 371.50,
                    "block_reason": "buy_blocked_insufficient_buying_power",
                },
                {
                    "ticker": "JCI",
                    "side": "BUY",
                    "shares": 3,
                    "price": 106.00,
                    "notional": 318.00,
                    "block_reason": "buy_blocked_insufficient_buying_power",
                },
                {
                    "ticker": "MS",
                    "side": "BUY",
                    "shares": 1,
                    "price": 141.00,
                    "notional": 141.00,
                    "block_reason": "buy_blocked_insufficient_buying_power",
                },
                {
                    "ticker": "VZ",
                    "side": "BUY",
                    "shares": 11,
                    "price": 45.27,
                    "notional": 497.97,
                    "block_reason": "buy_blocked_insufficient_buying_power",
                },
            ],
        },
    )
    _write_json(
        run_root / "broker" / f"recon_posttrade_{TRADE_DATE}.json",
        {"drift_status": "OK_RECONCILED", "broker_cash": 541.93, "broker_equity": 10860.00},
    )
    _write_json(
        run_root / "broker" / "posttrade_account_snapshot.json",
        {"cash": 541.93, "equity": 10860.00, "captured_at": "2026-06-29T13:36:00+00:00"},
    )
    _write_json(
        run_root / "audit" / "execution_integrity.json",
        {
            "status": "WARN",
            "missing_buy_orders": [
                {"ticker": "ABBV", "side": "BUY", "shares": 2},
                {"ticker": "EOG", "side": "BUY", "shares": 6},
                {"ticker": "JCI", "side": "BUY", "shares": 3},
                {"ticker": "MS", "side": "BUY", "shares": 1},
                {"ticker": "SPG", "side": "BUY", "shares": 3},
                {"ticker": "VZ", "side": "BUY", "shares": 11},
            ],
        },
    )

    artifact = build_execution_target_attainment(
        outputs_root=outputs,
        trade_date=TRADE_DATE,
        run_id=RUN_ID,
    )

    assert artifact["status"] == "OK_TARGET_ATTAINED"
    assert {row["ticker"] for row in artifact["resized_intended_buys"]} == {"EOG", "SPG"}
    assert {row["ticker"] for row in artifact["suppressed_intended_buys"]} == {"ABBV", "JCI", "MS", "VZ"}
    assert artifact["missing_intended_buys"] == []


def test_target_attainment_classifies_partial_fill_underdeployment_as_action_required(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    run_id = "2026-06-30T093510-0400_18213b0"
    trade_date = "2026-06-30"
    run_root = outputs / "runs" / run_id

    intended_orders = [
        {"ticker": "MDLZ", "side": "BUY", "shares": 16, "price": 60.15},
        {"ticker": "PNC", "side": "BUY", "shares": 2, "price": 246.73},
        {"ticker": "ABBV", "side": "BUY", "shares": 2, "price": 248.63},
        {"ticker": "ABNB", "side": "BUY", "shares": 3, "price": 177.15},
        {"ticker": "GE", "side": "BUY", "shares": 1, "price": 552.28},
        {"ticker": "PANW", "side": "BUY", "shares": 1, "price": 309.91},
    ]
    order_lifecycle = [
        {"ticker": "MDLZ", "side": "BUY", "qty": 15.643930139897845, "latest_status": "OrderStatus.PARTIALLY_FILLED", "filled_qty": "4"},
        {"ticker": "PNC", "side": "BUY", "qty": 2.7459387662013923, "latest_status": "OrderStatus.FILLED", "filled_qty": "2.745938766"},
    ]

    _write_json(
        run_root / "execution_payload.json",
        {
            "trade_date": trade_date,
            "run_id": run_id,
            "execution_status": "EXECUTED",
            "submitted_count": 5,
            "accepted_count": 5,
            "rejected_count": 0,
            "submitted_buy_count": 2,
            "submitted_sell_count": 3,
            "cash_target_weight": 0.05,
            "buy_phase_status": "BUY_PHASE_PARTIAL",
            "buy_phase_completion_reason": "partial_buy_order_completion",
            "order_lifecycle": order_lifecycle,
        },
    )
    _write_json(
        run_root / "execution_results.json",
        {
            "trade_date": trade_date,
            "run_id": run_id,
            "status": "EXECUTED",
            "submitted_count": 5,
            "accepted_count": 5,
            "rejected_count": 0,
            "submitted_buy_count": 2,
            "filled_buy_count": 2,
            "pending_buy_count": 0,
            "buy_phase_status": "BUY_PHASE_PARTIAL",
            "buy_phase_completion_reason": "partial_buy_order_completion",
            "order_lifecycle": order_lifecycle,
            "broker_responses": order_lifecycle,
        },
    )
    _write_json(
        run_root / "operator_summary.json",
        {"trade_date": trade_date, "run_id": run_id, "terminal_status": "success"},
    )
    _write_json(
        run_root / "broker" / f"intended_orders_{trade_date}.json",
        {"report_date": trade_date, "orders_intended": intended_orders},
    )
    _write_json(
        run_root / "broker" / f"post_sell_rebudget_{trade_date}.json",
        {
            "trade_date": trade_date,
            "status": "REBUILT",
            "target_cash_weight": 0.05,
            "post_sell_cash": 2200.05,
            "post_sell_equity": 10941.45,
            "risk_cash_target": 547.07,
            "estimated_ending_cash": 581.56,
            "final_submitted_buy_notional": 1618.49,
            "final_buy_orders_submitted": [
                {"ticker": "MDLZ", "side": "BUY", "quantity": 15.643930139897845, "notional": 940.98, "reason": "post_sell_rebudget"},
                {"ticker": "PNC", "side": "BUY", "quantity": 2.7459387662013923, "notional": 677.51, "reason": "post_sell_rebudget"},
            ],
            "skipped_buy_orders": [
                {"ticker": "ABBV", "side": "BUY", "quantity": 2, "notional": 497.27, "block_reason": "min_trade_dollars_after_budget_clip"},
                {"ticker": "ABNB", "side": "BUY", "quantity": 3, "notional": 531.45, "block_reason": "min_trade_dollars_after_budget_clip"},
                {"ticker": "GE", "side": "BUY", "quantity": 1, "notional": 552.28, "block_reason": "min_trade_dollars_after_budget_clip"},
                {"ticker": "PANW", "side": "BUY", "quantity": 1, "notional": 309.91, "block_reason": "min_trade_dollars_after_budget_clip"},
            ],
        },
    )
    _write_json(
        run_root / "broker" / f"recon_posttrade_{trade_date}.json",
        {"drift_status": "OK_RECONCILED", "broker_cash": 1289.57, "broker_equity": 10934.34},
    )
    _write_json(
        run_root / "broker" / "posttrade_account_snapshot.json",
        {"cash": 1289.57, "equity": 10934.34, "captured_at": "2026-06-30T13:36:24+00:00"},
    )

    artifact = build_execution_target_attainment(
        outputs_root=outputs,
        trade_date=trade_date,
        run_id=run_id,
    )

    assert artifact["status"] == "WARN_UNDERDEPLOYED_PENDING_BUY_FILLS"
    assert artifact["underdeployment_classification"] == "pending_incomplete_fill_timing"
    assert artifact["underdeployment_reason_code"] == "underdeployment_pending_incomplete_buy_fills"
    assert artifact["action_required"] is True
    assert artifact["partial_buy_count"] == 1
    assert artifact["pending_buy_count"] == 1
    assert artifact["residual_undeployed_cash"] == 742.85
    assert {row["ticker"] for row in artifact["suppressed_intended_buys"]} == {"ABBV", "ABNB", "GE", "PANW"}
    assert "pending or incomplete buy fills" in artifact["warnings"]
