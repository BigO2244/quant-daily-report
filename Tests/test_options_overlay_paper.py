from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.options_overlay_paper import (
    build_options_overlay_paper_review,
    write_options_overlay_paper_review,
)


class OptionsOverlayPaperTests(unittest.TestCase):
    def test_risk_on_surfaces_review_candidates_but_not_paper_ready_without_covered_inventory(self) -> None:
        payload = build_options_overlay_paper_review(
            trade_date="2026-04-10",
            asof_date="2026-04-09",
            regime_summary={
                "composite_regime": "risk_on_trending",
                "trend_state": "strong_up",
                "volatility_state": "normal",
                "breadth_state": "healthy",
                "macro_state": "risk_on",
            },
            portfolio_equity=100000.0,
            portfolio_cash=5000.0,
            spy_price=680.0,
            live_regime_review={"promotion_gate": {"overall_status": "ready"}},
        )

        self.assertEqual(payload["paper_review_status"], "WATCH_ONLY_CONTRACT_TOO_LARGE")
        self.assertFalse(payload["paper_ready"])
        strategies = {item["strategy"] for item in payload["candidate_strategies"]}
        self.assertIn("covered_call", strategies)
        self.assertIn("leap_call", strategies)

    def test_shadow_ready_becomes_paper_ready(self) -> None:
        payload = build_options_overlay_paper_review(
            trade_date="2026-04-10",
            asof_date="2026-04-09",
            regime_summary={
                "composite_regime": "risk_off_defensive",
                "trend_state": "weak_down",
                "volatility_state": "elevated",
                "breadth_state": "deteriorating",
                "macro_state": "risk_off",
            },
            portfolio_equity=200000.0,
            portfolio_cash=5000.0,
            spy_price=680.0,
            live_regime_review={"promotion_gate": {"overall_status": "ready"}},
        )

        self.assertEqual(payload["shadow"]["trigger"]["status"], "READY_SHADOW_RECOMMENDATION")
        self.assertEqual(payload["paper_review_status"], "READY_FOR_PAPER_REVIEW")
        self.assertTrue(payload["paper_ready"])
        self.assertEqual(payload["paper_plan"]["strategy"], "put_spread")
        # 200bps on $200K = $400 budget; floor($400/$75)=5, capped at max_contracts=3 → 3
        self.assertEqual(payload["paper_plan"]["contracts_recommended"], 3)
        self.assertEqual(payload["paper_plan"]["roll_before_dte"], 14)

    def test_small_account_crisis_regime_promotes_to_paper_ready_with_directional_sizing(self) -> None:
        # 500bps on $10K = $500; floor(500/150)=3 contracts, capped at max_contracts=5 → 3.
        payload = build_options_overlay_paper_review(
            trade_date="2026-04-10",
            asof_date="2026-04-09",
            regime_summary={
                "composite_regime": "high_volatility",
                "trend_state": "strong_down",
                "volatility_state": "crisis",
                "breadth_state": "washed_out",
                "macro_state": "stress",
            },
            portfolio_equity=10000.0,
            portfolio_cash=1000.0,
            spy_price=680.0,
            live_regime_review={"promotion_gate": {"overall_status": "ready"}},
        )

        self.assertEqual(payload["paper_review_status"], "READY_FOR_PAPER_REVIEW")
        self.assertTrue(payload["paper_ready"])
        self.assertEqual(payload["paper_plan"]["strategy"], "protective_put")
        self.assertEqual(payload["paper_plan"]["contracts_recommended"], 3)

    def test_writer_persists_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            payload = write_options_overlay_paper_review(
                run_root=tmp_path / "outputs" / "runs" / "run-1",
                output_dir=tmp_path / "outputs" / "options_overlay_paper",
                trade_date="2026-04-10",
                asof_date="2026-04-09",
                regime_summary={
                    "composite_regime": "risk_off_defensive",
                    "trend_state": "weak_down",
                    "volatility_state": "elevated",
                    "breadth_state": "deteriorating",
                    "macro_state": "risk_off",
                },
                portfolio_equity=200000.0,
                portfolio_cash=5000.0,
                spy_price=680.0,
                live_regime_review={"promotion_gate": {"overall_status": "ready"}},
            )

            artifact_paths = payload["artifact_paths"]
            self.assertTrue(Path(artifact_paths["run_json"]).exists())
            self.assertTrue(Path(artifact_paths["dated_json"]).exists())
            self.assertTrue(Path(artifact_paths["dated_markdown"]).exists())
            self.assertTrue(Path(artifact_paths["latest_json"]).exists())


if __name__ == "__main__":
    unittest.main()
