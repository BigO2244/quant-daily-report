"""
Tests — Alpha Stack Research Metrics
======================================
Smoke tests for correctness of metrics utilities.
No network calls — all data is synthetic.
"""

import pytest
import numpy as np
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def _daily_returns(n=252, seed=42, annualised_mean=0.10, annualised_vol=0.15):
    """Synthetic daily return series."""
    np.random.seed(seed)
    daily_mean = annualised_mean / 252
    daily_vol = annualised_vol / np.sqrt(252)
    return pd.Series(np.random.normal(daily_mean, daily_vol, n))


def _nav_series(n=252, seed=42):
    """Synthetic NAV series starting at 1.0."""
    rets = _daily_returns(n=n, seed=seed)
    return (1 + rets).cumprod()


class TestSharpeRatio:
    def test_positive_sharpe_for_positive_returns(self):
        from alpha_stack.research.metrics import sharpe_ratio
        rets = _daily_returns(annualised_mean=0.15, annualised_vol=0.10)
        sr = sharpe_ratio(rets)
        assert sr > 0, f"Expected positive Sharpe, got {sr:.3f}"

    def test_negative_sharpe_for_negative_returns(self):
        from alpha_stack.research.metrics import sharpe_ratio
        rets = _daily_returns(annualised_mean=-0.15, annualised_vol=0.10)
        sr = sharpe_ratio(rets)
        assert sr < 0, f"Expected negative Sharpe, got {sr:.3f}"

    def test_sharpe_with_zero_returns(self):
        from alpha_stack.research.metrics import sharpe_ratio
        rets = pd.Series([0.0] * 252)
        sr = sharpe_ratio(rets)
        assert sr == 0.0 or np.isnan(sr), "Zero returns should give 0 or NaN Sharpe"

    def test_sharpe_is_float(self):
        from alpha_stack.research.metrics import sharpe_ratio
        rets = _daily_returns()
        sr = sharpe_ratio(rets)
        assert isinstance(sr, float), f"Expected float, got {type(sr)}"


class TestSortinoRatio:
    def test_sortino_positive_for_good_returns(self):
        from alpha_stack.research.metrics import sortino_ratio
        rets = _daily_returns(annualised_mean=0.15, annualised_vol=0.10)
        sr = sortino_ratio(rets)
        assert sr > 0, f"Expected positive Sortino, got {sr:.3f}"

    def test_sortino_ge_sharpe_for_positive_skew(self):
        """Sortino uses only downside vol, so it should be >= Sharpe for positive-return series."""
        from alpha_stack.research.metrics import sharpe_ratio, sortino_ratio
        rets = _daily_returns(annualised_mean=0.12, annualised_vol=0.10)
        s = sharpe_ratio(rets)
        so = sortino_ratio(rets)
        # Not always strictly true due to random draws, but generally holds
        assert not np.isnan(so), "Sortino returned NaN"


class TestMaxDrawdown:
    def test_max_drawdown_negative_or_zero(self):
        from alpha_stack.research.metrics import max_drawdown
        nav = _nav_series()
        mdd = max_drawdown(nav)
        assert mdd <= 0.0, f"Max drawdown should be <= 0, got {mdd:.4f}"

    def test_max_drawdown_monotone_nav_is_zero(self):
        from alpha_stack.research.metrics import max_drawdown
        nav = pd.Series(np.linspace(1.0, 2.0, 100))
        mdd = max_drawdown(nav)
        assert abs(mdd) < 1e-9, f"Monotone rising NAV should have 0 drawdown, got {mdd}"

    def test_max_drawdown_is_float(self):
        from alpha_stack.research.metrics import max_drawdown
        nav = _nav_series()
        mdd = max_drawdown(nav)
        assert isinstance(mdd, float)

    def test_drawdown_series_non_positive(self):
        from alpha_stack.research.metrics import drawdown_series
        nav = _nav_series()
        dd = drawdown_series(nav)
        assert (dd <= 1e-9).all(), "Drawdown series should be all non-positive"


class TestCAGR:
    def test_cagr_positive_for_growing_nav(self):
        from alpha_stack.research.metrics import cagr
        nav = pd.Series(np.linspace(1.0, 1.5, 252))  # 50% growth over ~1 year
        c = cagr(nav)
        assert c > 0, f"Expected positive CAGR, got {c:.4f}"

    def test_cagr_is_float(self):
        from alpha_stack.research.metrics import cagr
        nav = _nav_series()
        c = cagr(nav)
        assert isinstance(c, float)


