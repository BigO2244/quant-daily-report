import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import daily_quant_report as dqr  # noqa: E402
from core.portfolio_alloc import create_sleeve_output  # noqa: E402


class ResolveRegimeStrengthsTests(unittest.TestCase):
    def test_maps_live_sleeves_and_renormalizes_without_cash(self) -> None:
        regime_today = {
            "weights": {
                "trend": 0.45,
                "value": 0.20,
                "quality": 0.20,
                "mean_reversion": 0.10,
                "cash": 0.05,
            }
        }

        result = dqr.resolve_regime_strengths(
            regime_today,
            [
                "sleeve_trend",
                "sleeve_2",
                "sleeve_quality",
                "sleeve_mean_reversion",
            ],
        )

        self.assertAlmostEqual(sum(result.values()), 1.0, places=9)
        self.assertAlmostEqual(result["sleeve_trend"], 0.45 / 0.95, places=9)
        self.assertAlmostEqual(result["sleeve_2"], 0.20 / 0.95, places=9)
        self.assertAlmostEqual(result["sleeve_quality"], 0.20 / 0.95, places=9)
        self.assertAlmostEqual(result["sleeve_mean_reversion"], 0.10 / 0.95, places=9)

    def test_ignores_unavailable_sleeves_and_renormalizes_remaining(self) -> None:
        regime_today = {
            "weights": {
                "trend": 0.45,
                "value": 0.20,
                "quality": 0.20,
                "mean_reversion": 0.10,
                "cash": 0.05,
            }
        }

        result = dqr.resolve_regime_strengths(
            regime_today,
            ["sleeve_trend", "sleeve_quality"],
        )

        self.assertEqual(set(result.keys()), {"sleeve_trend", "sleeve_quality"})
        self.assertAlmostEqual(sum(result.values()), 1.0, places=9)
        self.assertAlmostEqual(result["sleeve_trend"], 0.45 / 0.65, places=9)
        self.assertAlmostEqual(result["sleeve_quality"], 0.20 / 0.65, places=9)

    def test_routes_cash_bucket_to_defensive_sleeve_in_defensive_regimes(self) -> None:
        regime_today = {
            "composite_regime": "high_volatility",
            "volatility_state": "crisis",
            "macro_state": "stress",
            "weights": {
                "trend": 0.05,
                "value": 0.20,
                "quality": 0.30,
                "mean_reversion": 0.05,
                "cash": 0.40,
            },
        }

        result = dqr.resolve_regime_strengths(
            regime_today,
            [
                "sleeve_trend",
                "sleeve_2",
                "sleeve_quality",
                "sleeve_mean_reversion",
                dqr.DEFENSIVE_SLEEVE_NAME,
            ],
        )

        self.assertAlmostEqual(sum(result.values()), 1.0, places=9)
        self.assertAlmostEqual(result[dqr.DEFENSIVE_SLEEVE_NAME], 0.40, places=9)
        self.assertAlmostEqual(result["sleeve_trend"], 0.05, places=9)

    def test_does_not_route_cash_bucket_to_defensive_sleeve_in_risk_on(self) -> None:
        regime_today = {
            "composite_regime": "risk_on_trending",
            "volatility_state": "normal",
            "macro_state": "risk_on",
            "weights": {
                "trend": 0.45,
                "value": 0.20,
                "quality": 0.20,
                "mean_reversion": 0.10,
                "cash": 0.05,
            },
        }

        result = dqr.resolve_regime_strengths(
            regime_today,
            [
                "sleeve_trend",
                "sleeve_2",
                "sleeve_quality",
                "sleeve_mean_reversion",
                dqr.DEFENSIVE_SLEEVE_NAME,
            ],
        )

        self.assertNotIn(dqr.DEFENSIVE_SLEEVE_NAME, result)
        self.assertAlmostEqual(sum(result.values()), 1.0, places=9)
        self.assertAlmostEqual(result["sleeve_trend"], 0.45 / 0.95, places=9)

    def test_does_not_route_cash_bucket_to_defensive_sleeve_in_breadth_washout(self) -> None:
        regime_today = {
            "composite_regime": "breadth_washout",
            "volatility_state": "normal",
            "macro_state": "neutral",
            "weights": {
                "trend": 0.20,
                "value": 0.15,
                "quality": 0.20,
                "mean_reversion": 0.40,
                "cash": 0.05,
            },
        }

        result = dqr.resolve_regime_strengths(
            regime_today,
            [
                "sleeve_trend",
                "sleeve_2",
                "sleeve_quality",
                "sleeve_mean_reversion",
                dqr.DEFENSIVE_SLEEVE_NAME,
            ],
        )

        self.assertNotIn(dqr.DEFENSIVE_SLEEVE_NAME, result)
        self.assertAlmostEqual(sum(result.values()), 1.0, places=9)
        self.assertAlmostEqual(result["sleeve_mean_reversion"], 0.40 / 0.95, places=9)

    def test_falls_back_to_equal_weights_when_regime_missing(self) -> None:
        result = dqr.resolve_regime_strengths(
            None,
            ["sleeve_trend", "sleeve_2", "sleeve_quality"],
        )

        self.assertEqual(
            result,
            {
                "sleeve_trend": 1 / 3,
                "sleeve_2": 1 / 3,
                "sleeve_quality": 1 / 3,
            },
        )


