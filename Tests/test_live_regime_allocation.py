import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import daily_quant_report as dqr  # noqa: E402
from core.portfolio_alloc import create_sleeve_output  # noqa: E402
from core.portfolio_alloc import PortfolioAllocator  # noqa: E402


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

        self.assertAlmostEqual(sum(result.values()), 1.0, places=9)
        # cash bucket NOT routed to defensive in risk-on — defensive sleeve absent
        self.assertNotIn(dqr.DEFENSIVE_SLEEVE_NAME, result)

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


class DriftBlendTests(unittest.TestCase):
    """Unit tests for _drift_blend() helper."""

    def test_below_hold_threshold_returns_zero(self) -> None:
        # drift < 1.5% -> 0.0
        self.assertEqual(dqr._drift_blend(0.00), 0.0)
        self.assertEqual(dqr._drift_blend(0.01), 0.0)
        self.assertEqual(dqr._drift_blend(0.015), 0.0)

    def test_above_full_threshold_returns_one(self) -> None:
        # drift > 4.5% -> 1.0
        self.assertEqual(dqr._drift_blend(0.045), 1.0)
        self.assertEqual(dqr._drift_blend(0.10), 1.0)
        self.assertEqual(dqr._drift_blend(1.00), 1.0)

    def test_midpoint_returns_half(self) -> None:
        # midpoint = (0.015 + 0.045) / 2 = 0.030 -> 0.5
        self.assertAlmostEqual(dqr._drift_blend(0.030), 0.5, places=9)

    def test_linear_interpolation_quarter(self) -> None:
        # 1/4 of the way through [0.015, 0.045] = 0.015 + 0.0075 = 0.0225 -> 0.25
        self.assertAlmostEqual(dqr._drift_blend(0.0225), 0.25, places=9)

    def test_linear_interpolation_three_quarters(self) -> None:
        # 3/4 of the way through = 0.015 + 0.0225 = 0.0375 -> 0.75
        self.assertAlmostEqual(dqr._drift_blend(0.0375), 0.75, places=9)


