"""Tests for write_regime_state_artifact (Phase 3 observability)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import daily_quant_report as dqr  # noqa: E402


class WriteRegimeStateArtifactTests(unittest.TestCase):
    def _call(self, tmp_root: Path, **overrides) -> Path | None:
        kwargs = dict(
            report_date="2026-07-10",
            regime_summary={
                "composite_regime": "risk_on_trending",
                "trend_state": "up",
                "volatility_state": "normal",
                "breadth_state": "healthy",
                "macro_state": "risk_on",
            },
            vix_regime={"vix": 17.2, "regime": "LOW", "max_positions": 6, "position_scale": 1.0},
            regime_strengths={"sleeve_trend": 0.60, "sleeve_2": 0.25, "sleeve_quality": 0.15},
            drift_blends={"sleeve_trend": 1.0, "sleeve_2": 0.5, "sleeve_quality": 0.0},
            sleeve_cash_routes=[],
            final_target_count=6,
            repo_root=tmp_root,
        )
        kwargs.update(overrides)
        return dqr.write_regime_state_artifact(**kwargs)

    def test_writes_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            path = self._call(tmp)
            self.assertIsNotNone(path)
            self.assertTrue(path.exists())
            data = json.loads(path.read_text())
            self.assertEqual(data["report_date"], "2026-07-10")

    def test_output_path_under_workflow_date(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            path = self._call(tmp)
            expected = tmp / "outputs" / "workflow" / "2026-07-10" / "regime_state.json"
            self.assertEqual(path, expected)

    def test_regime_fields_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            path = self._call(tmp)
            data = json.loads(path.read_text())
            self.assertEqual(data["regime"]["composite_regime"], "risk_on_trending")
            self.assertAlmostEqual(data["regime"]["vix_value"], 17.2, places=4)
            self.assertEqual(data["regime"]["vix_regime_label"], "low")
            self.assertEqual(data["concentration"]["n"], 6)

    def test_sleeve_strengths_and_blends_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            path = self._call(tmp)
            data = json.loads(path.read_text())
            self.assertAlmostEqual(data["sleeve_strengths"]["sleeve_trend"], 0.60, places=5)
            self.assertAlmostEqual(data["drift_blends"]["sleeve_trend"], 1.0, places=5)
            self.assertAlmostEqual(data["drift_blends"]["sleeve_2"], 0.5, places=5)
            self.assertAlmostEqual(data["drift_blends"]["sleeve_quality"], 0.0, places=5)

    def test_final_target_count_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            path = self._call(tmp)
            data = json.loads(path.read_text())
            self.assertEqual(data["final_target_count"], 6)

    def test_cash_routes_serialized(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            routes = [{"sleeve_id": "sleeve_trend", "routed_weight": 0.25, "invalid_reason": "nan_equity"}]
            path = self._call(tmp, sleeve_cash_routes=routes)
            data = json.loads(path.read_text())
            self.assertEqual(len(data["cash_routes"]), 1)
            self.assertEqual(data["cash_routes"][0]["sleeve"], "sleeve_trend")
            self.assertAlmostEqual(data["cash_routes"][0]["routed_weight"], 0.25, places=5)

    def test_deployed_sha_from_deploy_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "outputs").mkdir(parents=True, exist_ok=True)
            deploy_state = {"deployed_sha": "abc1234", "deployed_at": "2026-07-10T00:00:00Z", "branch": "main"}
            (tmp / "outputs" / "deploy_state.json").write_text(json.dumps(deploy_state))
            path = self._call(tmp)
            data = json.loads(path.read_text())
            self.assertEqual(data["deployed_sha"], "abc1234")

    def test_deployed_sha_null_when_deploy_state_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            path = self._call(tmp)
            data = json.loads(path.read_text())
            self.assertIsNone(data["deployed_sha"])

    def test_no_changes_key_when_no_prior_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            path = self._call(tmp)
            data = json.loads(path.read_text())
            self.assertNotIn("changes", data)

    def test_changes_list_when_prior_artifact_exists(self) -> None:
        """Write a prior-day artifact then check changes are detected."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # Write a prev-day artifact with different composite_regime
            prev_date = "2026-07-09"
            prev_dir = tmp / "outputs" / "workflow" / prev_date
            prev_dir.mkdir(parents=True, exist_ok=True)
            prev_state = {
                "report_date": prev_date,
                "generated_at": "2026-07-09T12:00:00Z",
                "deployed_sha": None,
                "regime": {
                    "composite_regime": "risk_off_defensive",
                    "vix_regime_label": "high",
                    "vix_value": 28.5,
                    "trend_state": "down",
                    "volatility_state": "elevated",
                    "breadth_state": "weak",
                    "macro_state": "stress",
                },
                "concentration": {"n": 3, "source": "vix_regime:HIGH"},
                "sleeve_strengths": {"sleeve_trend": 0.20, "sleeve_2": 0.40, "sleeve_quality": 0.40},
                "drift_blends": {"sleeve_trend": 1.0, "sleeve_2": 1.0, "sleeve_quality": 1.0},
                "cash_routes": [],
                "final_target_count": 3,
            }
            (prev_dir / "regime_state.json").write_text(json.dumps(prev_state))

            # Write today's artifact (different regime)
            path = self._call(tmp)
            data = json.loads(path.read_text())

            self.assertIn("changes", data)
            changes_str = " ".join(data["changes"])
            # composite_regime changed
            self.assertIn("composite_regime", changes_str)
            # concentration.n changed (3 -> 6)
            self.assertIn("concentration.n", changes_str)

    def test_no_changes_when_prior_matches(self) -> None:
        """When prior artifact matches current state, changes list is empty."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            prev_date = "2026-07-09"
            prev_dir = tmp / "outputs" / "workflow" / prev_date
            prev_dir.mkdir(parents=True, exist_ok=True)
            # Matching prior state
            prev_state = {
                "report_date": prev_date,
                "generated_at": "2026-07-09T12:00:00Z",
                "deployed_sha": None,
                "regime": {
                    "composite_regime": "risk_on_trending",
                    "vix_regime_label": "low",
                    "vix_value": 17.2,
                    "trend_state": "up",
                    "volatility_state": "normal",
                    "breadth_state": "healthy",
                    "macro_state": "risk_on",
                },
                "concentration": {"n": 6, "source": "vix_regime:LOW"},
                "sleeve_strengths": {"sleeve_trend": 0.60, "sleeve_2": 0.25, "sleeve_quality": 0.15},
                "drift_blends": {"sleeve_trend": 1.0, "sleeve_2": 0.5, "sleeve_quality": 0.0},
                "cash_routes": [],
                "final_target_count": 6,
            }
            (prev_dir / "regime_state.json").write_text(json.dumps(prev_state))

            path = self._call(tmp)
            data = json.loads(path.read_text())

            # changes list exists but should be empty (no differences)
            self.assertIn("changes", data)
            self.assertEqual(data["changes"], [])

    def test_does_not_raise_on_none_vix_regime(self) -> None:
        """Graceful handling when vix_regime is None."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            path = self._call(tmp, vix_regime=None)
            self.assertIsNotNone(path)
            data = json.loads(path.read_text())
            self.assertEqual(data["regime"]["vix_value"], 0.0)


if __name__ == "__main__":
    unittest.main()
