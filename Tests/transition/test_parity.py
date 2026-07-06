"""Golden parity: the extracted engine reproduces the paper transition functions.

This is the no-behavior-change proof for Task 1. We drive the REAL paper functions
(``build_rebalance_trades`` for sells, ``_rebuild_post_sell_buy_trades`` for buys,
``_post_sell_buy_budget`` for the capital budget) with constructed inputs and assert
the engine emits the same intents (symbol / shares / notional).

Parity scope: the engine covers the core transition semantics — target-minus-current
diff, incremental need, the min(cash, buying-power-minus-reserve, risk, cap) budget,
and the greedy per-order fit. Paper-only execution guards that are NOT transition
semantics (slippage pricing, turnover cap, per-position change caps, whole-share cash
sweep) are neutralized in these fixtures (slippage_bps=0, generous risk caps) so the
comparison isolates the lifted logic.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from paper.paper_broker import (
    PaperConfig,
    _post_sell_buy_budget,
    _rebuild_post_sell_buy_trades,
    build_rebalance_trades,
)
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
# Paper's post-sell reserve floor (CAPITAL_POSTSELL_RESERVE_MIN_CASH default) with
# env unset. The engine reproduces the same equity-scaled reserve.
_RESERVE_MIN_CASH = 100.0


def _cfg(*, allow_fractional: bool = True, min_trade: float = 100.0) -> PaperConfig:
    return PaperConfig(
        initial_equity=10000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,               # neutralize slippage so ref price == engine price
        allow_fractional=allow_fractional,
        min_trade_dollars=min_trade,
        cash_buffer_bps=0.0,
        trading_mode="paper",           # so _compute_buy_budget uses broker buying_power
        rebalance_deadband_pct=0.0,     # neutralize deadband
        max_turnover_pct=100.0,         # neutralize turnover cap
        max_position_change_pct=100.0,  # neutralize per-position change cap
        max_position_pct=100.0,         # neutralize per-position cap
        max_trades_per_day=100,
        cash_target_weight_default=0.0,
    )


def _prices(mapping: dict[str, float]) -> pd.Series:
    return pd.Series(mapping, dtype=float)


def _holdings_df(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {"ticker": [t for t, _ in rows], "sleeve": ["s"] * len(rows), "shares": [s for _, s in rows]}
    )


def _targets_df(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame({"ticker": [t for t, _ in rows], "target_weight": [w for _, w in rows]})


def _engine_capital_policy(cap: float | None = None) -> CapitalPolicy:
    # reserve params match paper's _reserve_cash_for_equity defaults.
    return CapitalPolicy(
        approved_cap_usd=cap,
        reserve=_RESERVE_MIN_CASH,
        risk_cash_weight=0.0,
        reserve_equity_pct=0.005,
        reserve_max_pct=0.05,
    )


# --------------------------------------------------------------------------- #
# BUY parity vs _rebuild_post_sell_buy_trades (the canonical rebudget)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("allow_fractional", [True, False])
def test_buy_sizing_parity_with_rebuild_post_sell(allow_fractional: bool) -> None:
    equity = 10000.0
    cash = 5000.0
    buying_power = 5000.0
    price_map = {"AAA": 100.0, "BBB": 50.0, "CCC": 200.0}
    target_rows = [("AAA", 0.3), ("BBB", 0.2), ("CCC", 0.1)]
    holding_rows = [("AAA", 10.0)]  # hold some AAA; BBB/CCC new. No sells needed.

    cfg = _cfg(allow_fractional=allow_fractional)
    account = {"cash": cash, "equity": equity, "buying_power": buying_power}

    ref_budget, _budget_meta = _post_sell_buy_budget(
        account=account,
        cfg=cfg,
        target_cash_weight=0.0,
        fallback_equity=equity,
        capital_constraint_clear=True,
    )
    ref_frame, _meta, _skipped = _rebuild_post_sell_buy_trades(
        holdings=_holdings_df(holding_rows),
        targets=_targets_df(target_rows),
        prices=_prices(price_map),
        total_equity=equity,
        buy_budget=ref_budget,
        cfg=cfg,
        max_buy_orders=cfg.max_trades_per_day,
    )
    ref_rows = {
        str(r["ticker"]): (float(r["shares"]), float(r["notional"]))
        for _, r in ref_frame.iterrows()
    }

    plan = compute_transition(
        current_holdings=Holdings(
            tuple(Position(t, s, price_map[t]) for t, s in holding_rows)
        ),
        target_holdings=TargetPortfolio(
            tuple(TargetPosition(t, w, price_map[t]) for t, w in target_rows), cash_buffer=1.0
        ),
        account_snapshot=AccountSnapshot(
            cash=cash, buying_power=buying_power, equity=equity, as_of=_AS_OF
        ),
        capital_policy=_engine_capital_policy(cap=None),
        order_policy=OrderPolicy(fractional=allow_fractional, min_trade_usd=cfg.min_trade_dollars),
        mode_constraints=ModeConstraints(sells_supported=True, max_orders=cfg.max_trades_per_day),
    )

    # Budget parity.
    assert plan.diagnostics["tradable_capital"] == pytest.approx(float(ref_budget), abs=1e-6)
    # Same set of buy symbols.
    engine_rows = {b.symbol: (b.shares, b.notional) for b in plan.buy_orders_intended}
    assert set(engine_rows) == set(ref_rows)
    # Same shares/notional per symbol.
    for symbol, (ref_shares, ref_notional) in ref_rows.items():
        eng_shares, eng_notional = engine_rows[symbol]
        assert eng_shares == pytest.approx(ref_shares, abs=1e-6), symbol
        assert eng_notional == pytest.approx(ref_notional, abs=1e-6), symbol


def test_buy_parity_when_budget_forces_a_clip() -> None:
    # Tight budget so the lowest-weight order is capital-clipped — exercises the
    # greedy-fit clip path in both implementations.
    equity = 10000.0
    cash = 2600.0
    buying_power = 2600.0
    price_map = {"AAA": 100.0, "BBB": 50.0, "CCC": 200.0}
    target_rows = [("AAA", 0.2), ("BBB", 0.1), ("CCC", 0.1)]
    cfg = _cfg(allow_fractional=True)
    account = {"cash": cash, "equity": equity, "buying_power": buying_power}

    ref_budget, _ = _post_sell_buy_budget(
        account=account, cfg=cfg, target_cash_weight=0.0,
        fallback_equity=equity, capital_constraint_clear=True,
    )
    ref_frame, _meta, _skipped = _rebuild_post_sell_buy_trades(
        holdings=_holdings_df([]),
        targets=_targets_df(target_rows),
        prices=_prices(price_map),
        total_equity=equity,
        buy_budget=ref_budget,
        cfg=cfg,
        max_buy_orders=cfg.max_trades_per_day,
    )
    ref_rows = {str(r["ticker"]): float(r["notional"]) for _, r in ref_frame.iterrows()}

    plan = compute_transition(
        current_holdings=Holdings(()),
        target_holdings=TargetPortfolio(
            tuple(TargetPosition(t, w, price_map[t]) for t, w in target_rows), cash_buffer=1.0
        ),
        account_snapshot=AccountSnapshot(
            cash=cash, buying_power=buying_power, equity=equity, as_of=_AS_OF
        ),
        capital_policy=_engine_capital_policy(cap=None),
        order_policy=OrderPolicy(fractional=True, min_trade_usd=cfg.min_trade_dollars),
        mode_constraints=ModeConstraints(sells_supported=True, max_orders=cfg.max_trades_per_day),
    )
    engine_rows = {b.symbol: b.notional for b in plan.buy_orders_intended}
    assert set(engine_rows) == set(ref_rows)
    for symbol, ref_notional in ref_rows.items():
        assert engine_rows[symbol] == pytest.approx(ref_notional, abs=1e-6), symbol
    # Total deployed does not exceed the budget in either implementation.
    assert sum(engine_rows.values()) <= float(ref_budget) + 1e-6


# --------------------------------------------------------------------------- #
# SELL parity vs build_rebalance_trades
# --------------------------------------------------------------------------- #
def test_sell_intent_parity_with_build_rebalance_trades() -> None:
    equity = 10000.0
    price_map = {"AAA": 100.0, "ZZZ": 40.0}
    # Hold more AAA than target (reduce) and hold ZZZ which is not targeted (exit).
    holding_rows = [("AAA", 80.0), ("ZZZ", 5.0)]
    target_rows = [("AAA", 0.5)]  # target 50 shares of AAA -> reduce 30
    cfg = _cfg(allow_fractional=True)

    trades_df, _details = build_rebalance_trades(
        holdings=_holdings_df(holding_rows),
        targets=_targets_df(target_rows),
        prices=_prices(price_map),
        total_equity=equity,
        starting_cash=1000.0,
        target_cash_weight=0.0,
        cfg=cfg,
    )
    ref_sells = {
        str(r["ticker"]): (float(r["shares"]), float(r["notional"]))
        for _, r in trades_df.iterrows()
        if str(r["side"]).upper() == "SELL"
    }

    plan = compute_transition(
        current_holdings=Holdings(
            tuple(Position(t, s, price_map[t]) for t, s in holding_rows)
        ),
        target_holdings=TargetPortfolio(
            tuple(TargetPosition(t, w, price_map[t]) for t, w in target_rows), cash_buffer=1.0
        ),
        account_snapshot=AccountSnapshot(
            cash=1000.0, buying_power=1000.0, equity=equity, as_of=_AS_OF
        ),
        capital_policy=_engine_capital_policy(cap=None),
        order_policy=OrderPolicy(fractional=True, min_trade_usd=cfg.min_trade_dollars),
        # sells_supported so the engine emits the sell intents (buys are deferred).
        mode_constraints=ModeConstraints(sells_supported=True, max_orders=None),
    )
    engine_sells = {
        s.symbol: (s.shares, s.notional) for s in plan.sell_orders_intended
    }
    assert set(engine_sells) == set(ref_sells)
    for symbol, (ref_shares, ref_notional) in ref_sells.items():
        eng_shares, eng_notional = engine_sells[symbol]
        assert eng_shares == pytest.approx(ref_shares, abs=1e-6), symbol
        assert eng_notional == pytest.approx(ref_notional, abs=1e-6), symbol
