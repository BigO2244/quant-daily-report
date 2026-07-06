"""Capital-rule invariants (Architecture V2.1 §13, Workstream B Task 1 invariants 1-6)."""

from __future__ import annotations

import pytest

from transition import (
    AccountSnapshot,
    BLOCK_BUYING_POWER_UNAVAILABLE,
    BLOCK_INSUFFICIENT_BUYING_POWER,
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

_AS_OF = "2026-07-06T13:45:00Z"


def _run(holdings, target, account, *, cap, order_policy, sells_supported=True, max_orders=None,
         risk_cash_weight=0.0, reserve=100.0):
    return compute_transition(
        current_holdings=holdings,
        target_holdings=target,
        account_snapshot=account,
        capital_policy=CapitalPolicy(
            approved_cap_usd=cap, reserve=reserve, risk_cash_weight=risk_cash_weight
        ),
        order_policy=order_policy,
        mode_constraints=ModeConstraints(sells_supported=sells_supported, max_orders=max_orders),
    )


# Invariant 1: tradable_capital = min(buying_power, cap_remaining, target_need); cap != cash.
def test_cap_is_a_ceiling_not_cash() -> None:
    # Buying power is huge, but the approved cap is $300 -> deployment capped at $300.
    target = TargetPortfolio((TargetPosition("AAA", 1.0, 100.0),), cash_buffer=1.0)
    holdings = Holdings(())
    account = AccountSnapshot(cash=100000.0, buying_power=100000.0, equity=100000.0, as_of=_AS_OF)
    plan = _run(
        holdings, target, account,
        cap=300.0, reserve=0.0,
        order_policy=OrderPolicy(fractional=True, min_trade_usd=100.0),
    )
    assert plan.blocked is False
    deployed = sum(b.notional for b in plan.buy_orders_intended)
    assert deployed == pytest.approx(300.0, abs=1e-6)  # capped by cap, not by the 100k cash
    assert plan.diagnostics["tradable_capital"] == pytest.approx(300.0, abs=1e-6)


def test_cap_none_means_no_ceiling() -> None:
    target = TargetPortfolio((TargetPosition("AAA", 0.5, 100.0),), cash_buffer=1.0)
    account = AccountSnapshot(cash=10000.0, buying_power=10000.0, equity=10000.0, as_of=_AS_OF)
    plan = _run(
        Holdings(()), target, account, cap=None, reserve=0.0,
        order_policy=OrderPolicy(fractional=True, min_trade_usd=100.0),
    )
    # target_dollars = 0.5*10000 = 5000 -> 50 shares @ 100 = $5000, within cash/bp.
    assert sum(b.notional for b in plan.buy_orders_intended) == pytest.approx(5000.0, abs=1e-6)


# Invariant 2: held-and-targeted -> incremental need, never blind.
def test_incremental_need_for_held_and_targeted() -> None:
    target = TargetPortfolio((TargetPosition("AAA", 1.0, 100.0),), cash_buffer=1.0)
    holdings = Holdings((Position("AAA", 30.0, 100.0),))  # already hold 30
    account = AccountSnapshot(cash=10000.0, buying_power=10000.0, equity=10000.0, as_of=_AS_OF)
    plan = _run(
        holdings, target, account, cap=None, reserve=0.0,
        order_policy=OrderPolicy(fractional=True, min_trade_usd=100.0),
    )
    # target 100 shares, hold 30 -> incremental 70 shares = $7000 (not the full 100/$10000).
    assert plan.diagnostics["incremental_need_shares"]["AAA"] == pytest.approx(70.0, abs=1e-9)
    assert sum(b.shares for b in plan.buy_orders_intended) == pytest.approx(70.0, abs=1e-6)
    assert "AAA" in plan.holdings_to_increase


# Invariant 3: rotation required + sells unsupported -> blocked EXISTING_POSITIONS_REQUIRE_ROTATION.
def test_rotation_required_sells_unsupported_blocks() -> None:
    target = TargetPortfolio((TargetPosition("AAA", 1.0, 100.0),), cash_buffer=1.0)
    holdings = Holdings((Position("ZZZ", 5.0, 100.0),))  # ZZZ not in target -> must exit
    account = AccountSnapshot(cash=5000.0, buying_power=5000.0, equity=10000.0, as_of=_AS_OF)
    plan = _run(
        holdings, target, account, cap=1000.0, reserve=0.0, sells_supported=False,
        order_policy=OrderPolicy(fractional=True, min_trade_usd=100.0),
    )
    assert plan.blocked is True
    assert plan.block_reason == BLOCK_ROTATION_UNSUPPORTED
    assert plan.buy_orders_intended == ()


# Invariant 4: buying power None -> blocked, not defaulted.
def test_buying_power_none_blocks() -> None:
    target = TargetPortfolio((TargetPosition("AAA", 1.0, 100.0),), cash_buffer=1.0)
    account = AccountSnapshot(cash=5000.0, buying_power=None, equity=10000.0, as_of=_AS_OF)
    plan = _run(
        Holdings(()), target, account, cap=1000.0, reserve=0.0,
        order_policy=OrderPolicy(fractional=True, min_trade_usd=100.0),
    )
    assert plan.blocked is True
    assert plan.block_reason == BLOCK_BUYING_POWER_UNAVAILABLE
    assert plan.buy_orders_intended == ()


# Invariant 5: sells precede buys — when rotation is needed, buys are deferred.
def test_sells_precede_buys_when_rotation_needed() -> None:
    target = TargetPortfolio((TargetPosition("AAA", 0.5, 100.0),), cash_buffer=1.0)
    holdings = Holdings((Position("ZZZ", 10.0, 100.0),))  # exit ZZZ to fund AAA
    account = AccountSnapshot(cash=50.0, buying_power=50.0, equity=10000.0, as_of=_AS_OF)
    plan = _run(
        holdings, target, account, cap=5000.0, reserve=0.0, sells_supported=True,
        order_policy=OrderPolicy(fractional=True, min_trade_usd=100.0),
    )
    assert len(plan.sell_orders_intended) == 1
    assert plan.sell_orders_intended[0].symbol == "ZZZ"
    assert plan.buy_orders_intended == ()  # deferred until post-sell snapshot
    assert plan.block_reason == BLOCK_SELLS_NOT_FILLED


# Invariant 6: partial/unresolved sells in the snapshot -> buys blocked.
def test_open_sell_orders_block_buys() -> None:
    target = TargetPortfolio((TargetPosition("AAA", 0.5, 100.0),), cash_buffer=1.0)
    holdings = Holdings((Position("AAA", 10.0, 100.0),))  # no new sells needed
    account = AccountSnapshot(
        cash=5000.0, buying_power=5000.0, equity=10000.0, as_of=_AS_OF, open_sell_orders=1
    )
    plan = _run(
        holdings, target, account, cap=None, reserve=0.0, sells_supported=True,
        order_policy=OrderPolicy(fractional=True, min_trade_usd=100.0),
    )
    assert plan.blocked is True
    assert plan.block_reason == BLOCK_SELLS_NOT_FILLED
    assert plan.buy_orders_intended == ()


# min-trade floor: a sub-$100 buy is dropped.
def test_min_trade_floor_drops_small_buys() -> None:
    target = TargetPortfolio((TargetPosition("AAA", 1.0, 100.0),), cash_buffer=1.0)
    account = AccountSnapshot(cash=50.0, buying_power=50.0, equity=50.0, as_of=_AS_OF)
    plan = _run(
        Holdings(()), target, account, cap=500.0, reserve=0.0,
        order_policy=OrderPolicy(fractional=True, min_trade_usd=100.0),
    )
    # target 0.5 shares = $50 < $100 min-trade -> no buy candidate at all.
    assert plan.buy_needs == ()
    assert plan.buy_orders_intended == ()


def test_insufficient_buying_power_blocks_when_nothing_deployable() -> None:
    # A material target buy ($100) but only $0.88 buying power -> nothing fits.
    target = TargetPortfolio((TargetPosition("AAA", 1.0, 100.0),), cash_buffer=1.0)
    account = AccountSnapshot(cash=0.88, buying_power=0.88, equity=200.0, as_of=_AS_OF)
    plan = _run(
        Holdings(()), target, account, cap=500.0, reserve=0.0,
        order_policy=OrderPolicy(fractional=True, min_trade_usd=100.0),
    )
    assert plan.blocked is True
    assert plan.block_reason == BLOCK_INSUFFICIENT_BUYING_POWER


# fractional on/off rounding.
def test_fractional_off_floors_to_whole_shares() -> None:
    target = TargetPortfolio((TargetPosition("AAA", 1.0, 300.0),), cash_buffer=1.0)
    account = AccountSnapshot(cash=1000.0, buying_power=1000.0, equity=1000.0, as_of=_AS_OF)
    plan = _run(
        Holdings(()), target, account, cap=None, reserve=0.0,
        order_policy=OrderPolicy(fractional=False, min_trade_usd=100.0),
    )
    # target_dollars=1000 -> 3.33 shares @300 -> floor 3 shares = $900.
    assert len(plan.buy_orders_intended) == 1
    assert plan.buy_orders_intended[0].shares == pytest.approx(3.0, abs=1e-9)


def test_fractional_on_keeps_fractional_shares() -> None:
    # weight 0.1 of $10k equity -> $1000 target; ample budget so no clip -> full
    # fractional 3.333... shares are preserved (not floored).
    target = TargetPortfolio((TargetPosition("AAA", 0.1, 300.0),), cash_buffer=1.0)
    account = AccountSnapshot(cash=10000.0, buying_power=10000.0, equity=10000.0, as_of=_AS_OF)
    plan = _run(
        Holdings(()), target, account, cap=None, reserve=0.0,
        order_policy=OrderPolicy(fractional=True, min_trade_usd=100.0),
    )
    assert plan.buy_orders_intended[0].shares == pytest.approx(1000.0 / 300.0, abs=1e-9)