class ComputeSleeveDriftTests(unittest.TestCase):
    def test_rebalances_all_when_broker_data_unavailable(self) -> None:
        """No broker data fallback: all sleeves return blend=1.0 (full rebalance)."""
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

        # blend=1.0 for all sleeves when broker data unavailable
        self.assertAlmostEqual(result["sleeve_trend"], 1.0, places=9)
        self.assertAlmostEqual(result["sleeve_2"], 1.0, places=9)

    def test_holds_when_live_weights_are_within_threshold(self) -> None:
        """Drift below 1.5%: blend=0.0 (HOLD)."""
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

        # sleeve_trend: current=0.30, target=0.31, drift=0.01 < 1.5% -> blend=0.0
        # sleeve_2:     current=0.20, target=0.19, drift=0.01 < 1.5% -> blend=0.0
        result = dqr.compute_sleeve_drift(
            broker_positions=[
                {"symbol": "AAPL", "market_value": "3000"},
                {"symbol": "IBM", "market_value": "2000"},
            ],
            broker_equity=10_000.0,
            sleeve_outputs=outputs,
            regime_strengths={"sleeve_trend": 0.31, "sleeve_2": 0.19},
        )

        self.assertAlmostEqual(result["sleeve_trend"], 0.0, places=9)
        self.assertAlmostEqual(result["sleeve_2"], 0.0, places=9)

    def test_full_rebalance_when_drift_exceeds_upper_threshold(self) -> None:
        """Drift above 4.5%: blend=1.0 (full rebalance)."""
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

        # sleeve_trend: current=0.30, target=0.60, drift=0.30 > 4.5% -> blend=1.0
        # sleeve_2:     current=0.20, target=0.40, drift=0.20 > 4.5% -> blend=1.0
        result = dqr.compute_sleeve_drift(
            broker_positions=[
                {"symbol": "AAPL", "market_value": "3000"},
                {"symbol": "IBM", "market_value": "2000"},
            ],
            broker_equity=10_000.0,
            sleeve_outputs=outputs,
            regime_strengths={"sleeve_trend": 0.60, "sleeve_2": 0.40},
        )

        self.assertAlmostEqual(result["sleeve_trend"], 1.0, places=9)
        self.assertAlmostEqual(result["sleeve_2"], 1.0, places=9)

    def test_partial_blend_in_middle_zone(self) -> None:
        """Drift in (1.5%, 4.5%) zone: blend is linear between 0 and 1."""
        outputs = [
            create_sleeve_output(
                [{"ticker": "AAPL", "target_weight": 1.0}],
                "sleeve_trend",
                strength=1.0,
            ),
        ]

        # current=0.30, target=0.33, drift=0.03 exactly at midpoint [0.015..0.045]
        # -> blend = (0.03 - 0.015) / 0.030 = 0.5
        result = dqr.compute_sleeve_drift(
            broker_positions=[{"symbol": "AAPL", "market_value": "3000"}],
            broker_equity=10_000.0,
            sleeve_outputs=outputs,
            regime_strengths={"sleeve_trend": 0.33},
        )

        self.assertAlmostEqual(result["sleeve_trend"], 0.5, places=6)

    def test_shared_ticker_market_value_is_split_across_sleeves(self) -> None:
        """Shared ticker MV split: small drift -> blend=0.0 for both sleeves."""
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

        self.assertAlmostEqual(result["sleeve_trend"], 0.0, places=9)
        self.assertAlmostEqual(result["sleeve_quality"], 0.0, places=9)

    def test_rebalances_when_split_holdings_are_far_from_target(self) -> None:
        """Shared ticker far from target: blend=1.0 for both sleeves."""
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

        self.assertAlmostEqual(result["sleeve_trend"], 1.0, places=9)
        self.assertAlmostEqual(result["sleeve_quality"], 1.0, places=9)

    def test_hold_blends_preserve_regime_strengths_in_allocator(self) -> None:
        """blend=0.0 (HOLD) with prior=regime target preserves target strength."""
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
        regime_strengths = {"sleeve_trend": 0.60, "sleeve_2": 0.40}

        # First, set the sleeves to the regime target so prior = target
        dqr.apply_regime_strengths_to_sleeves(outputs, regime_strengths, None)

        # Now small drift -> blend=0.0: strengths should remain at target
        drift_blends = dqr.compute_sleeve_drift(
            broker_positions=[
                {"symbol": "AAPL", "market_value": "5900"},
                {"symbol": "IBM", "market_value": "4100"},
            ],
            broker_equity=10_000.0,
            sleeve_outputs=outputs,
            regime_strengths=regime_strengths,
        )

        # drift < 1.5% on both -> blend=0.0
        self.assertAlmostEqual(drift_blends["sleeve_trend"], 0.0, places=9)
        self.assertAlmostEqual(drift_blends["sleeve_2"], 0.0, places=9)

        dqr.apply_regime_strengths_to_sleeves(outputs, regime_strengths, drift_blends)

        result = PortfolioAllocator(max_position_pct=1.0, min_gross_exposure=1.0).allocate(outputs)

        # blend=0.0 means prior is kept; prior was already set to regime target,
        # so allocations remain at regime target
        self.assertAlmostEqual(
            result.sleeve_allocations.get("sleeve_trend", 0.0),
            0.60,
            places=9,
        )
        self.assertAlmostEqual(
            result.sleeve_allocations.get("sleeve_2", 0.0),
            0.40,
            places=9,
        )

    def test_full_blend_applies_regime_target_from_arbitrary_prior(self) -> None:
        """blend=1.0 (full rebalance) updates strength to regime target."""
        outputs = [
            create_sleeve_output(
                [{"ticker": "AAPL", "target_weight": 1.0}],
                "sleeve_trend",
                strength=0.20,  # prior far from target
            ),
            create_sleeve_output(
                [{"ticker": "IBM", "target_weight": 1.0}],
                "sleeve_2",
                strength=0.80,  # prior far from target
            ),
        ]
        regime_strengths = {"sleeve_trend": 0.60, "sleeve_2": 0.40}
        drift_blends = {"sleeve_trend": 1.0, "sleeve_2": 1.0}

        dqr.apply_regime_strengths_to_sleeves(outputs, regime_strengths, drift_blends)

        result = PortfolioAllocator(max_position_pct=1.0, min_gross_exposure=1.0).allocate(outputs)

        self.assertAlmostEqual(
            result.sleeve_allocations.get("sleeve_trend", 0.0),
            0.60,
            places=9,
        )
        self.assertAlmostEqual(
            result.sleeve_allocations.get("sleeve_2", 0.0),
            0.40,
            places=9,
        )

    def test_partial_blend_still_anchors_to_regime_target(self) -> None:
        """Blends are observability-only: meta.strength at call time is the
        construction-time base, not a genuine prior-day strength, so any
        blend value must still anchor the sleeve to the regime target."""
        outputs = [
            create_sleeve_output(
                [{"ticker": "AAPL", "target_weight": 1.0}],
                "sleeve_trend",
                strength=0.4,  # stale construction-time base, not a real prior
            ),
        ]
        regime_strengths = {"sleeve_trend": 0.8}
        drift_blends = {"sleeve_trend": 0.5}

        dqr.apply_regime_strengths_to_sleeves(outputs, regime_strengths, drift_blends)

        self.assertAlmostEqual(outputs[0].meta.strength, 0.8, places=9)

    def test_hold_blend_with_base_strengths_does_not_renormalize_to_50_50(self) -> None:
        """Regression: two active sleeves at construction-time base strength 1.0
        with blend=0.0 (HOLD) must end up at their regime-target strengths.
        Blending against the stale 1.0/1.0 bases previously let the allocator
        renormalize them into an unintended 50/50 split."""
        outputs = [
            create_sleeve_output(
                [{"ticker": "AAPL", "target_weight": 1.0}],
                "sleeve_trend",
                strength=1.0,  # construction-time base
            ),
            create_sleeve_output(
                [{"ticker": "IBM", "target_weight": 1.0}],
                "sleeve_2",
                strength=1.0,  # construction-time base
            ),
        ]
        regime_strengths = {"sleeve_trend": 0.60, "sleeve_2": 0.40}
        drift_blends = {"sleeve_trend": 0.0, "sleeve_2": 0.0}

        dqr.apply_regime_strengths_to_sleeves(outputs, regime_strengths, drift_blends)

        self.assertAlmostEqual(outputs[0].meta.strength, 0.60, places=9)
        self.assertAlmostEqual(outputs[1].meta.strength, 0.40, places=9)

        result = PortfolioAllocator(max_position_pct=1.0, min_gross_exposure=1.0).allocate(outputs)

        self.assertAlmostEqual(
            result.sleeve_allocations.get("sleeve_trend", 0.0),
            0.60,
            places=9,
        )
        self.assertAlmostEqual(
            result.sleeve_allocations.get("sleeve_2", 0.0),
            0.40,
            places=9,
        )


if __name__ == "__main__":
    unittest.main()
