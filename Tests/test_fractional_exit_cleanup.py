from __future__ import annotations

import pandas as pd
import pytest

from core.live_pilot_guardrails import validate_live_pilot_plan
from execution.core import (
    ExecutionRequest,
    apply_capital_budget_and_execution_filter,
    compute_transition_trades,
    live_pilot_execution_config,
)


def _request() -> ExecutionRequest:
    return ExecutionRequest(
        holdings=pd.DataFrame(
            [{"ticker": "ZZZ", "sleeve": "legacy", "shares": 0.5}]
        ),
        targets=pd.DataFrame(
            [{"ticker": "AAA", "sleeve": "target", "target_weight": 0.5}]
        ),
        prices=pd.Series({"ZZZ": 40.0, "AAA": 100.0}, dtype=float),
        total_equity=1000.0,
        starting_cash=500.0,
        target_cash_weight=0.5,
        planning_account={
            "cash": "500",
            "settled_cash": "500",
            "equity": "1000",
            "buying_power": "500",
        },
        run_id="fractional-exit-fixture",
    )


def _executable(*, allow_fractional_sells: bool) -> pd.DataFrame:
    request = _request()
    config = live_pilot_execution_config(
        approved_cap_usd=1000.0,
        allow_fractional=False,
        allow_fractional_sells=allow_fractional_sells,
        fractional_sell_min_trade_usd=1.0,
        max_orders=10,
        min_trade_usd=100.0,
    )
    raw, _ = compute_transition_trades(request=request, config=config)
    _, _, executable, _ = apply_capital_budget_and_execution_filter(
        trades=raw,
        planning_account=request.planning_account,
        config=config,
    )
    return executable


def test_fractional_exit_cleanup_preserves_small_off_target_sell() -> None:
    executable = _executable(allow_fractional_sells=True)
    sell = executable.loc[executable["side"] == "SELL"].iloc[0]
    assert sell["ticker"] == "ZZZ"
    assert sell["shares"] == pytest.approx(0.5)
    assert sell["notional"] == pytest.approx(20.0)


def test_default_live_policy_still_drops_fractional_exit() -> None:
    executable = _executable(allow_fractional_sells=False)
    assert executable.loc[executable["side"] == "SELL"].empty


def test_guardrail_override_accepts_fractional_sell_but_not_fractional_buy() -> None:
    env = {
        "CAERUS_LIVE_PILOT_SELLS_ENABLED": "1",
        "CAERUS_LIVE_PILOT_SELL_WHITELIST": "*",
        "CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL": "0",
    }
    result = validate_live_pilot_plan(
        [
            {"ticker": "ZZZ", "side": "SELL", "shares": 0.5, "price": 40.0},
            {"ticker": "AAA", "side": "BUY", "shares": 0.5, "price": 100.0},
        ],
        env=env,
        capital_cap_usd=1000.0,
        max_orders=10,
        run_id="fractional-guard-fixture",
        sell_inventory={"ZZZ": 0.5},
        allow_fractional_sells=True,
    )
    assert result.status == "PASS"
    assert [(order.symbol, order.side, order.qty) for order in result.orders] == [
        ("ZZZ", "SELL", 0.5)
    ]
    assert result.dropped_orders[0].reason_code == "AAA:fractional_qty_not_allowed"

