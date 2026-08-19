from __future__ import annotations

import copy

import pytest

from core.generic_live_dynamic_capital import (
    GenericLiveDynamicCapitalError,
    build_generic_live_dynamic_capital_proof,
    derive_dynamic_capital_limits,
)
from Tests.test_generic_live_v1_submission import _ready


def _plan():
    return copy.deepcopy(_ready()[1])


def _build(plan, *, equity=460.9, cash=460.9, pending_in=0, pending_out=0, age=30):
    return build_generic_live_dynamic_capital_proof(
        exact_plan=plan,
        fresh_net_liquidation_equity_usd=equity,
        fresh_cash_usd=cash,
        pending_transfer_in_usd=pending_in,
        pending_transfer_out_usd=pending_out,
        observation_age_seconds=age,
    )


def test_dynamic_capital_has_no_nominal_ceiling_and_ignores_buying_power():
    plan = _plan()
    proof = _build(plan, equity=460.9, cash=plan["starting_cash"])
    assert proof["nominal_capital_ceiling_usd"] is None
    assert proof["dynamic_gross_cap_usd"] == pytest.approx(437.855)
    assert proof["required_settled_cash_reserve_usd"] == pytest.approx(23.045)
    assert proof["buying_power_used"] is False
    assert proof["margin_multiplier_used"] is False


@pytest.mark.parametrize(("pending_in", "pending_out"), [(1, 0), (0, 1)])
def test_pending_transfers_fail_closed(pending_in, pending_out):
    plan = _plan()
    with pytest.raises(GenericLiveDynamicCapitalError, match="pending transfers"):
        _build(plan, cash=plan["starting_cash"], pending_in=pending_in, pending_out=pending_out)


def test_stale_broker_capital_evidence_fails_closed():
    plan = _plan()
    with pytest.raises(GenericLiveDynamicCapitalError, match="120 seconds"):
        _build(plan, cash=plan["starting_cash"], age=120)


def test_deposit_and_withdrawal_change_limits_automatically():
    base = derive_dynamic_capital_limits(
        fresh_net_liquidation_equity_usd=460.9, fresh_cash_usd=460.9,
        pending_transfer_in_usd=0, pending_transfer_out_usd=0,
        observation_age_seconds=30,
    )
    deposit = derive_dynamic_capital_limits(
        fresh_net_liquidation_equity_usd=960.9, fresh_cash_usd=960.9,
        pending_transfer_in_usd=0, pending_transfer_out_usd=0,
        observation_age_seconds=30,
    )
    withdrawal = derive_dynamic_capital_limits(
        fresh_net_liquidation_equity_usd=300.0, fresh_cash_usd=300.0,
        pending_transfer_in_usd=0, pending_transfer_out_usd=0,
        observation_age_seconds=30,
    )
    assert deposit["dynamic_gross_cap_usd"] > base["dynamic_gross_cap_usd"]
    assert withdrawal["dynamic_gross_cap_usd"] < base["dynamic_gross_cap_usd"]


def test_buy_cannot_use_cash_reserved_or_borrowed():
    limits = derive_dynamic_capital_limits(
        fresh_net_liquidation_equity_usd=460.9, fresh_cash_usd=460.9,
        pending_transfer_in_usd=0, pending_transfer_out_usd=0,
        observation_age_seconds=30,
    )
    assert limits["maximum_new_buy_cash_usd"] == pytest.approx(437.855)
    assert 440.01 > limits["maximum_new_buy_cash_usd"]


def test_sell_proceeds_are_not_counted_as_same_session_settled_cash():
    plan = _plan()
    if not plan["sell_orders"]:
        pytest.skip("fixture needs a sell")
    proof = _build(plan, equity=460.9, cash=plan["starting_cash"])
    assert proof["worst_case_posttrade_settled_cash_usd"] == pytest.approx(60.89)
    assert proof["unsettled_funds_used"] is False
