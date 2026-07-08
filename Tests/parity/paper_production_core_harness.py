from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

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
from execution.core import (
    ExecutionRequest,
    apply_capital_budget_and_execution_filter,
    compute_transition_trades,
    paper_execution_config,
)
from paper.paper_broker import (
    _build_shadow_orders,
    _run_paper_core_execution_phase,
)


class InMemoryPaperBroker:
    """Fixture broker for the production paper adapter path."""

    paper = True
    base_url = "https://paper-api.alpaca.markets"

    def __init__(
        self,
        *,
        scenario: PaperParityScenario,
        prices: pd.Series,
        expected_sell_count: int,
    ) -> None:
        self.scenario = scenario
        self.prices = prices
        self.expected_sell_count = int(expected_sell_count)
        self.sell_count = 0
        self.buy_count = 0
        self.cash = float(scenario.starting_cash)
        self.orders_by_id: dict[str, dict[str, Any]] = {}
        self.orders_by_client_id: dict[str, dict[str, Any]] = {}
        self.shares: dict[str, float] = {}
        for row in scenario.holdings:
            ticker = str(row.get("ticker") or "").upper()
            self.shares[ticker] = self.shares.get(ticker, 0.0) + float(row.get("shares") or 0.0)

    def list_orders(self, status: str = "open", limit: int = 500) -> list[dict[str, Any]]:
        return []

    def find_order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        return self.orders_by_client_id.get(str(client_order_id))

    def get_order(self, order_id: str) -> dict[str, Any]:
        return dict(self.orders_by_id[str(order_id)])

    def submit_limit_order(self, **kwargs: Any) -> dict[str, Any]:
        return self.submit_market_order(**kwargs)

    def submit_market_order(
        self,
        *,
        symbol: str,
        qty: float,
        side: str,
        client_order_id: str,
        tif: str = "day",
    ) -> dict[str, Any]:
        symbol = str(symbol).upper()
        side = str(side).upper()
        qty = abs(float(qty or 0.0))
        price = float(self.prices.loc[symbol]) if symbol in self.prices.index else 0.0
        notional = qty * price
        if side in {"SELL", "CLOSE", "REDUCE"}:
            self.sell_count += 1
            self.shares[symbol] = max(0.0, float(self.shares.get(symbol, 0.0)) - qty)
            self.cash += notional
            if (
                self.scenario.preserve_post_sell_account_cash
                and self.sell_count >= self.expected_sell_count
                and self.scenario.post_sell_account
                and self.scenario.post_sell_account.get("cash") is not None
            ):
                self.cash = float(self.scenario.post_sell_account.get("cash") or 0.0)
        else:
            self.buy_count += 1
            self.shares[symbol] = float(self.shares.get(symbol, 0.0)) + qty
            self.cash -= notional
            if self.scenario.artifact_expectations.get("ending_cash") is not None:
                self.cash = float(self.scenario.artifact_expectations["ending_cash"])

        order_id = f"paper-fixture-{len(self.orders_by_id) + 1}"
        order = {
            "id": order_id,
            "client_order_id": str(client_order_id),
            "symbol": symbol,
            "side": side,
            "qty": str(qty),
            "filled_qty": str(qty),
            "status": "FILLED",
            "submitted_at": "2026-07-07T13:35:09Z",
            "filled_at": "2026-07-07T13:35:09Z",
        }
        self.orders_by_id[order_id] = dict(order)
        self.orders_by_client_id[str(client_order_id)] = dict(order)
        return dict(order)

    def get_account(self) -> dict[str, Any]:
        account = dict(self.scenario.post_sell_account or self.scenario.planning_account)
        if self.buy_count > 0 and self.scenario.artifact_expectations.get("ending_cash") is not None:
            account["cash"] = str(float(self.scenario.artifact_expectations["ending_cash"]))
        elif (
            self.scenario.preserve_post_sell_account_cash
            and self.sell_count >= self.expected_sell_count
            and self.scenario.post_sell_account
            and self.scenario.post_sell_account.get("cash") is not None
        ):
            account["cash"] = str(float(self.scenario.post_sell_account.get("cash") or 0.0))
        else:
            account["cash"] = str(float(self.cash))
        account.setdefault("buying_power", account["cash"])
        account.setdefault("status", "ACTIVE")
        return account

    def get_positions(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for symbol, shares in sorted(self.shares.items()):
            if shares <= 1e-12:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "qty": str(float(shares)),
                    "market_value": str(float(shares) * float(self.prices.get(symbol, 0.0))),
                }
            )
        return rows


def _with_run_output_root(root: Path):
    class _Env:
        def __enter__(self) -> None:
            self.old = os.environ.get("RUN_OUTPUT_ROOT")
            os.environ["RUN_OUTPUT_ROOT"] = str(root)

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            if self.old is None:
                os.environ.pop("RUN_OUTPUT_ROOT", None)
            else:
                os.environ["RUN_OUTPUT_ROOT"] = self.old

    return _Env()


