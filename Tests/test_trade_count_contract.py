from __future__ import annotations

import json
from pathlib import Path

from core.operator_summary import write_operator_summary
from core.trade_count_contract import compute_trade_count_contract
from daily_quant_report import build_execution_email_payload


def test_execution_enabled_counts_keep_model_planner_and_eligible_distinct(tmp_path: Path) -> None:
    payload = build_execution_email_payload(
        trade_date="2026-03-12",
        daily_snapshot={
            "risk_levels": [{"ticker": "AAPL", "entry_price": 200.0}],
            "holdings": [],
            "proposed_trades": [
                {"ticker": "AAPL"},
                {"ticker": "MSFT"},
                {"ticker": "NVDA"},
            ],
        },
        paper_summary={
            "trading_mode": "ALPACA",
            "market_status": "OPEN",
            "execution_trades": [
                {"ticker": "AAPL", "side": "BUY", "shares": 2, "notional": 400.0},
            ],
            "trade_plan": [
                {"ticker": "AAPL", "side": "BUY", "shares": 2, "notional": 400.0},
                {"ticker": "MSFT", "side": "BUY", "shares": 0, "notional": 20.0},
            ],
            "execution_filter": {
                "raw": 2,
                "rounded": 2,
                "kept": 1,
                "dropped_zero_shares": 1,
                "dropped_min_notional": 0,
            },
            "min_trade_dollars": 100.0,
            "risk_meta": {},
        },
    )

    run_root = tmp_path / "outputs" / "runs" / "run-counts"
    run_root.mkdir(parents=True, exist_ok=True)
    write_operator_summary(
        run_root,
        run_id="run-counts",
        trade_date="2026-03-12",
        mode="ALPACA",
        pretrade_status="READY",
        proposed_trades_count=int(payload["planner_intended_trades_count"]),
        executable_trades_count=int(payload["execution_eligible_trades_count"]),
        model_proposed_trades_count=int(payload["model_proposed_trades_count"]),
        planner_intended_trades_count=int(payload["planner_intended_trades_count"]),
        execution_eligible_trades_count=int(payload["execution_eligible_trades_count"]),
        orders_submitted_count=int(payload["orders_submitted_count"]),
        orders_filled_count=int(payload["orders_filled_count"]),
        planner_completed=True,
        execution_payload_written=True,
    )
    operator_summary = json.loads((run_root / "operator_summary.json").read_text(encoding="utf-8"))

    assert payload["model_proposed_trades_count"] == 3
    assert payload["planner_intended_trades_count"] == 2
    assert payload["execution_eligible_trades_count"] == 1
    assert payload["proposed_trades_intent_count"] == 2
    assert payload["executable_trades_count"] == 1

    assert operator_summary["model_proposed_trades_count"] == 3
    assert operator_summary["planner_intended_trades_count"] == 2
    assert operator_summary["execution_eligible_trades_count"] == 1
    assert operator_summary["proposed_trades_count"] == 2
    assert operator_summary["executable_trades_count"] == 1


def test_trade_count_contract_counts_submitted_and_filled_from_execution_results() -> None:
    counts = compute_trade_count_contract(
        daily_snapshot={"proposed_trades": [{"ticker": "AAPL"}]},
        paper_summary={"trade_plan": [{"ticker": "AAPL", "side": "BUY"}]},
        execution_payload={"trades": [{"ticker": "AAPL", "side": "BUY"}], "executable_trades_count": 1},
        execution_results={
            "submitted_count": 2,
            "broker_responses": [
                {"status": "FILLED"},
                {"status": "ACCEPTED"},
                {"status": "FILLED_ESTIMATE"},
            ],
        },
    )

    assert counts["model_proposed_trades_count"] == 1
    assert counts["planner_intended_trades_count"] == 1
    assert counts["execution_eligible_trades_count"] == 1
    assert counts["orders_submitted_count"] == 2
    assert counts["orders_filled_count"] == 2
    assert counts["sources"]["planner_intended_trades_count"] == "paper_summary.trade_plan"
    assert counts["sources"]["orders_filled_count"] == "execution_results.broker_responses[].status"
