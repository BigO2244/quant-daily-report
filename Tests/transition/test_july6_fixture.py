"""Mandatory July 6, 2026 regression fixture (Architecture V2.1 §15).

Live holdings ABBV / ALL / C, buying power $0.88, approved cap $500, target
includes ALL. The engine must:

* recognize existing ALL exposure and compute the INCREMENTAL need (never a blind
  buy of the full target);
* assess ABBV / C (not in target) for exit;
* with sells unsupported (Option A) -> block with EXISTING_POSITIONS_REQUIRE_ROTATION;
* with sells supported -> emit sell intents FIRST and never size a buy against the
  $0.88 pre-sell buying power;
* NEVER submit an ALL buy justified by the $500 cap alone (the forbidden outcome).
"""

from __future__ import annotations

import pytest

from transition import (
    AccountSnapshot,
    BLOCK_ROTATION_UNSUPPORTED,
    BLOCK_SELLS_NOT_FILLED,
    CapitalPolicy,
    Holdings,
    ModeConstraints,
    OrderPolicy,
    Position,
    TargetPortfolio,
    TargetPosition,
    compute_transition,
)

ALL_PRICE = 180.0
ABBV_PRICE = 150.0
C_PRICE = 60.0


def _holdings() -> Holdings:
    return Holdings(
        (
            Position("ABBV", 2.0, ABBV_PRICE),
            Position("ALL", 1.0, ALL_PRICE),
            Position("C", 3.0, C_PRICE),
        )
    )


def _target() -> TargetPortfolio:
    # Only ALL is targeted; ABBV and C must be assessed for exit.
    return TargetPortfolio((TargetPosition("ALL", 0.5, ALL_PRICE),), cash_buffer=1.0)


def _account() -> AccountSnapshot:
    return AccountSnapshot(
        cash=0.88, buying_power=0.88, equity=506.82, as_of="2026-07-06T13:45:00Z"
    )


def _capital() -> CapitalPolicy:
    return CapitalPolicy(approved_cap_usd=500.0)


def _order_policy() -> OrderPolicy:
    return OrderPolicy(fractional=True, min_trade_usd=100.0)


def test_incremental_all_need_is_computed_not_blind() -> None:
    plan = compute_transition(
        current_holdings=_holdings(),
        target_holdings=_target(),
        account_snapshot=_account(),
        capital_policy=_capital(),
        order_policy=_order_policy(),
        mode_constraints=ModeConstraints(sells_supported=True, max_orders=3),
    )
    incremental = plan.diagnostics["incremental_need_shares"]
    # target_dollars = 0.5 * 506.82 * 1.0 = 253.41 ; target_shares = 253.41/180 = 1.4078
    # current ALL = 1.0 -> incremental need = 0.4078 shares (NOT the full 1.4078).
    expected_target_shares = (0.5 * 506.82 * 1.0) / ALL_PRICE
    assert incremental["ALL"] == pytest.approx(expected_target_shares - 1.0, abs=1e-9)
    assert incremental["ALL"] < expected_target_shares  # incremental, not the full target
    assert incremental["ALL"] > 0.0


def test_sells_unsupported_blocks_with_rotation_reason() -> None:
    plan = compute_transition(
        current_holdings=_holdings(),
        target_holdings=_target(),
        account_snapshot=_account(),
        capital_policy=_capital(),
        order_policy=_order_policy(),
        mode_constraints=ModeConstraints(sells_supported=False, max_orders=1),
    )
    assert plan.blocked is True
    assert plan.block_reason == BLOCK_ROTATION_UNSUPPORTED
    assert plan.buy_orders_intended == ()
    # ABBV and C are recognized as exits (holdings-to-sell), even though the mode
    # cannot execute them.
    assert set(plan.holdings_to_sell) == {"ABBV", "C"}


def test_sells_supported_emits_sells_first_and_defers_buys() -> None:
    plan = compute_transition(
        current_holdings=_holdings(),
        target_holdings=_target(),
        account_snapshot=_account(),
        capital_policy=_capital(),
        order_policy=_order_policy(),
        mode_constraints=ModeConstraints(sells_supported=True, max_orders=3),
    )
    # Sells are emitted; buys are deferred until a post-sell snapshot confirms fills.
    assert {s.symbol for s in plan.sell_orders_intended} == {"ABBV", "C"}
    assert plan.buy_orders_intended == ()
    assert plan.blocked is True
    assert plan.block_reason == BLOCK_SELLS_NOT_FILLED
    # No buy was sized against the $0.88 pre-sell buying power.
    assert plan.diagnostics["deployed_buy_notional"] == 0.0


@pytest.mark.parametrize("sells_supported", [False, True])
def test_forbidden_all_buy_justified_by_cap_alone_is_impossible(sells_supported: bool) -> None:
    plan = compute_transition(
        current_holdings=_holdings(),
        target_holdings=_target(),
        account_snapshot=_account(),
        capital_policy=_capital(),
        order_policy=_order_policy(),
        mode_constraints=ModeConstraints(sells_supported=sells_supported, max_orders=3),
    )
    # The core July-6 failure: an ALL buy submitted solely because planned notional
    # was under the $500 cap. That can never happen — no ALL buy intent, ever.
    assert all(b.symbol != "ALL" for b in plan.buy_orders_intended)
    assert plan.buy_orders_intended == ()
    # Cap is never treated as spendable cash: tradable capital tracks the $0.88
    # buying power (minus reserve -> 0), never the $500 cap.
    assert plan.diagnostics["tradable_capital"] == 0.0
    assert plan.diagnostics["approved_cap_usd"] == 500.0