def capture_paper_production_core(
    scenario: PaperParityScenario,
    *,
    run_output_root: str | Path,
) -> dict[str, Any]:
    cfg = make_config(scenario)
    output_root = Path(run_output_root)
    cfg.sent_ledger_path = str(output_root / "orders_sent.csv")
    holdings = holdings_frame(scenario)
    targets = targets_frame(scenario)
    prices = prices_series(scenario)
    run_id = f"parity:{scenario.name}:paper_native"
    config = paper_execution_config(
        cfg,
        target_cash_weight=float(scenario.target_cash_weight),
        ledger_output_root=output_root,
    )
    request = ExecutionRequest(
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
    )
    raw_trades, trade_meta = compute_transition_trades(request=request, config=config)
    capital_trades, capital_budget, executable_trades, execution_filter_stats = (
        apply_capital_budget_and_execution_filter(
            trades=raw_trades,
            planning_account=scenario.planning_account,
            config=config,
            precomputed_trade_plan_used=bool(scenario.precomputed_trades),
        )
    )
    initial_orders = _build_shadow_orders(
        executable_trades,
        run_id,
        allow_fractional=bool(cfg.allow_fractional),
    )
    expected_sell_count = len(
        [order for order in initial_orders if str(order.get("side") or "").upper() == "SELL"]
    )
    broker = InMemoryPaperBroker(
        scenario=scenario,
        prices=prices,
        expected_sell_count=expected_sell_count,
    )
    alpaca_submissions: list[dict[str, Any]] = []
    alpaca_submission_summary: dict[str, Any] = {}
    with _with_run_output_root(output_root):
        phase = _run_paper_core_execution_phase(
            alpaca=broker,  # type: ignore[arg-type]
            run_date="2026-07-07",
            run_id=run_id,
            cfg=cfg,
            holdings_prev=holdings,
            targets=targets,
            pricing_series=prices,
            execution_trades=executable_trades,
            orders=initial_orders,
            planning_account_snapshot=scenario.planning_account,
            cash_prev=float(scenario.starting_cash),
            equity_prev=float(scenario.total_equity),
            target_cash_weight=float(scenario.target_cash_weight),
            capital_budget_meta=dict(capital_budget),
            sent_ledger_path=cfg.sent_ledger_path,
            alpaca_submissions=alpaca_submissions,
            submission_metadata={},
            idempotent_skips=[],
            idempotent_drop_reasons=Counter(),
            alpaca_submission_summary=alpaca_submission_summary,
            rebudget_total_equity=scenario.rebudget_total_equity,
        )
    result = phase["core_result"]
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
        raw_trades,
        prices=prices,
        price_basis=scenario.price_basis,
        stage="build_rebalance_trades",
    )
    executable_intents = _intent_records(
        executable_trades,
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
        ending_cash = phase["post_sell_rebudget"].get("ending_cash")
        ending_cash_vs_risk_target = phase["post_sell_rebudget"].get("ending_cash_vs_risk_target")
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
            "schema_version": "paper_production_core_parity.v1",
            "scenario": {
                "name": scenario.name,
                "source": scenario.source,
                "notes": scenario.notes,
                "price_basis": scenario.price_basis,
            },
            "transition_computation": {
                "raw_intents": raw_intents,
                "raw_intent_order": [intent["symbol"] for intent in raw_intents],
                "trade_meta": trade_meta,
                "deadband_decisions": trade_meta.get("deadband_skipped", []),
                "whole_share_sweep": {
                    "cash_sweep_added_shares": trade_meta.get("cash_sweep_added_shares"),
                    "cash_sweep_iterations": trade_meta.get("cash_sweep_iterations"),
                    "cash_sweep_tickers": trade_meta.get("cash_sweep_tickers"),
                    "cash_sweep_remaining_dollars": trade_meta.get(
                        "cash_sweep_remaining_dollars"
                    ),
                },
                "slippage_bps": float(cfg.slippage_bps),
            },
            "capital_budget": _budget_components(capital_budget, result.post_sell_budget_meta),
            "execution_filter": {
                "stats": execution_filter_stats,
                "intents": executable_intents,
                "min_trade_decisions": {
                    "dropped_min_notional": execution_filter_stats.get("dropped_min_notional"),
                    "dropped_zero_shares": execution_filter_stats.get("dropped_zero_shares"),
                    "kept": execution_filter_stats.get("kept"),
                },
            },
            "sell_first_lifecycle": {
                "adapter": "PaperBrokerCoreAdapter",
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
            "ledger_path": str(output_root / "live_trade_ledger.jsonl"),
        }
    )


def production_behavioral_projection(payload: dict[str, Any]) -> dict[str, Any]:
    lifecycle = dict(payload["sell_first_lifecycle"])
    lifecycle.pop("broker_stub", None)
    lifecycle.pop("adapter", None)
    lifecycle.pop("sell_fill", None)
    return {
        "transition_computation": payload["transition_computation"],
        "capital_budget": payload["capital_budget"],
        "execution_filter": payload["execution_filter"],
        "sell_first_lifecycle": lifecycle,
        "post_sell_rebudget": payload["post_sell_rebudget"],
        "final_decision_output": payload["final_decision_output"],
        "artifact_comparison": payload["artifact_comparison"],
    }
