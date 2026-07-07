from __future__ import annotations

import json

import pytest

from Tests.parity.paper_execution_harness import (
    capture_all_scenarios,
    capture_paper_native_execution,
    golden_path,
    stable_json,
)
from Tests.parity.scenarios import scenario_by_name, scenarios


def test_paper_execution_harness_is_deterministic() -> None:
    first = capture_all_scenarios()
    second = capture_all_scenarios()
    assert first == second


@pytest.mark.parametrize("scenario", scenarios(), ids=lambda scenario: scenario.name)
def test_paper_native_execution_matches_golden(scenario) -> None:
    path = golden_path(scenario.name)
    assert path.exists(), (
        f"Missing paper parity golden {path}. "
        "Run: python -m Tests.parity.paper_execution_harness --write-golden"
    )
    expected = json.loads(path.read_text(encoding="utf-8"))
    actual = capture_paper_native_execution(scenario)
    assert actual == expected, (
        f"Paper-native execution behavior diverged for {scenario.name}.\n"
        "Refresh only after explicit operator approval with:\n"
        "python -m Tests.parity.paper_execution_harness --write-golden\n"
        f"Current capture:\n{stable_json(actual)}"
    )


def test_july_2026_fixture_captures_email_shape() -> None:
    payload = capture_paper_native_execution(
        scenario_by_name("2026_07_07_synthetic_38pos")
    )
    final_output = payload["final_decision_output"]
    rebudget = payload["post_sell_rebudget"]

    assert final_output["sell_count"] == 4
    assert final_output["buy_count"] == 2
    assert [
        intent["symbol"]
        for intent in final_output["intents"]
        if intent["side"] == "SELL"
    ] == ["WBD", "CVS", "MO", "NEE"]
    assert [row["symbol"] for row in rebudget["resized_by_post_sell_rebudget"]] == [
        "GE",
        "PNC",
    ]
    assert len(rebudget["suppressed_by_post_sell_rebudget"]) == 7
    assert float(rebudget["estimated_ending_cash"]) == pytest.approx(939.0)
    post_sell_equity = float(payload["capital_budget"]["post_sell_buy_budget"]["post_sell_equity"])
    assert float(rebudget["estimated_ending_cash"]) / post_sell_equity == pytest.approx(
        0.0879,
        abs=0.0001,
    )


def test_july_2026_real_fixture_matches_artifact() -> None:
    payload = capture_paper_native_execution(scenario_by_name("2026_07_07_real"))
    comparison = payload["artifact_comparison"]
    rebudget = payload["post_sell_rebudget"]

    assert comparison["actual"]["buys"] == comparison["expected"]["buys"]
    assert comparison["actual"]["skipped"] == comparison["expected"]["skipped"]
    assert comparison["actual"]["ending_cash"] == pytest.approx(939.73)
    assert comparison["actual"]["estimated_ending_cash"] == pytest.approx(935.8470412704778)
    assert comparison["actual"]["target_cash_weight"] == pytest.approx(0.0875652909)
    assert comparison["actual"]["post_sell_equity"] == pytest.approx(10687.42)
    assert float(rebudget["ending_cash"]) == pytest.approx(939.73)
    assert float(rebudget["estimated_ending_cash"]) == pytest.approx(935.8470412704778)
    assert float(payload["capital_budget"]["post_sell_buy_budget"]["post_sell_equity"]) == pytest.approx(10687.42)
    assert float(payload["capital_budget"]["post_sell_buy_budget"]["target_cash_weight"]) == pytest.approx(0.0875652909)
    assert [
        order["ticker"]
        for order in rebudget["final_buy_orders_submitted"]
    ] == ["PNC", "GE"]
    assert [
        row["symbol"]
        for row in rebudget["skipped_buy_orders"]
    ] == ["VRTX", "WELL", "ALL", "ABBV", "AVGO", "ELV", "NXPI", "F", "PANW", "GEV"]