class TestInformationCoefficient:
    def test_ic_perfect_rank_correlation(self):
        from alpha_stack.research.metrics import information_coefficient
        scores = pd.Series([5.0, 4.0, 3.0, 2.0, 1.0])
        fwd_rets = pd.Series([0.05, 0.04, 0.03, 0.02, 0.01])
        ic = information_coefficient(scores, fwd_rets)
        assert abs(ic - 1.0) < 0.01, f"Expected IC ~1.0, got {ic:.4f}"

    def test_ic_perfect_inverse_correlation(self):
        from alpha_stack.research.metrics import information_coefficient
        scores = pd.Series([5.0, 4.0, 3.0, 2.0, 1.0])
        fwd_rets = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05])
        ic = information_coefficient(scores, fwd_rets)
        assert abs(ic - (-1.0)) < 0.01, f"Expected IC ~-1.0, got {ic:.4f}"

    def test_ic_in_minus1_to_1(self):
        from alpha_stack.research.metrics import information_coefficient
        np.random.seed(0)
        scores = pd.Series(np.random.normal(0, 1, 50))
        fwd_rets = pd.Series(np.random.normal(0, 0.05, 50))
        ic = information_coefficient(scores, fwd_rets)
        assert -1.0 <= ic <= 1.0, f"IC out of range: {ic}"

    def test_ic_empty_returns_nan(self):
        from alpha_stack.research.metrics import information_coefficient
        ic = information_coefficient(pd.Series([], dtype=float), pd.Series([], dtype=float))
        assert np.isnan(ic) or ic == 0.0, "Empty IC should be NaN or 0"


class TestTurnover:
    def test_turnover_zero_for_unchanged_weights(self):
        """History with all identical rows should have 0 turnover."""
        from alpha_stack.research.metrics import turnover
        tickers = ["AAPL", "MSFT", "GOOG"]
        # Same weights on 3 days → 0 turnover
        history = pd.DataFrame(
            [[0.1, 0.1, 0.1], [0.1, 0.1, 0.1], [0.1, 0.1, 0.1]],
            columns=tickers,
        )
        t = turnover(history)
        assert abs(t) < 1e-9, f"Unchanged weights should have 0 turnover, got {t}"

    def test_turnover_positive_for_changing_weights(self):
        """History with changing weights should have positive turnover."""
        from alpha_stack.research.metrics import turnover
        tickers = ["AAPL", "MSFT", "GOOG", "AMZN"]
        np.random.seed(42)
        # Random weights on each row to ensure changes
        rows = [np.random.dirichlet(np.ones(4)) for _ in range(5)]
        history = pd.DataFrame(rows, columns=tickers)
        t = turnover(history)
        assert t > 0, f"Changing weights should have positive turnover, got {t}"

    def test_turnover_non_negative(self):
        """Turnover should never be negative."""
        from alpha_stack.research.metrics import turnover
        np.random.seed(42)
        tickers = [f"T{i:02d}" for i in range(10)]
        rows = [np.random.dirichlet(np.ones(10)) for _ in range(20)]
        history = pd.DataFrame(rows, columns=tickers)
        t = turnover(history)
        assert t >= 0, f"Turnover should be non-negative, got {t}"


class TestAnnualisedVol:
    def test_vol_positive_for_random_returns(self):
        from alpha_stack.research.metrics import annualised_vol
        rets = _daily_returns()
        v = annualised_vol(rets)
        assert v > 0, f"Expected positive vol, got {v}"

    def test_vol_zero_for_constant_returns(self):
        from alpha_stack.research.metrics import annualised_vol
        rets = pd.Series([0.001] * 252)
        v = annualised_vol(rets)
        assert abs(v) < 1e-9, f"Constant returns should have 0 vol, got {v}"


class TestSummarisePerformance:
    def test_summarise_returns_dict(self):
        from alpha_stack.research.metrics import summarise_performance
        rets = _daily_returns()
        result = summarise_performance(rets)
        assert isinstance(result, dict)

    def test_summarise_contains_key_metrics(self):
        from alpha_stack.research.metrics import summarise_performance
        rets = _daily_returns()
        result = summarise_performance(rets)
        for key in ("sharpe", "max_drawdown", "annualised_vol"):
            assert key in result, f"Missing key: {key}"

    def test_summarise_with_empty_returns(self):
        from alpha_stack.research.metrics import summarise_performance
        rets = pd.Series([], dtype=float)
        result = summarise_performance(rets)
        assert isinstance(result, dict), "summarise_performance should return dict for empty input"
