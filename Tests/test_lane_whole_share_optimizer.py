from __future__ import annotations

import copy

from core.lane_whole_share_optimizer import (
    content_hash,
    optimize_cash_aware_quantities,
    validate_cash_aware_realization,
)


def _optimize(**overrides) -> dict:
    arguments = {
        "target_rows": [{"symbol": "AAA", "target_weight": 0.9}],
        "starting_positions": [],
        "marks": {"AAA": 100.0},
        "buy_prices": {"AAA": 100.0},
        "sell_prices": {"AAA": 100.0},
        "equity": 1000.0,
        "starting_cash": 1000.0,
        "target_cash_weight": 0.1,
        "minimum_cash_weight": 0.0,
        "cash_target_tolerance_usd": 1.0,
        "quantity_precision": 0,
        "fee_per_order_usd": 0.0,
        "minimum_order_notional_usd": 0.0,
        "maximum_order_notional_usd": 10_000.0,
        "maximum_total_buy_notional_usd": 10_000.0,
        "maximum_orders": 20,
        "max_candidates": 100_000,
    }
    arguments.update(overrides)
    return optimize_cash_aware_quantities(**arguments)


def test_cash_scarcity_uses_safe_incumbent_below_target_floor() -> None:
    result = _optimize(
        buy_prices={"AAA": 101.0},
        sell_prices={"AAA": 99.0},
        minimum_cash_weight=0.1,
        fee_per_order_usd=1.0,
    )
    assert validate_cash_aware_realization(result) == []
    assert result["allocations"][0]["target_quantity"] == 8.0
    assert result["projected_cash"] == 191.0
    assert result["projected_cash_weight"] >= 0.1
    assert result["cash_target_status"] == "NEAREST_FEASIBLE_OUTSIDE_TOLERANCE"


def test_confirmed_sell_economics_fund_buys_in_one_cash_projection() -> None:
    result = _optimize(
        target_rows=[
            {"symbol": "AAA", "target_weight": 0.5},
            {"symbol": "BBB", "target_weight": 0.4},
        ],
        starting_positions=[{"symbol": "AAA", "quantity": 10.0}],
        marks={"AAA": 100.0, "BBB": 100.0},
        buy_prices={"AAA": 100.0, "BBB": 100.0},
        sell_prices={"AAA": 100.0, "BBB": 100.0},
        starting_cash=0.0,
    )
    assert [(row["symbol"], row["side"], row["quantity"]) for row in result["transitions"]] == [
        ("AAA", "SELL", 5.0),
        ("BBB", "BUY", 4.0),
    ]
    assert result["projected_cash"] == 100.0
    assert result["cash_target_within_tolerance"] is True


def test_identical_objective_tie_uses_symbol_quantity_vector() -> None:
    result = _optimize(
        target_rows=[
            {"symbol": "AAA", "target_weight": 0.45},
            {"symbol": "BBB", "target_weight": 0.45},
        ],
        marks={"AAA": 60.0, "BBB": 60.0},
        buy_prices={"AAA": 60.0, "BBB": 60.0},
        sell_prices={"AAA": 60.0, "BBB": 60.0},
        equity=100.0,
        starting_cash=100.0,
        cash_target_tolerance_usd=100.0,
    )
    assert {
        row["symbol"]: row["target_quantity"] for row in result["allocations"]
    } == {"AAA": 0.0, "BBB": 1.0}
    assert result["tie_breakers"][-1] == "symbol_quantity_vector"


def test_partial_starting_holding_quantizes_delta_not_final_holding() -> None:
    result = _optimize(
        target_rows=[{"symbol": "AAA", "target_weight": 0.12}],
        starting_positions=[{"symbol": "AAA", "quantity": 10.5}],
        marks={"AAA": 100.0},
        buy_prices={"AAA": 100.0},
        sell_prices={"AAA": 100.0},
        equity=10_000.0,
        starting_cash=8950.0,
        target_cash_weight=0.88,
        cash_target_tolerance_usd=100.0,
    )
    assert result["allocations"][0]["target_quantity"] == 11.5
    assert result["transitions"][0]["quantity"] == 1.0
    assert result["quantity_precision"] == 0
    assert validate_cash_aware_realization(result) == []


def test_fees_and_price_boundaries_are_explicit_and_hash_bound() -> None:
    result = _optimize(
        buy_prices={"AAA": 100.99},
        sell_prices={"AAA": 99.01},
        fee_per_order_usd=2.0,
        cash_target_tolerance_usd=20.0,
    )
    transition = result["transitions"][0]
    assert transition["enforcement_price"] == 100.99
    assert transition["estimated_fee"] == 2.0
    assert transition["cash_effect"] == -(transition["notional"] + 2.0)

    tampered = copy.deepcopy(result)
    tampered["transitions"][0]["quantity"] = 0.5
    body = copy.deepcopy(tampered)
    body.pop("content_hash", None)
    tampered["content_hash"] = content_hash(body)
    assert "lane_whole_share:order_quantity_precision" in validate_cash_aware_realization(
        tampered
    )
