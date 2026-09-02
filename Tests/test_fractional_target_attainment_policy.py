from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pandas as pd
import pytest

from authority.contracts import (
    build_decision_package,
    build_evidence_package,
    build_risk_package,
)
from authority.pipeline import execution_package_from_risk
from core.paper_full_account_invariant import full_account_plan_invariant_error
from core.target_attainment_policy import validate_target_attainment_policy
from execution.core import (
    ExecutionRequest,
    apply_capital_budget_and_execution_filter,
    compute_transition_trades,
    live_pilot_execution_config,
)
from scripts.live_pilot_execute import _core_rows_from_frame
from scripts.authorize_exact_execution_plan import _expected_state


POLICY = {
    "schema_version": "caerus.target_attainment_policy.v1",
    "account_scope": "PAPER",
    "share_mode": "FRACTIONAL_SHARES",
    "target_cash_weight": 0.05,
    "minimum_cash_weight": 0.025,
    "fixed_drift_tolerance": 0.02,
    "nearest_feasible_required": False,
    "comparison_epoch_policy": "FIRST_CLEAN_POST_FIX_PAPER_RUN",
    "strict_green_propagation": True,
    "owner_approved_at": "2026-08-31",
}


def _approved_package(rows: list[dict] | None = None) -> dict:
    rows = rows or [
        {"symbol": "AAA", "target_weight": 0.475},
        {"symbol": "BBB", "target_weight": 0.475},
    ]
    evidence = build_evidence_package(
        package_id="evidence:fractional-target",
        trade_date="2026-08-31",
        source_refs=["orion.json"],
        observations=rows,
    )
    decision = build_decision_package(
        package_id="decision:fractional-target",
        trade_date="2026-08-31",
        evidence=evidence,
        target_rows=rows,
        target_cash_weight=0.05,
        source_refs=["orion.json"],
    )
    risk = build_risk_package(
        package_id="risk:fractional-target",
        decision=decision,
        approved_target_rows=rows,
        approved_cash_weight=0.05,
        constraints={"target_attainment_policy": POLICY},
        source_refs=["decision:fractional-target"],
    )
    return execution_package_from_risk(risk).to_dict()


def _request() -> ExecutionRequest:
    return ExecutionRequest(
        holdings=pd.DataFrame(
            [
                {"ticker": "AAA", "shares": 4.0},
                {"ticker": "BBB", "shares": 3.0},
            ]
        ),
        targets=pd.DataFrame(
            [
                {"ticker": "AAA", "target_weight": 0.475},
                {"ticker": "BBB", "target_weight": 0.475},
            ]
        ),
        prices=pd.Series({"AAA": 100.0, "BBB": 100.0}),
        total_equity=1000.0,
        starting_cash=300.0,
        target_cash_weight=0.05,
        planning_account={"cash": 300.0, "equity": 1000.0},
        run_id="fractional-policy-test",
        approved_execution_package=_approved_package(),
        price_basis="timestamped_alpaca_latest_trade_at_authorization",
    )


def _config(*, allow_fractional: bool):
    config = live_pilot_execution_config(
        approved_cap_usd=1000.0,
        allow_fractional=allow_fractional,
        max_orders=10,
        min_trade_usd=1.0,
        buy_buffer_pct=1.0,
        ledger_enabled=False,
    )
    return dataclasses.replace(config, mode="paper")


def test_fractional_policy_builds_fractional_orders_with_package_lineage() -> None:
    trades, meta = compute_transition_trades(
        request=_request(),
        config=_config(allow_fractional=True),
    )

    by_symbol = {
        str(row["ticker"]): (str(row["side"]), float(row["shares"]))
        for _, row in trades.iterrows()
    }
    assert by_symbol == {"AAA": ("BUY", 0.75), "BBB": ("BUY", 1.75)}
    assert meta["fractional_target_attainment"]["status"] == "PASS"
    assert "whole_share_feasibility" not in meta


def test_fractional_policy_rejects_a_whole_share_runtime() -> None:
    with pytest.raises(ValueError, match="requires fractional execution"):
        compute_transition_trades(
            request=_request(),
            config=_config(allow_fractional=False),
        )


def test_fractional_order_quantity_is_sealed_to_six_decimals() -> None:
    rows = _core_rows_from_frame(
        pd.DataFrame(
            [
                {
                    "ticker": "AAA",
                    "side": "BUY",
                    "shares": 0.123456789,
                    "price": 100.0,
                    "notional": 12.3456789,
                }
            ]
        ),
        plan={
            "execution_lane": "paper",
            "allow_fractional": True,
            "target_portfolio": [{"symbol": "AAA"}],
        },
    )

    assert rows[0]["shares"] == 0.123457
    assert rows[0]["qty"] == 0.123457
    assert rows[0]["notional"] == pytest.approx(12.3457)


def test_fractional_expected_state_uses_broker_quantity_precision() -> None:
    positions, cash = _expected_state(
        positions=[{"symbol": "LRCX", "quantity": 6.657142}],
        cash=516.82,
        orders=[
            {
                "symbol": "LRCX",
                "side": "BUY",
                "quantity": 0.158,
                "expected_price": 288.415,
                "notional": 46.02382,
            }
        ],
    )

    assert positions == [{"symbol": "LRCX", "quantity": 6.815142}]
    assert cash == pytest.approx(470.79618)


