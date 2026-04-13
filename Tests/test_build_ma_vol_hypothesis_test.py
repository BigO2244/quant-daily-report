from __future__ import annotations

import csv
import json
import subprocess
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "research" / "build_ma_vol_hypothesis_test.py"
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python3"


class BuildMaVolHypothesisTest(unittest.TestCase):
    def test_script_writes_expected_artifacts_from_local_price_panel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / "data").mkdir(parents=True)
            (tmp_path / "outputs" / "research").mkdir(parents=True)

            (tmp_path / "data" / "universe.csv").write_text(
                "\n".join(
                    [
                        "ticker,sector",
                        "AAA,Information Technology",
                        "BBB,Financials",
                        "CCC,Industrials",
                        "DDD,Health Care",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            price_path = tmp_path / "prices.csv"
            dates: list[date] = []
            cursor = date(2024, 1, 2)
            while len(dates) < 360:
                if cursor.weekday() < 5:
                    dates.append(cursor)
                cursor += timedelta(days=1)

            rows: list[list[object]] = []
            for index, trade_date in enumerate(dates):
                day = trade_date.isoformat()
                aaa_close = 100.0 + index * 0.35 + (0.15 if index % 7 == 0 else 0.0)
                bbb_close = 160.0 - index * 0.20 + (0.10 if index % 9 == 0 else 0.0)
                ccc_close = 110.0 + ((index % 12) - 6) * 0.4
                ddd_close = 90.0 + index * 0.28 + (((index % 10) - 5) * 0.45)
                spy_close = 400.0 + index * 0.18
                rows.extend(
                    [
                        [day, "AAA", aaa_close - 0.4, aaa_close + 0.6, aaa_close - 0.8, aaa_close, 1_200_000, "Information Technology"],
                        [day, "BBB", bbb_close + 0.4, bbb_close + 0.8, bbb_close - 0.8, bbb_close, 1_100_000, "Financials"],
                        [day, "CCC", ccc_close - 0.3, ccc_close + 0.5, ccc_close - 0.6, ccc_close, 900_000, "Industrials"],
                        [day, "DDD", ddd_close - 0.8, ddd_close + 1.2, ddd_close - 1.4, ddd_close, 1_300_000, "Health Care"],
                        [day, "SPY", spy_close - 0.5, spy_close + 0.7, spy_close - 0.9, spy_close, 5_000_000, "Benchmark"],
                    ]
                )

            with price_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["date", "ticker", "open", "high", "low", "close", "volume", "sector"])
                writer.writerows(rows)

            subprocess.run(
                [
                    str(VENV_PYTHON),
                    str(SCRIPT),
                    "--repo-root",
                    str(tmp_path),
                    "--price-panel",
                    str(price_path),
                    "--start-date",
                    dates[260].isoformat(),
                    "--end-date",
                    dates[-2].isoformat(),
                    "--output-dir",
                    "outputs/research/ma_vol_hypothesis",
                ],
                cwd=tmp_path,
                check=True,
                capture_output=True,
                text=True,
            )

            output_dir = tmp_path / "outputs" / "research" / "ma_vol_hypothesis"
            summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
            report = (output_dir / "report.md").read_text(encoding="utf-8")
            state_csv = (output_dir / "state_summary.csv").read_text(encoding="utf-8")
            strategy_csv = (output_dir / "strategy_comparison.csv").read_text(encoding="utf-8")

            self.assertTrue(summary["data_source"].endswith("/prices.csv"))
            self.assertEqual(summary["ticker_count"], 4)
            self.assertGreater(summary["ma_signal_test"]["ma_uptrend"]["21d"]["mean_return_delta"], 0.0)
            self.assertIn("# MA / Volatility Hypothesis Test", report)
            self.assertIn("ma_uptrend", state_csv)
            self.assertIn("ma_uptrend_equal_weight", strategy_csv)


if __name__ == "__main__":
    unittest.main()
