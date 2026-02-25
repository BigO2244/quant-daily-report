# Tests/test_selection.py
"""
Tests for sleeves.sleeve_trend.selection — the cross-sectional scoring engine.

Run: python -m pytest Tests/test_selection.py -v
"""

import numpy as np
import pandas as pd
import pytest


def _make_signals(n_tickers: int = 30, n_days: int = 100) -> pd.DataFrame:
    """Build synthetic signals DataFrame matching prepare_data() output."""
    np.random.seed(42)
    tickers = [f"TKR{i:02d}" for i in range(n_tickers)]
    dates = pd.bdate_range("2025-06-01", periods=n_days)
    sectors = ["Tech", "Health", "Finance", "Energy", "Consumer"]

    rows = []
    for ticker in tickers:
        sector = sectors[hash(ticker) % len(sectors)]
        base_price = np.random.uniform(20, 500)
        # Random walk prices
        rets = np.random.normal(0.0005, 0.02, n_days)
        prices = base_price * np.cumprod(1 + rets)

        for i, date in enumerate(dates):
            close = prices[i]
            ema_fast = prices[max(0, i - 5):i + 1].mean()
            ema_slow = prices[max(0, i - 20):i + 1].mean()
            ema_trend = prices[max(0, i - 50):i + 1].mean()

            rows.append({
                "date": date,
                "ticker": ticker,
                "open": close * 0.999,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": np.random.randint(100_000, 5_000_000),
                "atr": close * 0.02,
                "adx": np.random.uniform(10, 50),
                "ema_fast": ema_fast,
                "ema_slow": ema_slow,
                "ema_trend": ema_trend,
                "above_trend": close > ema_trend,
                "volume_sma": np.random.uniform(200_000, 3_000_000),
                "volume_ratio": np.random.uniform(0.5, 2.5),
                "daily_return": rets[i],
                "sector": sector,
                "passes_liquidity": True,
                "golden_cross": False,
                "death_cross": False,
                "signal_long": False,
                "signal_short": False,
                "final_signal": np.random.uniform(30, 90),
            })

    return pd.DataFrame(rows)


class TestSelection:

    def test_basic_select_and_weight(self):
        from sleeves.sleeve_trend.selection import select_and_weight

        signals = _make_signals()
        result = select_and_weight(signals, top_n=5)

        assert not result.empty, "Should select at least some stocks"
        assert len(result) <= 5, f"Should select at most 5, got {len(result)}"
        assert "target_weight" in result.columns
        assert "score" in result.columns
        assert "sleeve" in result.columns
        assert (result["sleeve"] == "sleeve_trend").all()

    def test_weights_sum_to_one(self):
        from sleeves.sleeve_trend.selection import select_and_weight

        signals = _make_signals()
        result = select_and_weight(signals, top_n=10)

        if not result.empty:
            total = result["target_weight"].sum()
            assert abs(total - 1.0) < 1e-4, f"Weights should sum to 1.0, got {total}"

    def test_weights_are_differentiated(self):
        """Key test: weights should NOT be equal (unless only 1 stock)."""
        from sleeves.sleeve_trend.selection import select_and_weight

        signals = _make_signals(n_tickers=50)
        result = select_and_weight(signals, top_n=10, weight_method="inverse_vol")

        if len(result) > 1:
            weights = result["target_weight"].values
            assert not np.allclose(weights, weights[0]), \
                "Inverse-vol weights should be differentiated, not equal"

    def test_no_weight_exceeds_cap(self):
        from sleeves.sleeve_trend.selection import select_and_weight, MAX_SINGLE_WEIGHT

        signals = _make_signals()
        result = select_and_weight(signals, top_n=10)

        if not result.empty:
            max_w = result["target_weight"].max()
            # Allow small float tolerance
            assert max_w <= MAX_SINGLE_WEIGHT + 1e-6, \
                f"Max weight {max_w} exceeds cap {MAX_SINGLE_WEIGHT}"

    def test_scores_are_ranked(self):
        from sleeves.sleeve_trend.selection import select_and_weight

        signals = _make_signals()
        result = select_and_weight(signals, top_n=10)

        if len(result) > 1:
            scores = result["score"].values
            # Should be sorted descending
            assert all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1)), \
                "Results should be sorted by score descending"

    def test_sector_cap_applied(self):
        from sleeves.sleeve_trend.selection import select_and_weight, MAX_PER_SECTOR

        signals = _make_signals(n_tickers=50)
        result = select_and_weight(signals, top_n=15)

        if not result.empty and "sector" in result.columns:
            sector_counts = result["sector"].value_counts()
            over = sector_counts[sector_counts > MAX_PER_SECTOR]
            assert over.empty, f"Sectors exceed cap: {over.to_dict()}"

    def test_empty_signals(self):
        from sleeves.sleeve_trend.selection import select_and_weight

        empty = pd.DataFrame(columns=[
            "date", "ticker", "close", "adx", "ema_fast", "ema_slow",
            "ema_trend", "above_trend", "volume_sma", "volume_ratio",
            "daily_return", "sector", "passes_liquidity",
        ])
        result = select_and_weight(empty)
        assert result.empty

    def test_all_below_trend_returns_empty(self):
        """If nothing is above 200 EMA, selection should be empty."""
        from sleeves.sleeve_trend.selection import select_and_weight

        signals = _make_signals()
        signals["above_trend"] = False  # Force everything below trend
        result = select_and_weight(signals)
        assert result.empty, "Should select nothing when all below 200 EMA"

    def test_equal_weight_method(self):
        from sleeves.sleeve_trend.selection import select_and_weight

        signals = _make_signals()
        result = select_and_weight(signals, top_n=5, weight_method="equal")

        if not result.empty:
            weights = result["target_weight"].values
            expected = 1.0 / len(result)
            assert np.allclose(weights, expected, atol=0.01), \
                f"Equal weights should be ~{expected}, got {weights}"

    def test_score_weight_method(self):
        from sleeves.sleeve_trend.selection import select_and_weight

        signals = _make_signals()
        result = select_and_weight(signals, top_n=5, weight_method="score")

        if len(result) > 1:
            # Higher score should get higher weight
            assert result.iloc[0]["target_weight"] >= result.iloc[-1]["target_weight"], \
                "Score-weighted: top scorer should have highest weight"


class TestBuildSleeveOutput:

    def test_integration(self):
        from sleeves.sleeve_trend.build_sleeve_output import build_trend_sleeve_output

        signals = _make_signals()
        output = build_trend_sleeve_output(signals)

        assert output.meta.sleeve_name == "sleeve_trend"
        assert output.meta.is_active or output.positions_df.empty
        if output.meta.is_active:
            assert not output.positions_df.empty
            total = output.positions_df["target_weight"].sum()
            assert abs(total - 1.0) < 1e-4

    def test_none_signals(self):
        from sleeves.sleeve_trend.build_sleeve_output import build_trend_sleeve_output

        output = build_trend_sleeve_output(None)
        assert not output.meta.is_active
        assert output.positions_df.empty
