from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import research.ic_monitor as ic_monitor


def _build_price_frame(signal_dates: list[str]) -> pd.DataFrame:
    dates = pd.bdate_range(min(signal_dates), periods=35)
    score_map = {
        "A1": 0.01,
        "A2": 0.02,
        "A3": 0.03,
        "B1": 0.01,
        "B2": 0.02,
        "B3": 0.03,
    }
    rows = []
    for ticker, score in score_map.items():
        signed_score = score if ticker.startswith("A") else -score
        for step, dt in enumerate(dates):
            close = 100.0 * (1.0 + signed_score * step)
            rows.append(
                {
                    "date": dt,
                    "ticker": ticker,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1_000,
                }
            )
    return pd.DataFrame(rows)


class ICMonitorBackfillTest(unittest.TestCase):
    def test_backfill_rebuilds_daily_ic_from_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cwd = Path(tmp_dir)
            signals_dir = cwd / "signals"
            signals_dir.mkdir(parents=True)
            (cwd / "outputs").mkdir(parents=True)

            snapshot_dates = ["2026-04-01", "2026-04-02", "2026-04-03"]
            for snapshot_date in snapshot_dates:
                payload = {
                    "snapshot_date": snapshot_date,
                    "meta": {
                        "trade_date": snapshot_date,
                        "asof_date": snapshot_date,
                        "generated_at": f"{snapshot_date}T16:00:00Z",
                    },
                    "signals": [
                        {"ticker": "A1", "target_weight": 0.10, "sleeve": "sleeve_A", "raw_score": 0.01},
                        {"ticker": "A2", "target_weight": 0.20, "sleeve": "sleeve_A", "raw_score": 0.02},
                        {"ticker": "A3", "target_weight": 0.30, "sleeve": "sleeve_A", "raw_score": 0.03},
                        {"ticker": "B1", "target_weight": 0.10, "sleeve": "sleeve_B", "raw_score": 0.01},
                        {"ticker": "B2", "target_weight": 0.20, "sleeve": "sleeve_B", "raw_score": 0.02},
                        {"ticker": "B3", "target_weight": 0.30, "sleeve": "sleeve_B", "raw_score": 0.03},
                    ],
                }
                (signals_dir / f"{snapshot_date}.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")

            price_frame = _build_price_frame(snapshot_dates)
            with mock.patch.object(ic_monitor, "download_prices", return_value=price_frame):
                old_cwd = os.getcwd()
                os.chdir(cwd)
                try:
                    summary = ic_monitor.backfill_ic(signals_dir=signals_dir, report_date="2026-05-15")
                finally:
                    os.chdir(old_cwd)

            self.assertEqual(summary["status"], "ok")
            daily = pd.read_csv(cwd / "outputs" / "ic_monitor" / "ic_daily.csv")
            self.assertEqual(len(daily), 24)
            self.assertEqual(sorted(daily["date"].unique().tolist()), snapshot_dates)


if __name__ == "__main__":
    unittest.main()