def test_august_31_orion_snapshot_produces_four_fractional_rebalance_orders() -> None:
    prices = {
        "INTC": 89.91,
        "LRCX": 299.48,
        "MU": 938.83,
        "STX": 821.625,
        "WDC": 451.01,
    }
    target_rows = [
        {"symbol": symbol, "target_weight": 0.19}
        for symbol in prices
    ]
    planning_account = {
        "cash": 581.54,
        "equity": 10431.88,
        "buying_power": 581.54,
        "settled_cash": 581.54,
    }
    request = ExecutionRequest(
        holdings=pd.DataFrame(
            [
                {"ticker": "INTC", "shares": 22.0},
                {"ticker": "LRCX", "shares": 7.0},
                {"ticker": "MU", "shares": 2.0},
                {"ticker": "STX", "shares": 2.0},
                {"ticker": "WDC", "shares": 5.0},
            ]
        ),
        targets=pd.DataFrame(
            [
                {"ticker": row["symbol"], "target_weight": row["target_weight"]}
                for row in target_rows
            ]
        ),
        prices=pd.Series(prices),
        total_equity=10431.88,
        starting_cash=581.54,
        target_cash_weight=0.05,
        planning_account=planning_account,
        run_id="orion-2026-08-31-frozen-replay",
        approved_execution_package=_approved_package(target_rows),
        price_basis="timestamped_alpaca_latest_trade_at_authorization",
    )
    config = live_pilot_execution_config(
        approved_cap_usd=10431.88,
        allow_fractional=True,
        max_orders=50,
        min_trade_usd=100.0,
        buy_buffer_pct=0.98,
        ledger_enabled=False,
    )
    config = dataclasses.replace(config, mode="paper")

    raw, meta = compute_transition_trades(request=request, config=config)
    _capital, budget, executable, stats = apply_capital_budget_and_execution_filter(
        trades=raw,
        planning_account=planning_account,
        config=config,
    )

    assert meta["fractional_target_attainment"]["status"] == "PASS"
    assert set(executable["ticker"]) == {"LRCX", "MU", "STX", "WDC"}
    assert dict(zip(executable["ticker"], executable["side"])) == {
        "LRCX": "SELL",
        "MU": "BUY",
        "STX": "BUY",
        "WDC": "SELL",
    }
    assert all(
        not float(quantity).is_integer() for quantity in executable["shares"]
    )
    assert budget["capital_constraint_triggered"] is False
    assert stats["kept"] == 4

    protective_sell_proceeds = sum(
        float(row["shares"]) * float(row["price"]) * 0.99
        for _, row in executable.iterrows()
        if row["side"] == "SELL"
    )
    protective_buy_cost = sum(
        float(row["shares"]) * float(row["price"]) * 1.01
        for _, row in executable.iterrows()
        if row["side"] == "BUY"
    )
    worst_case_cash = 581.54 + protective_sell_proceeds - protective_buy_cost
    assert worst_case_cash / 10431.88 >= POLICY["minimum_cash_weight"]


def test_fractional_policy_cannot_claim_nearest_feasible_waiver() -> None:
    with pytest.raises(ValueError, match="cannot use a nearest-feasible waiver"):
        validate_target_attainment_policy(
            {**POLICY, "nearest_feasible_required": True}
        )


def test_risk_may_raise_cash_above_policy_but_may_not_reduce_it() -> None:
    assert validate_target_attainment_policy(
        POLICY,
        expected_target_cash_weight=0.20,
    )["target_cash_weight"] == 0.05
    with pytest.raises(ValueError, match="cannot exceed the approved cash target"):
        validate_target_attainment_policy(
            POLICY,
            expected_target_cash_weight=0.04,
        )


def _exact_plan(*, allow_fractional: bool = True, expected_cash: float = 50.0):
    return SimpleNamespace(
        risk_state={
            "target_attainment_policy": POLICY,
            "trade_meta": {},
            "decision_nav_reconstruction": {
                "authoritative_account_nav": 1000.0,
                "planning_equity": 1000.0,
                "planning_cash": 300.0,
                "planning_equity_cap": None,
            },
        },
        portfolio_nav=1000.0,
        starting_cash=300.0,
        expected_posttrade_cash=expected_cash,
        expected_posttrade_positions=(
            {"symbol": "AAA", "quantity": 4.75},
            {"symbol": "BBB", "quantity": 4.75},
        ),
        constraints={
            "allow_fractional": allow_fractional,
            "capital_cap_usd": 1000.0,
        },
        orders=(
            {"symbol": "AAA", "side": "BUY", "quantity": 0.75},
            {"symbol": "BBB", "side": "BUY", "quantity": 1.75},
        ),
    )


def test_fractional_full_account_invariant_requires_plan_pin_and_cash_floor() -> None:
    assert full_account_plan_invariant_error(_exact_plan()) is None
    assert (
        full_account_plan_invariant_error(_exact_plan(allow_fractional=False))
        == "fractional_policy_plan_pin_missing"
    )
    assert (
        full_account_plan_invariant_error(_exact_plan(expected_cash=20.0))
        == "expected_posttrade_cash_below_governed_floor"
    )
    invalid = _exact_plan()
    invalid.risk_state["target_attainment_policy"] = {
        **POLICY,
        "nearest_feasible_required": True,
    }
    assert (
        full_account_plan_invariant_error(invalid)
        == "target_attainment_policy_invalid"
    )
