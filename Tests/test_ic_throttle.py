from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from core.ic_throttle import (
    DEFAULT_FLOOR,
    DEFAULT_FLOOR_IC,
    _multiplier_from_ic,
    compute_sleeve_ic_throttle,
)


class MultiplierMathTest(unittest.TestCase):
    def test_positive_ic_returns_one(self) -> None:
        self.assertEqual(_multiplier_from_ic(0.05, floor=0.2, floor_ic=-0.05), 1.0)

    def test_zero_ic_returns_one(self) -> None:
        self.assertEqual(_multiplier_from_ic(0.0, floor=0.2, floor_ic=-0.05), 1.0)

    def test_very_negative_ic_pinned_at_floor(self) -> None:
        self.assertAlmostEqual(
            _multiplier_from_ic(-0.20, floor=0.2, floor_ic=-0.05), 0.2
        )

    def test_linear_interpolation(self) -> None:
        # halfway between 0 and -0.05 should give halfway between 1.0 and 0.2
        m = _multiplier_from_ic(-0.025, floor=0.2, floor_ic=-0.05)
        self.assertAlmostEqual(m, 0.6, places=4)


class ComputeSleeveIcThrottleTest(unittest.TestCase):
    def _write(self, path: Path, rows: list[dict]) -> None:
        pd.DataFrame(rows).to_csv(path, index=False)

    def test_missing_file_returns_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.csv"
            res = compute_sleeve_ic_throttle(
                sleeve="sleeve_quality", ic_rolling_path=missing
            )
            self.assertEqual(res.multiplier, 1.0)
            self.assertIsNone(res.rolling_ic)
            self.assertIn("missing", res.reason)

    def test_all_null_returns_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ic.csv"
            self._write(
                path,
                [
                    {"date": "2026-04-09", "sleeve": "sleeve_quality",
                     "horizon": 21, "window": 60, "rolling_ic": "",
                     "n": 0, "universe_size": 0},
                ],
            )
            res = compute_sleeve_ic_throttle(
                sleeve="sleeve_quality", ic_rolling_path=path
            )
            self.assertEqual(res.multiplier, 1.0)
            self.assertIsNone(res.rolling_ic)

    def test_negative_ic_scales_down(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ic.csv"
            self._write(
                path,
                [
                    {"date": "2026-04-01", "sleeve": "sleeve_quality",
                     "horizon": 21, "window": 60, "rolling_ic": -0.03,
                     "n": 20, "universe_size": 20},
                    {"date": "2026-04-09", "sleeve": "sleeve_quality",
                     "horizon": 21, "window": 60, "rolling_ic": -0.025,
                     "n": 20, "universe_size": 20},
                ],
            )
            res = compute_sleeve_ic_throttle(
                sleeve="sleeve_quality", ic_rolling_path=path
            )
            # latest is -0.025, halfway between 0 and -0.05
            self.assertAlmostEqual(res.multiplier, 0.6, places=3)
            self.assertAlmostEqual(res.rolling_ic, -0.025)
            self.assertEqual(res.horizon, 21)

    def test_positive_ic_is_full_strength(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ic.csv"
            self._write(
                path,
                [
                    {"date": "2026-04-09", "sleeve": "sleeve_quality",
                     "horizon": 21, "window": 60, "rolling_ic": 0.04,
                     "n": 20, "universe_size": 20},
                ],
            )
            res = compute_sleeve_ic_throttle(
                sleeve="sleeve_quality", ic_rolling_path=path
            )
            self.assertEqual(res.multiplier, 1.0)

    def test_horizon_fallback(self) -> None:
        # 21d is absent, should fall back to 10d
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ic.csv"
            self._write(
                path,
                [
                    {"date": "2026-04-09", "sleeve": "sleeve_quality",
                     "horizon": 10, "window": 60, "rolling_ic": -0.05,
                     "n": 20, "universe_size": 20},
                ],
            )
            res = compute_sleeve_ic_throttle(
                sleeve="sleeve_quality", ic_rolling_path=path
            )
            self.assertAlmostEqual(res.multiplier, DEFAULT_FLOOR, places=3)
            self.assertEqual(res.horizon, 10)


if __name__ == "__main__":
    unittest.main()
