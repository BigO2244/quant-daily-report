"""
test_regime.py
--------------
Unit and integration tests for the regime pipeline.

Run with:  pytest regime/test_regime.py -v

Tests are designed to run without a FRED API key or live data.
All market data is synthetic.
"""

import numpy as np
import pandas as pd
import pytest

from regime.regime_config import REGIME_WEIGHTS, WEIGHT_COLS, TRANSITION
from regime.regime_indicators import build_indicators, _compute_breadth
from regime.regime_classifier import classify, _classify_composite
from regime.regime_allocator import RegimeAllocator


# ---------------------------------------------------------------------------
# Fixtures — synthetic price data
# ---------------------------------------------------------------------------

def make_spy_prices(n=500, trend="up") -> pd.DataFrame:
    """Synthetic SPY prices with a configurable trend."""
    dates = pd.bdate_range("2020-01-01", periods=n)
    if trend == "up":
        prices = 300 * np.cumprod(1 + np.random.normal(0.0004, 0.01, n))
    elif trend == "down":
        prices = 300 * np.cumprod(1 + np.random.normal(-0.0006, 0.015, n))
    else:  # flat
        prices = 300 * np.cumprod(1 + np.random.normal(0.0, 0.008, n))
    return pd.DataFrame({"date": dates, "close": prices})


def make_vix_prices(n=500, level="normal") -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-01", periods=n)
    if level == "calm":
        vix = np.random.uniform(10, 15, n)
    elif level == "crisis":
        vix = np.random.uniform(45, 60, n)
    else:
        vix = np.random.uniform(15, 22, n)
    return pd.DataFrame({"date": dates, "close": vix})


def make_universe_prices(n=500, n_tickers=50) -> pd.DataFrame:
    """Synthetic universe with half the tickers in uptrend."""
    dates = pd.bdate_range("2020-01-01", periods=n)
    rows = []
    for i in range(n_tickers):
        drift = 0.0004 if i < n_tickers // 2 else -0.0002
        prices = 50 * np.cumprod(1 + np.random.normal(drift, 0.012, n))
        for d, p in zip(dates, prices):
            rows.append({"date": d, "ticker": f"T{i:03d}", "close": p})
    return pd.DataFrame(rows)


def make_fred_data(n=500, regime="normal") -> pd.DataFrame:
    """Synthetic FRED macro data."""
    dates = pd.bdate_range("2020-01-01", periods=n)
    if regime == "risk_on":
        curve = np.random.uniform(1.2, 2.0, n)
        hy    = np.random.uniform(250, 350, n)
    elif regime == "stress":
        curve = np.random.uniform(-1.0, -0.2, n)
        hy    = np.random.uniform(700, 900, n)
    else:
        curve = np.random.uniform(0.2, 0.8, n)
        hy    = np.random.uniform(350, 500, n)
    return pd.DataFrame(
        {"yield_curve_2s10s": curve, "hy_oas": hy},
        index=pd.DatetimeIndex(dates),
    )


# ---------------------------------------------------------------------------
# Tests: build_indicators
# ---------------------------------------------------------------------------

class TestBuildIndicators:
    def test_output_has_expected_columns(self):
        spy = make_spy_prices()
        vix = make_vix_prices()
        uni = make_universe_prices()
        fred = make_fred_data()

        ind = build_indicators(spy, vix, uni, fred)

        expected_raw = [
            "spy_vs_50d", "spy_vs_200d", "ma50_vs_ma200", "spy_ret_63d",
            "vix", "vix_vs_ma63", "vix_spike_10d",
            "pct_above_200d", "pct_above_50d",
            "yield_curve_2s10s", "hy_oas",
        ]
        expected_ewm = [f"{c}_ewm" for c in expected_raw if c != "vix_spike_10d"]

        for col in expected_raw + expected_ewm:
            assert col in ind.columns, f"Missing column: {col}"

    def test_index_is_datetime(self):
        ind = build_indicators(
            make_spy_prices(), make_vix_prices(), make_universe_prices(), make_fred_data()
        )
        assert isinstance(ind.index, pd.DatetimeIndex)

    def test_pct_above_200d_bounded(self):
        ind = build_indicators(
            make_spy_prices(), make_vix_prices(), make_universe_prices(), make_fred_data()
        )
        valid = ind["pct_above_200d"].dropna()
        assert (valid >= 0).all() and (valid <= 1).all()

    def test_ewm_smooths_vix(self):
        """EWM values should be smoother (lower std) than raw values."""
        ind = build_indicators(
            make_spy_prices(), make_vix_prices(), make_universe_prices(), make_fred_data()
        )
        raw_std = ind["vix"].dropna().std()
        ewm_std = ind["vix_ewm"].dropna().std()
        assert ewm_std < raw_std, "EWM should reduce variance"


