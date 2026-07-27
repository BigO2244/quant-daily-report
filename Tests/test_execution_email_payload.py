from __future__ import annotations

import daily_quant_report as dqr
from paper.build_execution_email import build_execution_email_text


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


def test_build_execution_email_payload_excludes_nonfinite_trade_price() -> None:
    payload = dqr.build_execution_email_payload(
        trade_date="2026-07-27",
        daily_snapshot={
            "holdings": [],
            "risk_levels": [
                {
                    "ticker": "KLAC",
                    "entry_price": float("nan"),
                    "stop_loss": float("nan"),
                    "take_profit": float("nan"),
                }
            ],
            "proposed_trades": [],
        },
        paper_summary={
            "trading_mode": "PAPER",
            "market_status": "OPEN",
            "execution_trades": [
                {
                    "ticker": "KLAC",
                    "side": "BUY",
                    "shares": 1,
                    "price": float("nan"),
                    "notional": float("nan"),
                }
            ],
        },
    )

    assert payload["trades"] == []
    assert payload["invalid_execution_trade_count"] == 1
    assert payload["invalid_execution_price_tickers"] == ["KLAC"]
    assert payload["invalid_execution_trades"][0]["reason"] == "missing_or_nonfinite_execution_price"


def test_execution_email_payload_preserves_fractional_submitted_quantities_and_lifecycle() -> None:
    daily_snapshot = {
        "holdings": [
            {"ticker": "GE", "shares": 1, "last_price": 250.0},
            {"ticker": "C", "shares": 2, "last_price": 95.0},
            {"ticker": "CVS", "shares": 2, "last_price": 62.0},
        ],
        "risk_levels": [
            {"ticker": "EOG", "entry_price": 132.60, "stop_loss": None, "take_profit": None},
            {"ticker": "SPG", "entry_price": 226.89, "stop_loss": None, "take_profit": None},
            {"ticker": "GE", "entry_price": 250.0, "stop_loss": None, "take_profit": None},
            {"ticker": "C", "entry_price": 95.0, "stop_loss": None, "take_profit": None},
            {"ticker": "CVS", "entry_price": 62.0, "stop_loss": None, "take_profit": None},
        ],
        "proposed_trades": [],
    }
    expected_submission_orders = [
        {"ticker": "GE", "side": "SELL", "quantity": 1, "stage": "expected_submission"},
        {"ticker": "C", "side": "SELL", "quantity": 2, "stage": "expected_submission"},
        {"ticker": "CVS", "side": "SELL", "quantity": 2, "stage": "expected_submission"},
        {"ticker": "EOG", "side": "BUY", "quantity": 6, "stage": "expected_submission"},
        {"ticker": "SPG", "side": "BUY", "quantity": 3, "stage": "expected_submission"},
        {"ticker": "ABBV", "side": "BUY", "quantity": 2, "stage": "expected_submission"},
        {"ticker": "JCI", "side": "BUY", "quantity": 3, "stage": "expected_submission"},
        {"ticker": "MS", "side": "BUY", "quantity": 1, "stage": "expected_submission"},
        {"ticker": "VZ", "side": "BUY", "quantity": 11, "stage": "expected_submission"},
    ]
    post_sell_rebudget = {
        "artifact_path": "outputs/runs/run/broker/post_sell_rebudget_2026-06-29.json",
        "final_buy_orders_submitted": [
            {"ticker": "EOG", "side": "BUY", "shares": 2.087315765, "notional": 276.78, "reason": "post_sell_rebudget_capital_clipped"},
            {"ticker": "SPG", "side": "BUY", "shares": 2.976602846, "notional": 675.36, "reason": "post_sell_rebudget_capital_clipped"},
        ],
        "skipped_buy_orders": [
            {"ticker": "ABBV", "side": "BUY", "shares": 2, "block_reason": "buy_blocked_insufficient_buying_power"},
            {"ticker": "JCI", "side": "BUY", "shares": 3, "block_reason": "buy_blocked_insufficient_buying_power"},
            {"ticker": "MS", "side": "BUY", "shares": 1, "block_reason": "buy_blocked_insufficient_buying_power"},
            {"ticker": "VZ", "side": "BUY", "shares": 11, "block_reason": "buy_blocked_insufficient_buying_power"},
        ],
    }
    paper_summary = {
        "trading_mode": "paper",
        "market_status": "OPEN",
        "execution_filter": {
            "raw": 11,
            "rounded": 11,
            "kept": 9,
            "dropped_zero_shares": 0,
            "dropped_min_notional": 2,
        },
        "execution_readiness_certification": {
            "planned_trade_count": 11,
            "expected_submissions": 9,
            "dropped_min_notional_count": 2,
            "expected_submission_orders": expected_submission_orders,
            "per_trade_diagnostics": {
                "skipped": [
                    {"ticker": "MO", "side": "SELL", "requested_quantity": 1, "skip_reason": "min_notional", "stage": "execution_filter"},
                    {"ticker": "NEE", "side": "SELL", "requested_quantity": 1, "skip_reason": "min_notional", "stage": "execution_filter"},
                ]
            },
        },
        "post_sell_rebudget": post_sell_rebudget,
        "alpaca_submissions": [
            {"ticker": "GE", "side": "SELL", "quantity": 1, "order_id": "run:GE:SELL"},
            {"ticker": "C", "side": "SELL", "quantity": 2, "order_id": "run:C:SELL"},
            {"ticker": "CVS", "side": "SELL", "quantity": 2, "order_id": "run:CVS:SELL"},
            {"ticker": "EOG", "side": "BUY", "quantity": 2.087315765, "order_id": "run:EOG:BUY"},
            {"ticker": "SPG", "side": "BUY", "quantity": 2.976602846, "order_id": "run:SPG:BUY"},
        ],
        "alpaca_submission_summary": {"submit_success": 5, "submit_failed": 0, "orders_filled_count": 5},
    }

    payload = dqr.build_execution_email_payload(
        trade_date="2026-06-29",
        daily_snapshot=daily_snapshot,
        paper_summary=paper_summary,
    )
    _, body = build_execution_email_text({**payload, "include_dynamic_sleeve_sections": False})

    eog = next(row for row in payload["trades"] if row["ticker"] == "EOG")
    assert eog["shares"] == 2.087315765
    assert "EOG | BUY | 2.087315765" in body
    assert "EOG | BUY | 2 |" not in body
    assert "Planned Payload Trades | 11" in body
    assert "Min-Notional Filtered | 2" in body
    assert "Intended Orders | 9" in body
    assert "Final Executable Trades | 5" in body
    assert "Orders Filled | 5" in body
    assert "ABBV BUY:buy_blocked_insufficient_buying_power" in body
    assert "MO SELL:min_notional" in body