class ComputeSleeveDriftTests(unittest.TestCase):
    def test_rebalances_all_when_broker_data_unavailable(self) -> None:
        outputs = [
            create_sleeve_output(
                [{"ticker": "AAPL", "target_weight": 1.0}],
                "sleeve_trend",
                strength=1.0,
            ),
            create_sleeve_output(
                [{"ticker": "IBM", "target_weight": 1.0}],
                "sleeve_2",
                strength=1.0,
            ),
        ]

        result = dqr.compute_sleeve_drift(
            broker_positions=None,
            broker_equity=None,
            sleeve_outputs=outputs,
            regime_strengths={"sleeve_trend": 0.5, "sleeve_2": 0.5},
        )

        self.assertEqual(result, {"sleeve_trend": True, "sleeve_2": True})

    def test_holds_when_live_weights_are_within_threshold(self) -> None:
        outputs = [
            create_sleeve_output(
                [{"ticker": "AAPL", "target_weight": 1.0}],
                "sleeve_trend",
                strength=1.0,
            ),
            create_sleeve_output(
                [{"ticker": "IBM", "target_weight": 1.0}],
                "sleeve_2",
                strength=1.0,
            ),
        ]

        result = dqr.compute_sleeve_drift(
            broker_positions=[
                {"symbol": "AAPL", "market_value": "3000"},
                {"symbol": "IBM", "market_value": "2000"},
            ],
            broker_equity=10_000.0,
            sleeve_outputs=outputs,
            regime_strengths={"sleeve_trend": 0.31, "sleeve_2": 0.19},
        )

        self.assertEqual(result, {"sleeve_trend": False, "sleeve_2": False})

    def test_shared_ticker_market_value_is_split_across_sleeves(self) -> None:
        outputs = [
            create_sleeve_output(
                [{"ticker": "AAPL", "target_weight": 0.75}],
                "sleeve_trend",
                strength=1.0,
            ),
            create_sleeve_output(
                [{"ticker": "AAPL", "target_weight": 0.25}],
                "sleeve_quality",
                strength=1.0,
            ),
        ]

        result = dqr.compute_sleeve_drift(
            broker_positions=[{"symbol": "AAPL", "market_value": "4000"}],
            broker_equity=10_000.0,
            sleeve_outputs=outputs,
            regime_strengths={"sleeve_trend": 0.30, "sleeve_quality": 0.10},
        )

        self.assertEqual(result, {"sleeve_trend": False, "sleeve_quality": False})

    def test_rebalances_when_split_holdings_are_far_from_target(self) -> None:
        outputs = [
            create_sleeve_output(
                [{"ticker": "AAPL", "target_weight": 0.75}],
                "sleeve_trend",
                strength=1.0,
            ),
            create_sleeve_output(
                [{"ticker": "AAPL", "target_weight": 0.25}],
                "sleeve_quality",
                strength=1.0,
            ),
        ]

        result = dqr.compute_sleeve_drift(
            broker_positions=[{"symbol": "AAPL", "market_value": "6000"}],
            broker_equity=10_000.0,
            sleeve_outputs=outputs,
            regime_strengths={"sleeve_trend": 0.30, "sleeve_quality": 0.10},
        )

        self.assertEqual(result, {"sleeve_trend": True, "sleeve_quality": True})


if __name__ == "__main__":
    unittest.main()