# ---------------------------------------------------------------------------
# Tests: regime_classifier
# ---------------------------------------------------------------------------

class TestClassifier:
    def _get_states(self, spy_trend="up", vix_level="normal", macro="normal"):
        spy = make_spy_prices(trend=spy_trend)
        vix = make_vix_prices(level=vix_level)
        uni = make_universe_prices()
        fred = make_fred_data(regime=macro)
        ind = build_indicators(spy, vix, uni, fred)
        return classify(ind)

    def test_output_columns(self):
        states = self._get_states()
        expected = [
            "trend_state", "volatility_state", "breadth_state",
            "macro_state", "composite_regime"
        ]
        for col in expected:
            assert col in states.columns

    def test_crisis_vix_triggers_high_volatility(self):
        """Crisis VIX should dominate composite regime."""
        states = self._get_states(vix_level="crisis")
        # Allow for burn-in period (first 63 rows may have NaN-driven defaults)
        tail = states.iloc[100:]
        pct_high_vol = (tail["composite_regime"] == "high_volatility").mean()
        assert pct_high_vol > 0.80, f"Expected >80% high_volatility, got {pct_high_vol:.1%}"

    def test_calm_uptrend_risk_on(self):
        """Calm VIX + uptrend + risk-on macro should produce risk_on_trending."""
        np.random.seed(42)
        states = self._get_states(spy_trend="up", vix_level="calm", macro="risk_on")
        tail = states.iloc[250:]  # well past burn-in
        pct_risk_on = (tail["composite_regime"] == "risk_on_trending").mean()
        assert pct_risk_on > 0.40, f"Expected >40% risk_on_trending, got {pct_risk_on:.1%}"

    def test_no_null_composite_states(self):
        """Composite regime should never be null."""
        states = self._get_states()
        assert states["composite_regime"].isna().sum() == 0

    def test_all_states_are_valid_labels(self):
        from regime.regime_classifier import (
            TREND_STATES, VOLATILITY_STATES, BREADTH_STATES,
            MACRO_STATES, COMPOSITE_REGIMES
        )
        states = self._get_states()
        assert set(states["trend_state"].unique()).issubset(set(TREND_STATES))
        assert set(states["volatility_state"].unique()).issubset(set(VOLATILITY_STATES))
        assert set(states["breadth_state"].unique()).issubset(set(BREADTH_STATES))
        assert set(states["macro_state"].unique()).issubset(set(MACRO_STATES))
        assert set(states["composite_regime"].unique()).issubset(set(COMPOSITE_REGIMES))

    def test_slow_deterioration_bundle_maps_to_risk_off(self):
        states = pd.DataFrame(
            {
                "trend_state": ["neutral"],
                "volatility_state": ["elevated"],
                "breadth_state": ["mixed"],
                "macro_state": ["neutral"],
            }
        )
        out = _classify_composite(states)
        assert out.iloc[0] == "risk_off_defensive"

    def test_constructive_uptrend_not_forced_to_risk_off(self):
        states = pd.DataFrame(
            {
                "trend_state": ["weak_up"],
                "volatility_state": ["elevated"],
                "breadth_state": ["healthy"],
                "macro_state": ["risk_on"],
            }
        )
        out = _classify_composite(states)
        assert out.iloc[0] == "neutral_mixed"


# ---------------------------------------------------------------------------
# Tests: regime_allocator
# ---------------------------------------------------------------------------

