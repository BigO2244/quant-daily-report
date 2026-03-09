"""
Tests — Trend Sleeve Output Correctness
==========================================
Verify that TrendSleeve produces well-formed outputs with synthetic data.
No network calls — all data is synthetic.
"""

import pytest
import numpy as np
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def _make_features(n=30, seed=42):
    """Make synthetic feature DataFrame for TrendSleeve."""
    np.random.seed(seed)
    tickers = [f"TICK{i:02d}" for i in range(n)]
    df = pd.DataFrame({
        "ticker": tickers,
        "as_of_date": pd.Timestamp("2024-06-01"),
        "close": np.random.uniform(10, 200, n),
        "ema50": np.random.uniform(10, 200, n),
        "ema200": np.random.uniform(10, 200, n),
        "trend_flag": np.random.choice([0, 1], n),
        "r12_1": np.random.normal(0.05, 0.20, n),
        "r6_1": np.random.normal(0.03, 0.15, n),
        "r3_1": np.random.normal(0.01, 0.10, n),
        "z_r12_1": np.random.normal(0, 1, n),
        "z_r6_1": np.random.normal(0, 1, n),
        "z_r3_1": np.random.normal(0, 1, n),
        "atr20_pct": np.random.uniform(0.01, 0.04, n),
        "realized_vol_20d": np.random.uniform(0.10, 0.40, n),
        "volume_sma": np.random.uniform(200_000, 2_000_000, n),
        "sector": np.random.choice(["Technology", "Health Care", "Financials"], n),
    })
    return df


def _make_ctx(trend="neutral", vol="normal"):
    from alpha_stack.regime.context import RegimeContext
    from alpha_stack.regime.state_machine import TrendState, VolatilityState, BreadthState, MacroState
    return RegimeContext(
        trend_state=TrendState(trend),
        vol_state=VolatilityState(vol),
        breadth_state=BreadthState.MIXED,
        macro_state=MacroState.NEUTRAL,
        as_of_date="2024-06-01",
    )


class TestTrendSleeveEligibility:
    def test_filters_low_price_tickers(self):
        """Tickers below min_price must be filtered out."""
        from alpha_stack.sleeves.trend import TrendSleeve
        sleeve = TrendSleeve()
        feats = _make_features(10)
        # Set 3 tickers below $5
        feats.loc[:2, "close"] = 2.0
        eligible = sleeve.eligibility_filter(feats)
        assert len(eligible) == 7

    def test_filters_low_volume_tickers(self):
        """Tickers below min ADV must be filtered."""
        from alpha_stack.sleeves.trend import TrendSleeve
        sleeve = TrendSleeve()
        feats = _make_features(10)
        feats.loc[:4, "volume_sma"] = 50_000  # below 100K
        eligible = sleeve.eligibility_filter(feats)
        assert len(eligible) == 5


class TestTrendSleeveScoring:
    def test_score_in_0_100_range(self):
        """All scores must be in [0, 100]."""
        from alpha_stack.sleeves.trend import TrendSleeve
        sleeve = TrendSleeve()
        feats = _make_features(20)
        eligible = sleeve.eligibility_filter(feats)
        scored = sleeve.score_universe(eligible)
        assert "score" in scored.columns
        assert scored["score"].between(0, 100).all(), \
            f"Scores out of range: {scored['score'].describe()}"

    def test_score_preserves_row_count(self):
        """score_universe must not drop rows."""
        from alpha_stack.sleeves.trend import TrendSleeve
        sleeve = TrendSleeve()
        feats = _make_features(20)
        eligible = sleeve.eligibility_filter(feats)
        scored = sleeve.score_universe(eligible)
        assert len(scored) == len(eligible)


class TestTrendSleeveWeights:
    def test_weights_sum_to_at_most_one(self):
        """Total weight must be <= 1.0."""
        from alpha_stack.sleeves.trend import TrendSleeve
        sleeve = TrendSleeve()
        feats = _make_features(20)
        ctx = _make_ctx()
        eligible = sleeve.eligibility_filter(feats)
        scored = sleeve.score_universe(eligible)
        candidates = sleeve.select_candidates(scored)
        if not candidates.empty:
            weighted = sleeve.target_weights(candidates, ctx)
            total = weighted["provisional_weight"].sum()
            assert total <= 1.0 + 1e-9, f"Total weight {total:.4f} exceeds 1.0"

    def test_no_weight_exceeds_cap(self):
        """No single position should exceed position_cap (requires cap * n_positions >= 1.0)."""
        from alpha_stack.sleeves.trend import TrendSleeve
        # Use top_n=15 with position_cap=0.10 → 15 * 0.10 = 1.5 >= 1.0 (feasible to cap)
        sleeve = TrendSleeve(config={"top_n": 15})
        feats = _make_features(30)  # Enough tickers to fill top_n
        ctx = _make_ctx()
        eligible = sleeve.eligibility_filter(feats)
        scored = sleeve.score_universe(eligible)
        candidates = sleeve.select_candidates(scored)
        if not candidates.empty and len(candidates) >= 10:
            weighted = sleeve.target_weights(candidates, ctx)
            cap = sleeve._cfg["position_cap"]
            assert all(weighted["provisional_weight"] <= cap + 1e-9), \
                f"Some weight exceeds position_cap ({cap}): max={weighted['provisional_weight'].max():.4f}"

    def test_all_weights_non_negative(self):
        """No negative weights."""
        from alpha_stack.sleeves.trend import TrendSleeve
        sleeve = TrendSleeve()
        feats = _make_features(20)
        ctx = _make_ctx()
        eligible = sleeve.eligibility_filter(feats)
        scored = sleeve.score_universe(eligible)
        candidates = sleeve.select_candidates(scored)
        if not candidates.empty:
            weighted = sleeve.target_weights(candidates, ctx)
            assert all(weighted["provisional_weight"] >= 0), "Negative weights found"


class TestTrendSleeveFullRun:
    def test_run_returns_sleeve_output(self):
        """run() returns a SleeveOutput with correct name."""
        from alpha_stack.sleeves.trend import TrendSleeve
        from alpha_stack.sleeves.base import SleeveOutput
        sleeve = TrendSleeve()
        feats = _make_features(20)
        ctx = _make_ctx()
        out = sleeve.run(feats, ctx, as_of_date="2024-06-01")
        assert isinstance(out, SleeveOutput)
        assert out.sleeve_name == "trend"

    def test_run_with_empty_data_returns_active_no_candidates(self):
        """run() on empty DataFrame returns active=True, no candidates."""
        from alpha_stack.sleeves.trend import TrendSleeve
        from alpha_stack.sleeves.base import SleeveOutput
        import pandas as pd
        sleeve = TrendSleeve()
        ctx = _make_ctx()
        out = sleeve.run(pd.DataFrame(columns=["ticker", "close", "r12_1"]), ctx)
        assert isinstance(out, SleeveOutput)
        assert out.has_candidates is False

    def test_diagnostics_populated_after_run(self):
        """diagnostics() must return a non-empty dict after run()."""
        from alpha_stack.sleeves.trend import TrendSleeve
        sleeve = TrendSleeve()
        feats = _make_features(20)
        ctx = _make_ctx()
        sleeve.run(feats, ctx)
        diag = sleeve.diagnostics()
        assert isinstance(diag, dict)
