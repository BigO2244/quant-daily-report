from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.live_regime_review import (
    build_live_regime_review,
    build_returns_by_ticker,
    compute_live_sleeve_weights,
    write_live_regime_review,
)
from core.portfolio_alloc import create_sleeve_output


class LiveRegimeReviewTests(unittest.TestCase):
    def test_returns_builder_uses_prev_close_rows(self) -> None:
        import pandas as pd

        prev_px = pd.DataFrame(
            [
                {"ticker": "AAA", "prev_close": 100.0},
                {"ticker": "BBB", "prev_close": 50.0},
            ]
        )
        curr_px = pd.DataFrame(
            [
                {"ticker": "AAA", "prev_close": 110.0},
                {"ticker": "BBB", "prev_close": 45.0},
            ]
        )

        returns_by_ticker = build_returns_by_ticker(prev_px, curr_px)
        self.assertAlmostEqual(returns_by_ticker["AAA"], 0.10, places=9)
        self.assertAlmostEqual(returns_by_ticker["BBB"], -0.10, places=9)

    def test_review_splits_overlap_and_scores_promotion_gate(self) -> None:
        trend = create_sleeve_output(
            [
                {"ticker": "AAA", "target_weight": 0.75},
                {"ticker": "BBB", "target_weight": 0.25},
            ],
            "sleeve_trend",
            strength=0.30,
        )
        quality = create_sleeve_output(
            [{"ticker": "AAA", "target_weight": 1.0}],
            "sleeve_quality",
            strength=0.10,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            signal_path = tmp_path / "signals" / "2026-04-09.json"
            signal_path.parent.mkdir(parents=True, exist_ok=True)
            signal_path.write_text('{"signals":[]}\n', encoding="utf-8")

            payload = build_live_regime_review(
                trade_date="2026-04-09",
                asof_date="2026-04-08",
                regime_summary={
                    "composite_regime": "risk_on_trending",
                    "trend_state": "strong_up",
                    "volatility_state": "normal",
                    "breadth_state": "healthy",
                    "macro_state": "risk_on",
                    "target_weights": {"trend": 0.45, "quality": 0.20},
                },
                regime_strengths={"sleeve_trend": 0.30, "sleeve_quality": 0.10},
                drift_flags={"sleeve_trend": True, "sleeve_quality": False},
                sleeve_outputs=[trend, quality],
                final_target_allocations={"sleeve_trend": 0.30, "sleeve_quality": 0.10},
                broker_positions=[
                    {"symbol": "AAA", "market_value": "400"},
                    {"symbol": "BBB", "market_value": "200"},
                ],
                broker_equity=1000.0,
                returns_by_ticker={"AAA": 0.10, "BBB": -0.10},
                alpha_attribution={
                    "ok": True,
                    "overlap_days": 25,
                    "summary": {"cumulative_alpha": -0.02},
                },
                signal_snapshot_path=signal_path,
                objective_contract_path=Path("config") / "engine_objectives.json",
            )

            live_weights = payload["shadow_vs_live"]["live_broker_weights"]
            self.assertAlmostEqual(live_weights["sleeve_trend"], 0.4769230769, places=6)
            self.assertAlmostEqual(live_weights["sleeve_quality"], 0.1230769230, places=6)

            overlap = payload["overlap_diagnostics"]
            self.assertEqual(overlap["overlapping_ticker_count"], 1)
            self.assertAlmostEqual(overlap["overlapped_target_weight"], 0.325, places=6)
            self.assertEqual(overlap["overlapping_tickers"][0]["ticker"], "AAA")

            sleeves = {row["sleeve_name"]: row for row in payload["sleeves"]}
            self.assertAlmostEqual(
                sleeves["sleeve_trend"]["daily_contribution_target_book"],
                0.015,
                places=6,
            )
            self.assertAlmostEqual(
                sleeves["sleeve_quality"]["daily_contribution_target_book"],
                0.01,
                places=6,
            )

            promotion_gate = payload["promotion_gate"]
            self.assertEqual(promotion_gate["overall_status"], "watch")
            checks = {check["name"]: check for check in promotion_gate["checks"]}
            self.assertEqual(checks["regime_data_available"]["status"], "pass")
            self.assertEqual(checks["multi_sleeve_participation"]["status"], "pass")
            self.assertEqual(checks["signal_snapshot_present"]["status"], "pass")
            self.assertEqual(checks["shadow_vs_live_alignment"]["status"], "warn")
            self.assertEqual(checks["benchmark_relative_alpha"]["status"], "fail")
            self.assertEqual(checks["cash_discipline"]["status"], "fail")

    def test_missing_signal_snapshot_is_blocking_failure(self) -> None:
        trend = create_sleeve_output(
            [{"ticker": "AAA", "target_weight": 1.0}],
            "sleeve_trend",
            strength=1.0,
        )

        payload = build_live_regime_review(
            trade_date="2026-04-09",
            asof_date="2026-04-08",
            regime_summary={"composite_regime": "risk_on_trending"},
            regime_strengths={"sleeve_trend": 1.0},
            drift_flags={"sleeve_trend": True},
            sleeve_outputs=[trend],
            final_target_allocations={"sleeve_trend": 1.0},
            broker_positions=[{"symbol": "AAA", "market_value": "1000"}],
            broker_equity=1000.0,
            returns_by_ticker={"AAA": 0.02},
            alpha_attribution={"ok": False, "overlap_days": 0, "summary": {}},
            signal_snapshot_path="signals/missing.json",
            objective_contract_path=Path("config") / "engine_objectives.json",
        )

        self.assertEqual(payload["promotion_gate"]["overall_status"], "not_ready")
        self.assertIn("signal_snapshot_present", payload["promotion_gate"]["blockers"])
        self.assertIn("multi_sleeve_participation", payload["promotion_gate"]["blockers"])

    def test_writer_persists_json_and_markdown(self) -> None:
        trend = create_sleeve_output(
            [{"ticker": "AAA", "target_weight": 1.0}],
            "sleeve_trend",
            strength=1.0,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            signal_path = tmp_path / "signals" / "2026-04-09.json"
            signal_path.parent.mkdir(parents=True, exist_ok=True)
            signal_path.write_text('{"signals":[]}\n', encoding="utf-8")

            payload = write_live_regime_review(
                run_root=tmp_path / "outputs" / "runs" / "run-1",
                output_dir=tmp_path / "outputs" / "engine_review",
                trade_date="2026-04-09",
                asof_date="2026-04-08",
                regime_summary={"composite_regime": "risk_on_trending"},
                regime_strengths={"sleeve_trend": 1.0},
                drift_flags={"sleeve_trend": True},
                sleeve_outputs=[trend],
                final_target_allocations={"sleeve_trend": 1.0},
                broker_positions=[{"symbol": "AAA", "market_value": "1000"}],
                broker_equity=1000.0,
                returns_by_ticker={"AAA": 0.02},
                alpha_attribution={"ok": True, "overlap_days": 30, "summary": {"cumulative_alpha": 0.01}},
                signal_snapshot_path=signal_path,
                objective_contract_path=Path("config") / "engine_objectives.json",
            )

            artifact_paths = payload["artifact_paths"]
            self.assertTrue(Path(artifact_paths["run_json"]).exists())
            self.assertTrue(Path(artifact_paths["dated_json"]).exists())
            self.assertTrue(Path(artifact_paths["dated_markdown"]).exists())
            self.assertTrue(Path(artifact_paths["latest_json"]).exists())


class LiveSleeveWeightTests(unittest.TestCase):
    def test_compute_live_sleeve_weights_splits_shared_ticker_using_target_budgets(self) -> None:
        trend = create_sleeve_output(
            [{"ticker": "AAA", "target_weight": 0.75}],
            "sleeve_trend",
            strength=0.30,
        )
        quality = create_sleeve_output(
            [{"ticker": "AAA", "target_weight": 1.0}],
            "sleeve_quality",
            strength=0.10,
        )

        weights = compute_live_sleeve_weights(
            broker_positions=[{"symbol": "AAA", "market_value": "400"}],
            broker_equity=1000.0,
            sleeve_outputs=[trend, quality],
            sleeve_budgets={"sleeve_trend": 0.30, "sleeve_quality": 0.10},
        )

        self.assertAlmostEqual(weights["sleeve_trend"], 0.30, places=6)
        self.assertAlmostEqual(weights["sleeve_quality"], 0.10, places=6)


if __name__ == "__main__":
    unittest.main()