class TestAllocator:
    def _make_regime_series(self, n=100, label="risk_on_trending") -> pd.Series:
        dates = pd.bdate_range("2023-01-01", periods=n)
        return pd.Series([label] * n, index=dates, name="composite_regime")

    def test_weights_sum_to_one(self):
        alloc = RegimeAllocator()
        regime = self._make_regime_series()
        weights = alloc.compute_weights(regime)
        weight_cols = [c for c in WEIGHT_COLS if c in weights.columns]
        row_sums = weights[weight_cols].sum(axis=1)
        assert (row_sums - 1.0).abs().max() < 1e-6, "Weights must sum to 1.0"

    def test_weights_non_negative(self):
        alloc = RegimeAllocator()
        regime = self._make_regime_series()
        weights = alloc.compute_weights(regime)
        weight_cols = [c for c in WEIGHT_COLS if c in weights.columns]
        assert (weights[weight_cols] >= 0).all().all()

    def test_transition_smoothing_reduces_jumps(self):
        """
        When regime switches from risk_on to risk_off, smoothed weights
        should not jump immediately — the change should be gradual.
        """
        dates = pd.bdate_range("2023-01-01", periods=60)
        labels = ["risk_on_trending"] * 30 + ["risk_off_defensive"] * 30
        regime = pd.Series(labels, index=dates)

        alloc = RegimeAllocator(blend_alpha=0.2)
        weights = alloc.compute_weights(regime)

        # Trend weight on day of switch (day 30) should not be at target immediately
        trend_day30 = weights["trend"].iloc[30]
        trend_target_risk_off = REGIME_WEIGHTS["risk_off_defensive"][0] / 100.0
        trend_target_risk_on  = REGIME_WEIGHTS["risk_on_trending"][0] / 100.0

        # Should be between the two targets (transition in progress)
        assert trend_target_risk_off < trend_day30 < trend_target_risk_on

    def test_unknown_regime_defaults_to_neutral(self):
        """Unknown regime labels should not crash — default to neutral_mixed."""
        dates = pd.bdate_range("2023-01-01", periods=20)
        regime = pd.Series(["totally_fake_regime"] * 20, index=dates)
        alloc = RegimeAllocator()
        weights = alloc.compute_weights(regime)  # should not raise
        assert len(weights) == 20

    def test_target_weights_table_sums_to_one(self):
        alloc = RegimeAllocator()
        table = alloc.target_weights_table()
        row_sums = table[WEIGHT_COLS].sum(axis=1)
        assert (row_sums - 1.0).abs().max() < 1e-6

    def test_regime_changed_column(self):
        dates = pd.bdate_range("2023-01-01", periods=40)
        labels = ["risk_on_trending"] * 20 + ["neutral_mixed"] * 20
        regime = pd.Series(labels, index=dates)
        alloc = RegimeAllocator()
        weights = alloc.compute_weights(regime)
        assert weights["regime_changed"].iloc[0] == False   # first row has no prior
        assert weights["regime_changed"].iloc[20] == True    # switch day
        assert weights["regime_changed"].iloc[21] == False   # after switch

    def test_first_row_not_marked_as_transition_for_constant_series(self):
        regime = self._make_regime_series(n=5, label="neutral_mixed")
        alloc = RegimeAllocator()
        weights = alloc.compute_weights(regime)
        assert weights["regime_changed"].tolist() == [False, False, False, False, False]


# ---------------------------------------------------------------------------
# Integration test: full pipeline
# ---------------------------------------------------------------------------

class TestFullPipeline:
    def test_end_to_end(self):
        """Run the complete pipeline from raw prices to daily weights."""
        np.random.seed(0)
        spy  = make_spy_prices(n=400, trend="up")
        vix  = make_vix_prices(n=400, level="normal")
        uni  = make_universe_prices(n=400, n_tickers=30)
        fred = make_fred_data(n=400, regime="normal")

        indicators = build_indicators(spy, vix, uni, fred)
        states     = classify(indicators)
        alloc      = RegimeAllocator()
        weights    = alloc.compute_weights(states["composite_regime"])

        # Weights exist for every classified day
        assert len(weights) == len(states)

        # Weights sum to 1.0 everywhere
        weight_cols = [c for c in WEIGHT_COLS if c in weights.columns]
        row_sums = weights[weight_cols].sum(axis=1)
        assert (row_sums - 1.0).abs().max() < 1e-6

        # Composite regime column is present in weights output
        assert "composite_regime" in weights.columns
        assert "regime_changed" in weights.columns
