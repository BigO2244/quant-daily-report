from __future__ import annotations

from core.generic_live_v1_capital import build_generic_live_v1_capital_proof
from Tests.test_generic_live_v1_submission import _ready


def test_limit_price_fee_and_dynamic_95_5_limits_are_explicit() -> None:
    _, plan = _ready()
    proof = build_generic_live_v1_capital_proof(
        exact_plan=plan, fresh_equity_usd=460.9, fresh_cash_usd=460.9,
    )
    order = [*plan["sell_orders"], *plan["buy_orders"]][0]
    assert proof["dynamic_gross_cap_usd"] == 437.0
    assert proof["required_cash_reserve_usd"] == 23.0
    assert proof["worst_case_posttrade_gross_usd"] == (
        order["quantity"] * order["enforcement_price"]
    )
    assert proof["worst_case_posttrade_cash_usd"] == (
        plan["starting_cash"]
        - order["quantity"] * order["enforcement_price"]
        - 0.01
    )
    assert proof["gross_limit_pass"] is True
    assert proof["cash_reserve_pass"] is True


def test_lower_fresh_equity_tightens_cap_and_blocks_same_plan() -> None:
    _, plan = _ready()
    proof = build_generic_live_v1_capital_proof(
        exact_plan=plan, fresh_equity_usd=400.0, fresh_cash_usd=460.9,
    )
    assert proof["dynamic_gross_cap_usd"] == 380.0
    assert proof["gross_limit_pass"] is False
    assert proof["cash_reserve_pass"] is True


def test_capital_proof_is_deterministic_and_plan_bound() -> None:
    _, plan = _ready()
    first = build_generic_live_v1_capital_proof(
        exact_plan=plan, fresh_equity_usd=460.9, fresh_cash_usd=460.9,
    )
    second = build_generic_live_v1_capital_proof(
        exact_plan=plan, fresh_equity_usd=460.9, fresh_cash_usd=460.9,
    )
    assert first == second
    assert first["plan_hash"] == plan["content_hash"]

