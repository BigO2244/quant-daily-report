from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.options_overlay_shadow import (
    build_options_overlay_shadow,
    write_options_overlay_shadow,
)


class OptionsOverlayShadowTests(unittest.TestCase):
    def test_risk_on_regime_surfaces_income_and_leap_candidates(self) -> None:
        payload = build_options_overlay_shadow(
            trade_date="2026-04-09",
            asof_date="2026-04-08",
            regime_summary={
                "composite_regime": "risk_on_trending",
                "trend_state": "strong_up",
                "volatility_state": "normal",
                "breadth_state": "healthy",
                "macro_state": "risk_on",
            },
            portfolio_equity=100000.0,
            portfolio_cash=5000.0,
            spy_price=676.01,
        )

        self.assertIn(payload["trigger"]["status"], {"WATCH_ONLY_REVIEW_CANDIDATE", "READY_SHADOW_RECOMMENDATION"})
        self.assertTrue(payload["trigger"]["active"])
        strategies = {item["strategy"] for item in payload["candidate_strategies"]}
        self.assertIn("covered_call", strategies)
        self.assertIn("leap_call", strategies)
        self.assertIn(payload["recommendation"]["strategy"], strategies)

    def test_neutral_elevated_vol_regime_surfaces_butterfly_candidate(self) -> None:
        payload = build_options_overlay_shadow(
            trade_date="2026-04-09",
            asof_date="2026-04-08",
            regime_summary={
                "composite_regime": "neutral_mixed",
                "trend_state": "neutral",
                "volatility_state": "elevated",
                "breadth_state": "mixed",
                "macro_state": "neutral",
            },
            portfolio_equity=100000.0,
            portfolio_cash=5000.0,
            spy_price=676.01,
        )

        strategies = {item["strategy"] for item in payload["candidate_strategies"]}
        self.assertIn("call_butterfly", strategies)
        self.assertIn("covered_call", strategies)
        self.assertIn("leap_call", strategies)

    def test_high_vol_regime_surfaces_straddle_candidate(self) -> None:
        payload = build_options_overlay_shadow(
            trade_date="2026-04-09",
            asof_date="2026-04-08",
            regime_summary={
                "composite_regime": "high_volatility",
                "trend_state": "strong_down",
                "volatility_state": "elevated",
                "breadth_state": "washed_out",
                "macro_state": "stress",
            },
            portfolio_equity=100000.0,
            portfolio_cash=5000.0,
            spy_price=676.01,
        )

        strategies = {item["strategy"] for item in payload["candidate_strategies"]}
        self.assertIn("long_straddle", strategies)

    def test_risk_off_regime_recommends_put_spread_when_account_can_support_it(self) -> None:
        payload = build_options_overlay_shadow(
            trade_date="2026-04-09",
            asof_date="2026-04-08",
            regime_summary={
                "composite_regime": "risk_off_defensive",
                "trend_state": "weak_down",
                "volatility_state": "elevated",
                "breadth_state": "deteriorating",
                "macro_state": "risk_off",
            },
            portfolio_equity=200000.0,
            portfolio_cash=5000.0,
            spy_price=676.01,
        )

        self.assertEqual(payload["trigger"]["status"], "READY_SHADOW_RECOMMENDATION")
        self.assertTrue(payload["trigger"]["active"])
        self.assertEqual(payload["recommendation"]["strategy"], "put_spread")
        self.assertTrue(payload["recommendation"]["feasible"])
        self.assertEqual(payload["recommendation"]["contracts_recommended"], 1)
        self.assertEqual(payload["recommendation"]["expiry"], "2026-05-08")
        self.assertEqual(payload["recommendation"]["long_put"]["kind"], "PUT")
        self.assertEqual(payload["recommendation"]["short_put"]["kind"], "PUT")

    def test_high_cash_book_skips_non_crisis_overlay(self) -> None:
        payload = build_options_overlay_shadow(
            trade_date="2026-04-09",
            asof_date="2026-04-08",
            regime_summary={
                "composite_regime": "risk_off_defensive",
                "trend_state": "weak_down",
                "volatility_state": "elevated",
                "breadth_state": "deteriorating",
                "macro_state": "risk_off",
            },
            portfolio_equity=100000.0,
            portfolio_cash=25000.0,
            spy_price=676.01,
        )

        self.assertEqual(payload["trigger"]["status"], "INACTIVE_ALREADY_DEFENSIVE")
        self.assertFalse(payload["trigger"]["active"])
        self.assertIsNone(payload["recommendation"]["strategy"])

    def test_small_account_crisis_regime_recommends_one_protective_put(self) -> None:
        # Budget-based feasibility: 500bps on $10K = $500 >= min_contract_premium $50 → feasible.
        payload = build_options_overlay_shadow(
            trade_date="2026-04-09",
            asof_date="2026-04-08",
            regime_summary={
                "composite_regime": "high_volatility",
                "trend_state": "strong_down",
                "volatility_state": "crisis",
                "breadth_state": "washed_out",
                "macro_state": "stress",
            },
            portfolio_equity=10000.0,
            portfolio_cash=1000.0,
            spy_price=676.01,
        )

        self.assertEqual(payload["trigger"]["status"], "READY_SHADOW_RECOMMENDATION")
        self.assertTrue(payload["trigger"]["active"])
        self.assertEqual(payload["recommendation"]["strategy"], "protective_put")
        self.assertTrue(payload["recommendation"]["feasible"])
        self.assertEqual(payload["recommendation"]["contracts_recommended"], 1)
        self.assertEqual(payload["recommendation"]["expiry"], "2026-05-15")

    def test_tiny_account_below_premium_floor_stays_watch_only(self) -> None:
        # $800 equity × 500bps = $40 < min_contract_premium $50 → not feasible.
        payload = build_options_overlay_shadow(
            trade_date="2026-04-09",
            asof_date="2026-04-08",
            regime_summary={
                "composite_regime": "high_volatility",
                "trend_state": "strong_down",
                "volatility_state": "crisis",
                "breadth_state": "washed_out",
                "macro_state": "stress",
            },
            portfolio_equity=800.0,
            portfolio_cash=100.0,
            spy_price=676.01,
        )

        self.assertEqual(payload["trigger"]["status"], "WATCH_ONLY_CONTRACT_TOO_LARGE")
        self.assertTrue(payload["trigger"]["active"])
        self.assertEqual(payload["recommendation"]["strategy"], "protective_put")
        self.assertFalse(payload["recommendation"]["feasible"])
        self.assertEqual(payload["recommendation"]["contracts_recommended"], 0)

    def test_writer_persists_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            payload = write_options_overlay_shadow(
                run_root=tmp_path / "outputs" / "runs" / "run-1",
                output_dir=tmp_path / "outputs" / "options_overlay",
                trade_date="2026-04-09",
                asof_date="2026-04-08",
                regime_summary={
                    "composite_regime": "high_volatility",
                    "trend_state": "strong_down",
                    "volatility_state": "crisis",
                    "breadth_state": "washed_out",
                    "macro_state": "stress",
                },
                portfolio_equity=10000.0,
                portfolio_cash=1000.0,
                spy_price=676.01,
            )

            artifact_paths = payload["artifact_paths"]
            self.assertTrue(Path(artifact_paths["run_json"]).exists())
            self.assertTrue(Path(artifact_paths["dated_json"]).exists())
            self.assertTrue(Path(artifact_paths["dated_markdown"]).exists())
            self.assertTrue(Path(artifact_paths["latest_json"]).exists())


if __name__ == "__main__":
    unittest.main()
