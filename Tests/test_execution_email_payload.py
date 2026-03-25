from __future__ import annotations

import daily_quant_report as dqr


def test_build_execution_email_payload_uses_submitted_orders_for_partial_alpaca_run() -> None:
    daily_snapshot = {
        "holdings": [
            {"ticker": "ABBV", "shares": 0.9689, "last_price": 205.2},
            {"ticker": "VZ", "shares": 9.8764, "last_price": 50.91},
        ],
        "risk_levels": [
            {"ticker": "ABBV", "entry_price": 206.76, "stop_loss": None, "take_profit": None},
            {"ticker": "PSX", "entry_price": 182.80, "stop_loss": None, "take_profit": None},
            {"ticker": "VZ", "entry_price": 50.61, "stop_loss": None, "take_profit": None},
            {"ticker": "WBD", "entry_price": 27.22, "stop_loss": None, "take_profit": None},
        ],
        "proposed_trades": [],
    }
    paper_summary = {
        "trading_mode": "ALPACA",
        "execution_outcome": "post_submit_artifact_failure",
        "execution_reason": "post_sell_account_snapshot_write_failed",
        "halt_reason": "post_submit_artifact_failure:post_sell_account_snapshot_write_failed:cash_rebalance_incomplete",
        "cash_rebalance_status": "cash_rebalance_incomplete",
        "market_status": "OPEN",
        "planned_for": "2026-03-25T09:35:15.659989-04:00",
        "execution_trades": [
            {"ticker": "ABBV", "side": "SELL", "shares": 2.0, "price": 206.76, "notional": 413.52, "reason": "removed_from_targets"},
            {"ticker": "PSX", "side": "SELL", "shares": 1.0, "price": 182.80, "notional": 182.80, "reason": "removed_from_targets"},
            {"ticker": "VZ", "side": "SELL", "shares": 6.0, "price": 50.61, "notional": 303.66, "reason": "rebalance_to_target"},
            {"ticker": "WBD", "side": "SELL", "shares": 15.0, "price": 27.22, "notional": 408.30, "reason": "removed_from_targets"},
        ],
        "alpaca_submissions": [
            {"ticker": "WBD", "side": "SELL", "quantity": 15.0, "order_id": "run:WBD:SELL"},
            {"ticker": "ABBV", "side": "SELL", "quantity": 2.0, "order_id": "run:ABBV:SELL"},
            {"ticker": "VZ", "side": "SELL", "quantity": 6.0, "order_id": "run:VZ:SELL"},
            {"ticker": "PSX", "side": "SELL", "quantity": 1.0, "order_id": "run:PSX:SELL"},
        ],
        "alpaca_submission_summary": {
            "submit_success": 4,
            "submit_failed": 0,
        },
    }

    payload = dqr.build_execution_email_payload(
        trade_date="2026-03-25",
        daily_snapshot=daily_snapshot,
        paper_summary=paper_summary,
    )

    tickers = [trade["ticker"] for trade in payload["trades"]]
    assert tickers == ["ABBV", "PSX", "VZ", "WBD"]
    assert payload["execution_eligible_trades_count"] == 4
    assert payload["orders_submitted_count"] == 4
    assert payload["submitted_count"] == 4
