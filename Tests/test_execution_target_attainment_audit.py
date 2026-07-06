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
                {"ticker": "EOG", "side": "BUY", "shares": 2.087315765, "price": 132.60, "notional": 276.78, "reason": "post_sell_rebudget_capital_clipped"},
                {"ticker": "SPG", "side": "BUY", "shares": 2.976602846, "price": 226.89, "notional": 675.36, "reason": "post_sell_rebudget_capital_clipped"},
            ],
            "skipped_buy_orders": [
                {"ticker": "ABBV", "side": "BUY", "shares": 2, "price": 185.75, "notional": 371.50, "block_reason": "buy_blocked_insufficient_buying_power"},
                {"ticker": "JCI", "side": "BUY", "shares": 3, "price": 106.00, "notional": 318.00, "block_reason": "buy_blocked_insufficient_buying_power"},
                {"ticker": "MS", "side": "BUY", "shares": 1, "price": 141.00, "notional": 141.00, "block_reason": "buy_blocked_insufficient_buying_power"},
                {"ticker": "VZ", "side": "BUY", "shares": 11, "price": 45.27, "notional": 497.97, "block_reason": "buy_blocked_insufficient_buying_power"},
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
