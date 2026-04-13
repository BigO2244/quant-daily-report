from __future__ import annotations

import unittest

import pandas as pd

import research.ic_monitor as ic_monitor


class ICMonitorAlertsTest(unittest.TestCase):
    def test_alerts_fire_for_negative_rolling_ic_and_recent_sign_flip(self) -> None:
        dates = pd.bdate_range("2026-04-01", periods=15)
        daily_rows = []
        ic_values = [-0.25] * 12 + [0.18] * 3
        for dt, ic_value in zip(dates, ic_values):
            daily_rows.append(
                {
                    "date": dt.strftime("%Y-%m-%d"),
                    "sleeve": "sleeve_quality",
                    "horizon": 1,
                    "ic": ic_value,
                    "n": 3,
                    "universe_size": 3,
                }
            )
        daily_df = pd.DataFrame(daily_rows)
        rolling_df = pd.DataFrame(
            [
                {
                    "date": dt.strftime("%Y-%m-%d"),
                    "sleeve": "sleeve_quality",
                    "horizon": 1,
                    "window": 20,
                    "rolling_ic": -0.05,
                    "n": 3,
                    "universe_size": 3,
                }
                for dt in dates
            ]
        )

        summary = ic_monitor._build_summary(daily_df, rolling_df, as_of_date="2026-04-21")
        sleeve_alerts = summary["sleeves"]["sleeve_quality"]["alerts"]
        self.assertTrue(any("20d rolling IC has been <= 0 for 15 consecutive days" in alert for alert in sleeve_alerts))
        self.assertTrue(any("sign flipped within last 5 observations" in alert for alert in sleeve_alerts))
        self.assertIn("sleeve_quality: 20d rolling IC has been <= 0 for 15 consecutive days", summary["alerts"])


if __name__ == "__main__":
    unittest.main()
