from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from execution.core import (
    ExecutionRequest,
    SynchronousTestAdapter,
    execute_lifecycle,
    paper_execution_config,
)

from Tests.parity.paper_execution_harness import (
    _budget_components,
    _diff_rebudgeted_buys,
    _frame_records,
    _intent_records,
    _skipped_intent_records,
    _stable,
)
from Tests.parity.scenarios import (
    PaperParityScenario,
    holdings_frame,
    make_config,
    prices_series,
    targets_frame,
)


def capture_execution_core(
    scenario: PaperParityScenario,
    *,
    ledger_output_root: str | Path | None = None,
    ledger_enabled: bool = True,
) -> dict[str, Any]:
    cfg = make_config(scenario)
    holdings = holdings_frame(scenario)
    targets = targets_frame(scenario)
    prices = prices_series(scenario)
    run_id = f"parity:{scenario.name}:paper_native"
    config = paper_execution_config(
        cfg,
        target_cash_weight=float(scenario.target_cash_weight),
        ledger_output_root=ledger_output_root,
        ledger_enabled=ledger_enabled,
    )
    adapter = SynchronousTestAdapter(
        holdings=holdings,
        starting_cash=float(scenario.starting_cash),
        post_sell_account=scenario.post_sell_account,
        planning_account=scenario.planning_account,
        preserve_post_sell_account_cash=bool(scenario.preserve_post_sell_account_cash),
        portfolio_id=str(scenario.cfg_overrides.get("portfolio_id", "parity")),
    )
    result = execute_lifecycle(
        request=ExecutionRequest(
            holdings=holdings,
            targets=targets,
            prices=prices,
            total_equity=float(scenario.total_equity),
            starting_cash=float(scenario.starting_cash),
            target_cash_weight=float(scenario.target_cash_weight),
            planning_account=scenario.planning_account,
            run_id=run_id,
            price_basis=scenario.price_basis,
            precomputed_trades=scenario.precomputed_trades,
            rebudget_total_equity=scenario.rebudget_total_equity,
            artifact_expectations=scenario.artifact_expectations,
        ),
        adapter=adapter,
        config=config,
    )

    final_buy_orders = result.final_buy_orders
    estimated_ending_cash = float(result.post_sell_budget_meta.get("post_sell_cash") or 0.0) - float(
        sum(float(order.get("notional") or 0.0) for order in final_buy_orders)
    )
    rebudget_diffs = _diff_rebudgeted_buys(
        original_buys=result.original_buy_trades,
        rebuilt_buys=result.rebuilt_buy_trades,
        skipped_buy_orders=result.rebudget_skipped,
    )
    raw_intents = _intent_records(
        result.raw_trades,
        prices=prices,
        price_basis=scenario.price_basis,
        stage="build_rebalance_trades",
    )
    executable_intents = _intent_records(
        result.executable_trades,
        prices=prices,
        price_basis=scenario.price_basis,
        stage="execution_filter",
    )
    final_intents = _intent_records(
        result.final_execution_trades,
        prices=prices,
        price_basis=scenario.price_basis,
        stage="post_sell_rebudget_final",
    )
    skipped_intents = _skipped_intent_records(
        result.rebudget_skipped,
        prices=prices,
        price_basis=scenario.price_basis,
        stage="post_sell_rebudget_suppressed",
    )

    ending_cash = None
    ending_cash_vs_risk_target = None
    if scenario.artifact_expectations.get("ending_cash") is not None:
        ending_cash = float(scenario.artifact_expectations.get("ending_cash") or 0.0)
        ending_cash_vs_risk_target = ending_cash - float(
            result.post_sell_budget_meta.get("risk_cash_target") or 0.0
        )

    artifact_comparison = None
    if scenario.artifact_expectations:
        artifact_comparison = {
            "expected": scenario.artifact_expectations,
            "actual": {
                "ending_cash": ending_cash,
                "estimated_ending_cash": estimated_ending_cash,
                "target_cash_weight": result.post_sell_budget_meta.get("target_cash_weight"),
                "post_sell_equity": result.post_sell_budget_meta.get("post_sell_equity"),
                "buys": [str(order.get("ticker") or "") for order in final_buy_orders],
                "skipped": [
                    str(row.get("ticker") or row.get("symbol") or "")
                    for row in result.rebudget_skipped
                ],
            },
        }

    return _stable(
        {
            "schema_version": "execution_core_parity.v1",
            "scenario": {
                "name": scenario.name,
                "source": scenario.source,
                "notes": scenario.notes,
                "price_basis": scenario.price_basis,
            },
            "inputs_summary": {
                "holdings_count": int(len(holdings)),
                "targets_count": int(len(targets)),
                "prices_count": int(len(prices)),
                "total_equity": float(scenario.total_equity),
                "starting_cash": float(scenario.starting_cash),
                "target_cash_weight": float(scenario.target_cash_weight),
                "planning_account": scenario.planning_account,
                "post_sell_account_fixture": scenario.post_sell_account,
                "config": cfg.__dict__,
                "precomputed_trade_plan_used": bool(scenario.precomputed_trades),
                "rebudget_total_equity": scenario.rebudget_total_equity,
            },
            "transition_computation": {
                "raw_intents": raw_intents,
                "raw_intent_order": [intent["symbol"] for intent in raw_intents],
                "trade_meta": result.trade_meta,
                "deadband_decisions": result.trade_meta.get("deadband_skipped", []),
                "whole_share_sweep": {
                    "cash_sweep_added_shares": result.trade_meta.get("cash_sweep_added_shares"),
                    "cash_sweep_iterations": result.trade_meta.get("cash_sweep_iterations"),
                    "cash_sweep_tickers": result.trade_meta.get("cash_sweep_tickers"),
                    "cash_sweep_remaining_dollars": result.trade_meta.get(
                        "cash_sweep_remaining_dollars"
                    ),
                },
                "slippage_bps": float(cfg.slippage_bps),
            },
            "capital_budget": _budget_components(result.capital_budget, result.post_sell_budget_meta),
            "execution_filter": {
                "stats": result.execution_filter_stats,
                "intents": executable_intents,
                "min_trade_decisions": {
                    "dropped_min_notional": result.execution_filter_stats.get("dropped_min_notional"),
                    "dropped_zero_shares": result.execution_filter_stats.get("dropped_zero_shares"),
                    "kept": result.execution_filter_stats.get("kept"),
                },
            },
            "sell_first_lifecycle": {
                "adapter": "SynchronousTestAdapter",
                "sell_fill": result.sell_fill_meta,
                "post_sell_account": dict(result.post_sell_snapshot.account),
                "post_sell_holdings": _frame_records(result.post_sell_snapshot.holdings),
            },
            "post_sell_rebudget": {
                "enabled": True,
                "status": result.rebudget_meta.get("status"),
                "reason_codes": result.rebudget_meta.get("reason_codes"),
                "sell_orders_submitted_count": int(len(result.sell_trades)),
                "sell_orders_submitted": _intent_records(
                    result.sell_trades,
                    prices=prices,
                    price_basis=scenario.price_basis,
                    stage="sell_first",
                ),
                "original_precomputed_buy_notional": float(
                    result.original_buy_trades["notional"].astype(float).sum()
                    if result.original_buy_trades is not None
                    and not result.original_buy_trades.empty
                    else 0.0
                ),
                "recomputed_requested_buy_notional": result.rebudget_meta.get(
                    "recomputed_requested_buy_notional"
                ),
                "recomputed_buy_notional": result.rebudget_meta.get("recomputed_buy_notional"),
                "final_submitted_buy_notional": float(
                    sum(float(order.get("notional") or 0.0) for order in final_buy_orders)
                ),
                "final_buy_orders_submitted": final_buy_orders,
                "rebuilt_buy_intents": _intent_records(
                    result.rebuilt_buy_trades,
                    prices=prices,
                    price_basis=scenario.price_basis,
                    stage="post_sell_rebudget",
                ),
                "skipped_buy_orders": skipped_intents,
                "resized_by_post_sell_rebudget": rebudget_diffs["resized"],
                "suppressed_by_post_sell_rebudget": rebudget_diffs["suppressed"],
                "estimated_ending_cash": estimated_ending_cash,
                "estimated_ending_cash_vs_risk_target": estimated_ending_cash
                - float(result.post_sell_budget_meta.get("risk_cash_target") or 0.0),
                "ending_cash": ending_cash,
                "ending_cash_vs_risk_target": ending_cash_vs_risk_target,
                "rebudget_total_equity": scenario.rebudget_total_equity,
            },
            "final_decision_output": {
                "intents": final_intents,
                "intent_order": [intent["symbol"] for intent in final_intents],
                "buy_count": int(
                    len(
                        [
                            intent
                            for intent in final_intents
                            if str(intent.get("side") or "").upper() == "BUY"
                        ]
                    )
                ),
                "sell_count": int(
                    len(
                        [
                            intent
                            for intent in final_intents
                            if str(intent.get("side") or "").upper() == "SELL"
                        ]
                    )
                ),
            },
            "artifact_comparison": artifact_comparison,
            "ledger_submissions": [
                {
                    "symbol": submission.intent.symbol,
                    "side": submission.intent.side,
                    "shares": submission.intent.shares,
                    "notional": submission.intent.notional,
                    "reason": submission.intent.reason,
                    "status": submission.status,
                }
                for submission in [*result.submitted_sells, *result.submitted_buys]
            ],
        }
    )


def behavioral_projection(payload: dict[str, Any]) -> dict[str, Any]:
    lifecycle = dict(payload["sell_first_lifecycle"])
    lifecycle.pop("broker_stub", None)
    lifecycle.pop("adapter", None)
    return {
        "transition_computation": payload["transition_computation"],
        "capital_budget": payload["capital_budget"],
        "execution_filter": payload["execution_filter"],
        "sell_first_lifecycle": lifecycle,
        "post_sell_rebudget": payload["post_sell_rebudget"],
        "final_decision_output": payload["final_decision_output"],
        "artifact_comparison": payload["artifact_comparison"],
    }
