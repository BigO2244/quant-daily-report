from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from paper.paper_broker import (
    BUY_BLOCKED_RISK_CASH_TARGET,
    _build_capital_budget,
    _build_shadow_orders,
    _compute_buy_budget,
    _normalize_and_filter_executable_trades,
    _post_sell_buy_budget,
    _rebuild_post_sell_buy_trades,
    _reserve_cash_for_equity,
    build_rebalance_trades,
)
from brokers.alpaca_broker import json_safe_primitive

from Tests.parity.scenarios import (
    PaperParityScenario,
    holdings_frame,
    make_config,
    prices_series,
    scenario_by_name,
    scenarios,
    targets_frame,
)


GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def _stable_number(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(float(value), 10)
    try:
        if hasattr(value, "item"):
            return _stable_number(value.item())
    except Exception:
        return value
    return value


def _stable(value: Any) -> Any:
    value = json_safe_primitive(value)
    if isinstance(value, dict):
        return {str(key): _stable(val) for key, val in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [_stable(item) for item in value]
    return _stable_number(value)


def stable_json(payload: dict[str, Any]) -> str:
    return json.dumps(_stable(payload), indent=2, sort_keys=True) + "\n"


def _frame_records(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    records: list[dict[str, Any]] = []
    for order, (_, row) in enumerate(frame.iterrows(), start=1):
        item = row.to_dict()
        item["order"] = order
        records.append(item)
    return _stable(records)


def _intent_records(
    frame: pd.DataFrame | None,
    *,
    prices: pd.Series,
    price_basis: str,
    stage: str,
    block_reason: str | None = None,
) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    out: list[dict[str, Any]] = []
    for order, (_, row) in enumerate(frame.iterrows(), start=1):
        symbol = str(row.get("ticker") or "").upper()
        side = str(row.get("side") or "").upper()
        price = float(row.get("price") or 0.0)
        reference_price = float(prices.loc[symbol]) if symbol in prices.index else None
        if reference_price is None:
            slippage_per_share = None
        elif side == "BUY":
            slippage_per_share = price - reference_price
        elif side == "SELL":
            slippage_per_share = reference_price - price
        else:
            slippage_per_share = 0.0
        notional = abs(float(row.get("notional") or 0.0))
        min_trade_dollars = row.get("min_trade_dollars")
        out.append(
            {
                "order": order,
                "stage": stage,
                "symbol": symbol,
                "ticker": symbol,
                "side": side,
                "shares": abs(float(row.get("shares") or 0.0)),
                "price": price,
                "notional": notional,
                "price_basis": price_basis,
                "reference_price": reference_price,
                "slippage_cost": float(row.get("slippage_cost") or 0.0),
                "slippage_per_share": slippage_per_share,
                "reason_code": str(row.get("reason") or ""),
                "block_reason": str(row.get("block_reason") or block_reason or "") or None,
                "min_trade_decision": (
                    "suppressed_min_trade"
                    if str(row.get("block_reason") or "") in {
                        "min_trade_dollars",
                        "min_trade_dollars_after_budget_clip",
                    }
                    else "kept"
                    if notional + 1e-9 >= float(min_trade_dollars or 0.0)
                    else None
                ),
            }
        )
    return _stable(out)


def _skipped_intent_records(
    rows: list[dict[str, Any]],
    *,
    prices: pd.Series,
    price_basis: str,
    stage: str,
) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return []
    if "side" not in frame.columns:
        frame["side"] = "BUY"
    if "shares" not in frame.columns:
        frame["shares"] = 0.0
    if "price" not in frame.columns:
        frame["price"] = frame["ticker"].map(prices.to_dict())
    if "notional" not in frame.columns:
        frame["notional"] = frame["shares"].astype(float) * frame["price"].astype(float)
    if "reason" not in frame.columns:
        frame["reason"] = "post_sell_rebudget_skipped"
    if "slippage_cost" not in frame.columns:
        frame["slippage_cost"] = 0.0
    return _intent_records(frame, prices=prices, price_basis=price_basis, stage=stage)


def _orders_by_symbol(frame: pd.DataFrame | None) -> dict[str, dict[str, float]]:
    if frame is None or frame.empty:
        return {}
    out: dict[str, dict[str, float]] = {}
    buy_rows = frame[frame["side"].astype(str).str.upper() == "BUY"]
    for _, row in buy_rows.iterrows():
        symbol = str(row.get("ticker") or "").upper()
        out[symbol] = {
            "shares": abs(float(row.get("shares") or 0.0)),
            "notional": abs(float(row.get("notional") or 0.0)),
        }
    return out


def _precomputed_trades_frame(rows: tuple[dict[str, Any], ...]) -> pd.DataFrame:
    cols = ["ticker", "side", "shares", "price", "slippage_cost", "notional", "reason"]
    normalized: list[dict[str, Any]] = []
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        side = str(row.get("side") or "").upper()
        shares = abs(float(row.get("shares") or row.get("quantity") or 0.0))
        price = float(row.get("price") or row.get("entry_price") or 0.0)
        notional = abs(float(row.get("notional") or (shares * price)))
        if not ticker or side not in {"BUY", "SELL", "CLOSE", "REDUCE"}:
            continue
        normalized.append(
            {
                "ticker": ticker,
                "side": "SELL" if side in {"SELL", "CLOSE", "REDUCE"} else "BUY",
                "shares": shares,
                "price": price,
                "slippage_cost": float(row.get("slippage_cost") or 0.0),
                "notional": notional,
                "reason": str(row.get("reason") or row.get("notes") or "precomputed_execution_payload"),
            }
        )
    return pd.DataFrame(normalized, columns=cols)


def _diff_rebudgeted_buys(
    *,
    original_buys: pd.DataFrame,
    rebuilt_buys: pd.DataFrame,
    skipped_buy_orders: list[dict[str, Any]],
) -> dict[str, Any]:
    original = _orders_by_symbol(original_buys)
    rebuilt = _orders_by_symbol(rebuilt_buys)
    resized: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for symbol in sorted(set(original) & set(rebuilt)):
        old = original[symbol]
        new = rebuilt[symbol]
        if (
            abs(float(old["shares"]) - float(new["shares"])) > 1e-9
            or abs(float(old["notional"]) - float(new["notional"])) > 1e-9
        ):
            resized.append(
                {
                    "symbol": symbol,
                    "original_shares": old["shares"],
                    "original_notional": old["notional"],
                    "rebudgeted_shares": new["shares"],
                    "rebudgeted_notional": new["notional"],
                }
            )
    skipped_symbols = {str(row.get("ticker") or "").upper() for row in skipped_buy_orders}
    for row in skipped_buy_orders:
        suppressed.append(
            {
                "symbol": str(row.get("ticker") or "").upper(),
                "block_reason": str(row.get("block_reason") or ""),
                "requested_shares": abs(float(row.get("shares") or 0.0)),
                "requested_notional": abs(float(row.get("notional") or 0.0)),
            }
        )
    for symbol in sorted(set(original) - set(rebuilt) - skipped_symbols):
        suppressed.append(
            {
                "symbol": symbol,
                "block_reason": "not_rebuilt_after_post_sell_rebudget",
                "requested_shares": original[symbol]["shares"],
                "requested_notional": original[symbol]["notional"],
            }
        )
    return _stable({"resized": resized, "suppressed": suppressed})


class SynchronousSellFillBroker:
    """In-process broker stub matching paper's immediate sell-fill assumption."""

    def __init__(self, scenario: PaperParityScenario, prices: pd.Series) -> None:
        self.scenario = scenario
        self.prices = prices

    def fill_sells(
        self,
        *,
        holdings: pd.DataFrame,
        sell_trades: pd.DataFrame,
    ) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
        post_account = dict(self.scenario.post_sell_account or self.scenario.planning_account)
        shares_by_symbol: dict[str, float] = {}
        for _, row in holdings.iterrows():
            symbol = str(row.get("ticker") or "").upper()
            if symbol:
                shares_by_symbol[symbol] = shares_by_symbol.get(symbol, 0.0) + float(row.get("shares") or 0.0)

        cash = float(self.scenario.starting_cash)
        proceeds = 0.0
        sell_records: list[dict[str, Any]] = []
        if sell_trades is not None and not sell_trades.empty:
            for _, row in sell_trades.iterrows():
                symbol = str(row.get("ticker") or "").upper()
                shares = abs(float(row.get("shares") or 0.0))
                notional = abs(float(row.get("notional") or 0.0))
                proceeds += notional
                cash += notional
                shares_by_symbol[symbol] = max(0.0, shares_by_symbol.get(symbol, 0.0) - shares)
                sell_records.append(
                    {
                        "symbol": symbol,
                        "shares": shares,
                        "notional": notional,
                        "fill_status": "FILLED",
                    }
                )

        computed_post_sell_cash = float(cash)
        if self.scenario.preserve_post_sell_account_cash and post_account.get("cash") is not None:
            cash = float(post_account.get("cash") or 0.0)
            proceeds = max(0.0, float(cash) - float(self.scenario.starting_cash))
        post_account["cash"] = str(cash)
        if "buying_power" not in post_account or post_account.get("buying_power") is None:
            post_account["buying_power"] = str(cash)

        post_holdings = [
            {"ticker": symbol, "sleeve": self.scenario.cfg_overrides.get("portfolio_id", "parity"), "shares": shares}
            for symbol, shares in sorted(shares_by_symbol.items())
            if shares > 1e-12
        ]
        return (
            pd.DataFrame(post_holdings, columns=["ticker", "sleeve", "shares"]),
            post_account,
            {
                "sell_orders": sell_records,
                "confirmed_sell_proceeds": proceeds,
                "post_sell_cash_from_sync_fills": computed_post_sell_cash,
                "post_sell_cash_used_for_rebudget": cash,
                "fill_model": "synchronous_all_sells_filled",
            },
        )


def _budget_components(capital_budget: dict[str, Any], post_sell_budget: dict[str, Any]) -> dict[str, Any]:
    reserve_policy = capital_budget.get("reserve_cash_policy") or {}
    post_sell_equity = post_sell_budget.get("post_sell_equity")
    return _stable(
        {
            "pretrade_capital_budget": capital_budget,
            "post_sell_buy_budget": post_sell_budget,
            "explicit_components": {
                "post_sell_cash": post_sell_budget.get("post_sell_cash"),
                "broker_budget": post_sell_budget.get("broker_safeguard_buy_budget"),
                "sell_proceeds_haircut": reserve_policy.get("sell_proceeds_haircut"),
                "expected_sell_proceeds_conservative": capital_budget.get(
                    "expected_sell_proceeds_conservative"
                ),
                "reserve_cash_pretrade": reserve_policy.get("reserve_cash"),
                "reserve_cash_post_sell": _reserve_cash_for_equity(
                    float(post_sell_equity or 0.0),
                    min_cash=100.0,
                ),
                "risk_cash_target_term": post_sell_budget.get("risk_cash_target"),
                "risk_cash_target_buy_budget": post_sell_budget.get(
                    "risk_cash_target_buy_budget"
                ),
                "buy_budget_after_safeguards": post_sell_budget.get(
                    "buy_budget_after_safeguards"
                ),
            },
        }
    )


def capture_paper_native_execution(scenario: PaperParityScenario) -> dict[str, Any]:
    cfg = make_config(scenario)
    holdings = holdings_frame(scenario)
    targets = targets_frame(scenario)
    prices = prices_series(scenario)

    precomputed_trade_plan_used = bool(scenario.precomputed_trades)
    if precomputed_trade_plan_used:
        raw_trades = _precomputed_trades_frame(scenario.precomputed_trades)
        trade_meta = {
            "source": "precomputed_execution_payload",
            "precomputed_trade_plan_used": True,
            "deadband_skipped": [],
            "deadband_skipped_count": 0,
            "cash_sweep_added_shares": 0,
            "cash_sweep_iterations": 0,
            "cash_sweep_tickers": [],
            "cash_sweep_remaining_dollars": None,
            "target_cash_weight": float(scenario.target_cash_weight),
        }
    else:
        raw_trades, trade_meta = build_rebalance_trades(
            holdings=holdings,
            targets=targets,
            prices=prices,
            total_equity=float(scenario.total_equity),
            starting_cash=float(scenario.starting_cash),
            target_cash_weight=float(scenario.target_cash_weight),
            cfg=cfg,
        )

    requested_buy_notional = (
        float(
            raw_trades.loc[
                raw_trades["side"].astype(str).str.upper() == "BUY",
                "notional",
            ].astype(float).sum()
        )
        if raw_trades is not None and not raw_trades.empty
        else 0.0
    )
    expected_sell_proceeds = (
        float(
            raw_trades.loc[
                raw_trades["side"].astype(str).str.upper().isin({"SELL", "CLOSE", "REDUCE"}),
                "notional",
            ].astype(float).sum()
        )
        if raw_trades is not None and not raw_trades.empty
        else 0.0
    )
    capital_budget = _build_capital_budget(
        broker_cash=scenario.planning_account.get("cash"),
        broker_equity=scenario.planning_account.get("equity")
        or scenario.planning_account.get("portfolio_value"),
        broker_buying_power=scenario.planning_account.get("buying_power"),
        expected_sell_proceeds=expected_sell_proceeds,
        requested_buy_notional=requested_buy_notional,
    )
    if precomputed_trade_plan_used:
        capital_trades = raw_trades.copy()
        capital_budget = dict(capital_budget)
        capital_budget["capital_budget_application"] = "skipped_precomputed_trade_plan"
        capital_budget["allowed_buy_notional"] = requested_buy_notional
        capital_budget["capital_constraint_triggered"] = False
        capital_budget["clipped_or_deferred_buys_count"] = 0
    else:
        capital_trades, capital_budget = _apply_capital_budget(raw_trades, cfg, capital_budget)
    executable_trades, execution_filter_stats = _normalize_and_filter_executable_trades(
        capital_trades,
        cfg,
    )

    sell_trades = (
        executable_trades[
            executable_trades["side"].astype(str).str.upper().isin({"SELL", "CLOSE", "REDUCE"})
        ].copy()
        if executable_trades is not None
        and not executable_trades.empty
        and "side" in executable_trades.columns
        else pd.DataFrame(columns=capital_trades.columns)
    )
    original_buy_trades = (
        executable_trades[
            executable_trades["side"].astype(str).str.upper().isin({"BUY", "ADD"})
        ].copy()
        if executable_trades is not None
        and not executable_trades.empty
        and "side" in executable_trades.columns
        else pd.DataFrame(columns=capital_trades.columns)
    )

    broker = SynchronousSellFillBroker(scenario, prices)
    post_sell_holdings, post_sell_account, sell_fill_meta = broker.fill_sells(
        holdings=holdings,
        sell_trades=sell_trades,
    )
    capital_constraint_clear = not bool(capital_budget.get("capital_constraint_triggered"))
    sell_orders_present = not sell_trades.empty

    if sell_orders_present:
        buy_budget, post_sell_budget_meta = _post_sell_buy_budget(
            account=post_sell_account,
            cfg=cfg,
            target_cash_weight=float(scenario.target_cash_weight),
            fallback_equity=float(scenario.total_equity),
            capital_constraint_clear=capital_constraint_clear,
        )
        max_rebudget_buy_orders = max(0, int(cfg.max_trades_per_day or 0) - int(len(sell_trades)))
        zero_budget_block_reason = None
        if float(buy_budget or 0.0) <= 1e-9:
            if float(post_sell_budget_meta.get("risk_cash_target_buy_budget") or 0.0) <= 1e-9:
                zero_budget_block_reason = BUY_BLOCKED_RISK_CASH_TARGET
        rebuilt_buy_trades, rebudget_meta, rebudget_skipped = _rebuild_post_sell_buy_trades(
            holdings=post_sell_holdings,
            targets=targets,
            prices=prices,
            total_equity=float(
                scenario.rebudget_total_equity
                if scenario.rebudget_total_equity is not None
                else post_sell_budget_meta.get("post_sell_equity")
                or scenario.total_equity
            ),
            buy_budget=float(buy_budget or 0.0),
            cfg=cfg,
            max_buy_orders=max_rebudget_buy_orders,
            zero_budget_block_reason=zero_budget_block_reason,
        )
    else:
        buy_budget, buy_budget_basis = _compute_buy_budget(
            post_sell_account,
            cfg,
            capital_constraint_clear=capital_constraint_clear,
        )
        post_sell_equity = float(
            post_sell_account.get("equity")
            or post_sell_account.get("portfolio_value")
            or scenario.total_equity
            or 0.0
        )
        post_sell_cash = float(post_sell_account.get("cash") or 0.0)
        risk_cash_target = max(0.0, post_sell_equity * float(scenario.target_cash_weight or 0.0))
        post_sell_budget_meta = {
            "post_sell_cash": post_sell_cash,
            "post_sell_equity": post_sell_equity,
            "post_sell_buying_power": post_sell_account.get("buying_power"),
            "risk_cash_target": risk_cash_target,
            "target_cash_weight": float(scenario.target_cash_weight or 0.0),
            "buy_budget_before_safeguards": post_sell_cash,
            "broker_safeguard_buy_budget": float(buy_budget or 0.0),
            "risk_cash_target_buy_budget": max(0.0, post_sell_cash - risk_cash_target),
            "buy_budget_after_safeguards": float(buy_budget or 0.0),
            "buy_budget_basis": buy_budget_basis,
        }
        rebuilt_buy_trades = original_buy_trades.copy()
        rebudget_meta = {
            "status": "SKIPPED",
            "reason_codes": ["no_sell_orders"],
            "candidate_count": int(len(rebuilt_buy_trades)),
            "recomputed_requested_buy_notional": requested_buy_notional,
            "recomputed_buy_notional": requested_buy_notional,
        }
        rebudget_skipped = []

    sell_rows = sell_trades.copy()
    final_execution_trades = pd.concat(
        [sell_rows, rebuilt_buy_trades],
        ignore_index=True,
        sort=False,
    ).reindex(columns=executable_trades.columns if executable_trades is not None else None)
    run_id = f"parity:{scenario.name}:paper_native"
    final_buy_orders = _build_shadow_orders(
        rebuilt_buy_trades,
        run_id,
        allow_fractional=bool(cfg.allow_fractional),
    )
    estimated_ending_cash = float(post_sell_budget_meta.get("post_sell_cash") or 0.0) - float(
        sum(float(order.get("notional") or 0.0) for order in final_buy_orders)
    )
    rebudget_diffs = _diff_rebudgeted_buys(
        original_buys=original_buy_trades,
        rebuilt_buys=rebuilt_buy_trades,
        skipped_buy_orders=rebudget_skipped,
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
        final_execution_trades,
        prices=prices,
        price_basis=scenario.price_basis,
        stage="post_sell_rebudget_final",
    )
    skipped_intents = _skipped_intent_records(
        rebudget_skipped,
        prices=prices,
        price_basis=scenario.price_basis,
        stage="post_sell_rebudget_suppressed",
    )

    ending_cash = None
    ending_cash_vs_risk_target = None
    if scenario.artifact_expectations.get("ending_cash") is not None:
        ending_cash = float(scenario.artifact_expectations.get("ending_cash") or 0.0)
        ending_cash_vs_risk_target = ending_cash - float(
            post_sell_budget_meta.get("risk_cash_target") or 0.0
        )

    artifact_comparison = None
    if scenario.artifact_expectations:
        artifact_comparison = {
            "expected": scenario.artifact_expectations,
            "actual": {
                "ending_cash": ending_cash,
                "estimated_ending_cash": estimated_ending_cash,
                "target_cash_weight": post_sell_budget_meta.get("target_cash_weight"),
                "post_sell_equity": post_sell_budget_meta.get("post_sell_equity"),
                "buys": [str(order.get("ticker") or "") for order in final_buy_orders],
                "skipped": [
                    str(row.get("ticker") or row.get("symbol") or "")
                    for row in rebudget_skipped
                ],
            },
        }

    return _stable(
        {
            "schema_version": "paper_execution_parity.v1",
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
                "precomputed_trade_plan_used": precomputed_trade_plan_used,
                "rebudget_total_equity": scenario.rebudget_total_equity,
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
            "capital_budget": _budget_components(capital_budget, post_sell_budget_meta),
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
                "broker_stub": "SynchronousSellFillBroker",
                "sell_fill": sell_fill_meta,
                "post_sell_account": post_sell_account,
                "post_sell_holdings": _frame_records(post_sell_holdings),
            },
            "post_sell_rebudget": {
                "enabled": True,
                "status": rebudget_meta.get("status"),
                "reason_codes": rebudget_meta.get("reason_codes"),
                "sell_orders_submitted_count": int(len(sell_trades)),
                "sell_orders_submitted": _intent_records(
                    sell_trades,
                    prices=prices,
                    price_basis=scenario.price_basis,
                    stage="sell_first",
                ),
                "original_precomputed_buy_notional": float(
                    original_buy_trades["notional"].astype(float).sum()
                    if original_buy_trades is not None and not original_buy_trades.empty
                    else 0.0
                ),
                "recomputed_requested_buy_notional": rebudget_meta.get(
                    "recomputed_requested_buy_notional"
                ),
                "recomputed_buy_notional": rebudget_meta.get("recomputed_buy_notional"),
                "final_submitted_buy_notional": float(
                    sum(float(order.get("notional") or 0.0) for order in final_buy_orders)
                ),
                "final_buy_orders_submitted": final_buy_orders,
                "rebuilt_buy_intents": _intent_records(
                    rebuilt_buy_trades,
                    prices=prices,
                    price_basis=scenario.price_basis,
                    stage="post_sell_rebudget",
                ),
                "skipped_buy_orders": skipped_intents,
                "resized_by_post_sell_rebudget": rebudget_diffs["resized"],
                "suppressed_by_post_sell_rebudget": rebudget_diffs["suppressed"],
                "estimated_ending_cash": estimated_ending_cash,
                "estimated_ending_cash_vs_risk_target": estimated_ending_cash
                - float(post_sell_budget_meta.get("risk_cash_target") or 0.0),
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
        }
    )


def _apply_capital_budget(raw_trades: pd.DataFrame, cfg: Any, capital_budget: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    from paper.paper_broker import _apply_capital_budget_to_trades

    return _apply_capital_budget_to_trades(raw_trades, cfg, capital_budget)


def capture_all_scenarios() -> dict[str, dict[str, Any]]:
    return {scenario.name: capture_paper_native_execution(scenario) for scenario in scenarios()}


def golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.json"


def write_golden_files(names: list[str] | None = None) -> list[Path]:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    selected = scenarios() if not names else tuple(scenario_by_name(name) for name in names)
    written: list[Path] = []
    for scenario in selected:
        payload = capture_paper_native_execution(scenario)
        path = golden_path(scenario.name)
        path.write_text(stable_json(payload), encoding="utf-8")
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture paper-native execution parity goldens.")
    parser.add_argument("--write-golden", action="store_true", help="Write Tests/parity/golden JSON files.")
    parser.add_argument("--scenario", action="append", help="Scenario name to capture. May be repeated.")
    parser.add_argument("--print", dest="print_payload", action="store_true", help="Print captured JSON to stdout.")
    args = parser.parse_args()

    names = args.scenario
    if args.write_golden:
        paths = write_golden_files(names)
        for path in paths:
            print(path)
        return 0

    if args.print_payload:
        selected = scenarios() if not names else tuple(scenario_by_name(name) for name in names)
        payload = {
            scenario.name: capture_paper_native_execution(scenario)
            for scenario in selected
        }
        print(stable_json(payload))
        return 0

    parser.error("choose --write-golden or --print")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
