from __future__ import annotations

import unittest

import pandas as pd

from sleeves.sleeve_defensive_etf.selection import (
    DEFENSIVE_ETF_TICKERS,
    SLEEVE_NAME,
    build_defensive_etf_equity_curve,
    build_defensive_etf_sleeve_output,
)


def _make_prices() -> pd.DataFrame:
    dates = pd.bdate_range("2026-02-02", periods=40)
    rows = []
    price_templates = {
        "SGOV": 100.0,
        "SHY": 82.0,
        "IEF": 94.0,
        "TLT": 86.0,
    }
    daily_slopes = {
        "SGOV": 0.01,
        "SHY": 0.03,
        "IEF": 0.07,
        "TLT": 0.09,
    }
    for ticker in DEFENSIVE_ETF_TICKERS:
        base = price_templates[ticker]
        slope = daily_slopes[ticker]
        for idx, date in enumerate(dates):
            close = base + idx * slope
            rows.append(
                {
                    "date": date,
                    "ticker": ticker,
                    "open": close,
                    "high": close * 1.001,
                    "low": close * 0.999,
                    "close": close,
                    "volume": 1_000_000 + idx * 1000,
                }
            )
    return pd.DataFrame(rows)


class DefensiveEtfSleeveTests(unittest.TestCase):
    def test_inactive_outside_defensive_regimes(self) -> None:
        output = build_defensive_etf_sleeve_output(
            prices=_make_prices(),
            regime_summary={
                "composite_regime": "risk_on_trending",
                "volatility_state": "normal",
                "macro_state": "risk_on",
            },
        )

        self.assertEqual(output.meta.sleeve_name, SLEEVE_NAME)
        self.assertTrue(output.positions_df.empty)

    def test_builds_positions_in_defensive_regime(self) -> None:
        output = build_defensive_etf_sleeve_output(
            prices=_make_prices(),
            regime_summary={
                "composite_regime": "risk_off_defensive",
                "volatility_state": "elevated",
                "macro_state": "risk_off",
            },
        )

        self.assertFalse(output.positions_df.empty)
        self.assertAlmostEqual(float(output.positions_df["target_weight"].sum()), 1.0, places=9)
        self.assertIn("SGOV", set(output.positions_df["ticker"]))
        self.assertEqual(output.meta.sleeve_name, SLEEVE_NAME)

    def test_builds_equity_curve_from_positions(self) -> None:
        prices = _make_prices()
        output = build_defensive_etf_sleeve_output(
            prices=prices,
            regime_summary={
                "composite_regime": "high_volatility",
                "volatility_state": "crisis",
                "macro_state": "stress",
            },
        )
        equity_df = build_defensive_etf_equity_curve(
            prices=prices,
            sleeve_output=output,
            base_equity=10_000.0,
        )

        self.assertFalse(equity_df.empty)
        self.assertIn("equity", equity_df.columns)
        self.assertGreater(float(equity_df["equity"].iloc[-1]), 0.0)


if __name__ == "__main__":
    unittest.main()
