"""Invariant 7: identical inputs -> identical plan (no wall-clock, stable ordering)."""

from __future__ import annotations

from transition import (
    AccountSnapshot,
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


def _inputs():
    holdings = Holdings(
        (
            Position("AAA", 5.0, 100.0),
            Position("ZZZ", 3.0, 40.0),
            Position("MMM", 2.0, 250.0),
        )
    )
    target = TargetPortfolio(
        (
            TargetPosition("AAA", 0.4, 100.0),
            TargetPosition("BBB", 0.3, 50.0),
            TargetPosition("MMM", 0.2, 250.0),
        ),
        cash_buffer=1.0,
    )
    account = AccountSnapshot(cash=8000.0, buying_power=8000.0, equity=10000.0, as_of=_AS_OF)
    return holdings, target, account


def _run():
    holdings, target, account = _inputs()
    return compute_transition(
        current_holdings=holdings,
        target_holdings=target,
        account_snapshot=account,
        capital_policy=CapitalPolicy(approved_cap_usd=None, reserve=0.0),
        order_policy=OrderPolicy(fractional=True, min_trade_usd=100.0),
        mode_constraints=ModeConstraints(sells_supported=True, max_orders=None),
    )


def test_repeated_invocations_are_identical() -> None:
    plans = [_run() for _ in range(5)]
    first = plans[0]
    for other in plans[1:]:
        assert other == first  # frozen dataclass equality across all fields


def test_plan_is_hashable_stable_and_order_stable() -> None:
    a = _run()
    b = _run()
    # Intent tuples must be identical sequences (stable sort keys, no set/dict order leak).
    assert a.sell_orders_intended == b.sell_orders_intended
    assert a.buy_orders_intended == b.buy_orders_intended
    assert a.holdings_to_sell == b.holdings_to_sell
    assert repr(a) == repr(b)
