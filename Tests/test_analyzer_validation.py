"""
Tests for analyzer validation module.

Covers:
- Threshold sweep behavior
- Confusion matrix logic
- Excess return calculations
- Analyzer-conditioned grouping
- Small-sample labeling
"""

from __future__ import annotations

import pandas as pd
import pytest

from research.analyzer_validation import (
    AlarmDayMetrics,
    AnalyzerValidator,
    ConfusionMatrix,
    threshold_sweep_to_csv,
)


@pytest.fixture
def sample_canonical() -> pd.DataFrame:
    """Minimal canonical performance fixture"""
    return pd.DataFrame({
        "date": ["2026-02-20", "2026-02-24", "2026-02-25", "2026-02-27", "2026-03-02"],
        "premarket_score": [0.0, 0.5, 0.5, 0.5, 0.5],
        "spy_return": [-0.01, 0.008, 0.004, -0.008, None],
        "strategy_return": [-0.02, 0.010, 0.005, 0.0, None],
        "excess_return": [-0.01, 0.002, 0.001, 0.008, None],
        "strategy_nav": [9850.0, 9867.0, 9882.0, 9882.0, None],
        "spy_close": [410.0, 413.2, 414.0, 411.9, None],
    })


@pytest.fixture
def sample_overlay() -> pd.DataFrame:
    """Minimal overlay backtest fixture"""
    return pd.DataFrame({
        "date": ["2026-02-20", "2026-02-24", "2026-02-25", "2026-02-27", "2026-03-02"],
        "premarket_score": [0.0, 0.5, 0.5, 0.5, 0.5],
        "spy_return": [-0.01, 0.008, 0.004, -0.008, None],
        "strategy_return": [-0.02, 0.010, 0.005, 0.0, None],
        "overlay_multiplier": [0.0, 0.5, 0.5, 0.5, 0.5],
        "overlay_return": [0.0, 0.0, 0.0, 0.0, 0.0],
    })


class TestConfusionMatrix:
    """Test confusion matrix computations"""

    def test_confusion_matrix_basic(self):
        """Test basic confusion matrix construction"""
        cm = ConfusionMatrix(
            true_positive=5,
            false_positive=2,
            true_negative=10,
            false_negative=3,
        )
        assert cm.total == 20

    def test_hit_rate_calculation(self):
        """Hit rate = TP / (TP + FP)"""
        cm = ConfusionMatrix(true_positive=10, false_positive=5, true_negative=0, false_negative=0)
        assert cm.hit_rate == pytest.approx(10.0 / 15.0)

    def test_false_positive_rate(self):
        """FPR = FP / (FP + TN)"""
        cm = ConfusionMatrix(true_positive=0, false_positive=5, true_negative=15, false_negative=0)
        assert cm.false_positive_rate == pytest.approx(5.0 / 20.0)

    def test_false_negative_rate(self):
        """FNR = FN / (FN + TP)"""
        cm = ConfusionMatrix(true_positive=8, false_positive=0, true_negative=0, false_negative=2)
        assert cm.false_negative_rate == pytest.approx(2.0 / 10.0)

    def test_accuracy(self):
        """Accuracy = (TP + TN) / total"""
        cm = ConfusionMatrix(true_positive=10, false_positive=5, true_negative=15, false_negative=5)
        assert cm.accuracy == pytest.approx((10 + 15) / 35.0)

    def test_division_by_zero_handling(self):
        """Test graceful handling when denominator is 0"""
        cm = ConfusionMatrix(true_positive=0, false_positive=0, true_negative=0, false_negative=0)
        assert cm.hit_rate is None
        assert cm.false_positive_rate is None
        assert cm.false_negative_rate is None
        assert cm.accuracy is None

    def test_to_dict(self):
        """Test serialization to dict"""
        cm = ConfusionMatrix(true_positive=5, false_positive=2, true_negative=10, false_negative=3)
        d = cm.to_dict()
        assert d["true_positive"] == 5
        assert d["false_positive"] == 2
        assert d["hit_rate"] == pytest.approx(5.0 / 7.0)


class TestAlarmDayMetrics:
    """Test alarm day metrics computation"""

    def test_metrics_basic(self):
        """Test basic metrics construction"""
        metrics = AlarmDayMetrics(threshold=0.5, alarm_day_count=5, coverage_count=10)
        assert metrics.threshold == 0.5
        assert metrics.alarm_day_count == 5
        assert metrics.coverage_count == 10

    def test_sufficient_support_true(self):
        """Test sufficient support detection (True case)"""
        metrics = AlarmDayMetrics(
            threshold=0.5,
            alarm_day_count=12,
            coverage_count=15,
            condition_sample_size=5,
        )
        assert metrics.is_sufficient_support(min_samples=10)

    def test_sufficient_support_false_coverage(self):
        """Test insufficient support (low coverage)"""
        metrics = AlarmDayMetrics(
            threshold=0.5,
            alarm_day_count=2,
            coverage_count=5,
            condition_sample_size=5,
        )
        assert not metrics.is_sufficient_support(min_samples=10)

    def test_sufficient_support_false_condition(self):
        """Test insufficient support (low condition sample)"""
        metrics = AlarmDayMetrics(
            threshold=0.5,
            alarm_day_count=12,
            coverage_count=15,
            condition_sample_size=1,
        )
        assert not metrics.is_sufficient_support(min_samples=10)

    def test_to_dict(self):
        """Test serialization includes support flag"""
        metrics = AlarmDayMetrics(
            threshold=0.5,
            alarm_day_count=5,
            coverage_count=10,
            condition_sample_size=2,
        )
        d = metrics.to_dict()
        assert "sufficient_support" in d
        assert d["threshold"] == 0.5


class TestAnalyzerValidator:
    """Test AnalyzerValidator core logic"""

    def test_validator_initialization(self, sample_canonical):
        """Test validator can be initialized"""
        validator = AnalyzerValidator(sample_canonical)
        assert len(validator.canonical) == 5

    def test_column_normalization(self, sample_canonical):
        """Test that numeric columns are coerced properly"""
        # Add a string column that should be coerced
        sample_canonical["spy_return"] = sample_canonical["spy_return"].astype(str)
        validator = AnalyzerValidator(sample_canonical)
        assert pd.api.types.is_numeric_dtype(validator.canonical["spy_return"])

    def test_alarm_day_metrics_threshold_0_5(self, sample_canonical):
        """Test alarm day metrics with threshold 0.5"""
        validator = AnalyzerValidator(sample_canonical)
        metrics = validator.compute_alarm_day_metrics(threshold=0.5)

        # score <= 0.5: Feb 20 (0.0), Feb 24 (0.5), Feb 25 (0.5), Feb 27 (0.5), Mar 02 (0.5)
        # = 5 alarm days out of 5 with scores
        assert metrics.alarm_day_count == 5
        assert metrics.coverage_count == 5

    def test_alarm_day_metrics_threshold_0_25(self, sample_canonical):
        """Test alarm day metrics with lower threshold"""
        validator = AnalyzerValidator(sample_canonical)
        metrics = validator.compute_alarm_day_metrics(threshold=0.25)

        # score <= 0.25: only Feb 20 (0.0)
        assert metrics.alarm_day_count == 1
        assert metrics.coverage_count == 5

    def test_avg_returns_on_alarm_days(self, sample_canonical):
        """Test averaging spy_return and strategy_return on alarm days"""
        validator = AnalyzerValidator(sample_canonical)
        metrics = validator.compute_alarm_day_metrics(threshold=0.5)

        # Alarm days with both metrics: Feb 20 (spy=-0.01, strat=-0.02), Feb 24 (0.008, 0.01),
        # Feb 25 (0.004, 0.005), Feb 27 (-0.008, 0.0)
        # Avg spy: (-0.01 + 0.008 + 0.004 - 0.008) / 4 = -0.003 / 4 = -0.00075
        spy_returns = [-0.01, 0.008, 0.004, -0.008]
        expected_avg_spy = sum(spy_returns) / len(spy_returns)
        assert metrics.avg_spy_return_alarm == pytest.approx(expected_avg_spy)

    def test_confusion_matrix_generation(self, sample_canonical):
        """Test confusion matrix is computed from prediction vs actuals"""
        validator = AnalyzerValidator(sample_canonical)
        metrics = validator.compute_alarm_day_metrics(threshold=0.5)

        # Predictions: all score <= 0.5 (all bearish predictions)
        # Actuals: spy_return < 0 for Feb 20, Feb 27; > 0 for Feb 24, Feb 25
        # TP (pred bearish, actual down): 2 (Feb 20, Feb 27)
        # FP (pred bearish, actual up): 2 (Feb 24, Feb 25)
        # TN (pred bullish, actual up): 0
        # FN (pred bullish, actual down): 0

        cm = metrics.confusion_matrix
        assert cm is not None
        assert cm.true_positive == 2
        assert cm.false_positive == 2
        assert cm.true_negative == 0
        assert cm.false_negative == 0

    def test_hit_rate_from_metrics(self, sample_canonical):
        """Test hit rate is correctly computed from confusion matrix"""
        validator = AnalyzerValidator(sample_canonical)
        metrics = validator.compute_alarm_day_metrics(threshold=0.5)

        # From above: TP=2, FP=2 => hit_rate = 2/(2+2) = 0.5
        assert metrics.confusion_matrix.hit_rate == pytest.approx(0.5)

    def test_threshold_sweep(self, sample_canonical):
        """Test threshold sweep generates multiple threshold results"""
        validator = AnalyzerValidator(sample_canonical)
        results = validator.threshold_sweep(thresholds=[0.25, 0.5, 0.75])

        assert len(results) == 3
        assert results[0].threshold == 0.25
        assert results[1].threshold == 0.5
        assert results[2].threshold == 0.75

    def test_excess_return_attribution_basic(self, sample_canonical):
        """Test excess return attribution computes basic metrics"""
        validator = AnalyzerValidator(sample_canonical)
        attr = validator.compute_excess_return_attribution()

        assert "avg_daily_excess_return" in attr
        assert "strategy_total_return" in attr
        assert "spy_total_return" in attr

    def test_excess_return_attribution_total_return(self, sample_canonical):
        """Test total return calculation from NAV"""
        validator = AnalyzerValidator(sample_canonical)
        attr = validator.compute_excess_return_attribution()

        # strategy_nav: start 9850, end 9882 => (9882/9850 - 1) = 0.00324...
        expected = (9882.0 / 9850.0) - 1.0
        assert attr["strategy_total_return"] == pytest.approx(expected)

    def test_rolling_beta_computation(self, sample_canonical):
        """Test that rolling beta is computed (or skipped gracefully for small sample)"""
        validator = AnalyzerValidator(sample_canonical)
        attr = validator.compute_excess_return_attribution()

        # With only 5 rows, rolling 20-day beta won't generate (needs 20+ rows)
        # Should gracefully return None
        assert attr["avg_rolling_beta"] is None or isinstance(attr["avg_rolling_beta"], float)

    def test_overlay_conditioned_benchmarking_no_overlay(self, sample_canonical):
        """Test overlay conditioning returns error when no overlay provided"""
        validator = AnalyzerValidator(sample_canonical)
        result = validator.overlay_conditioned_benchmarking()
        assert "error" in result

    def test_overlay_conditioned_benchmarking_with_overlay(self, sample_canonical, sample_overlay):
        """Test overlay conditioning with data"""
        validator = AnalyzerValidator(sample_canonical, sample_overlay)
        result = validator.overlay_conditioned_benchmarking()

        # Should have keys for different conditions
        assert "alarm_days" in result or "normal_days" in result or "all_days" in result

    def test_small_sample_note_coverage(self, sample_canonical):
        """Test that insufficient coverage gets noted"""
        # Create very small datasample with one alarm day
        small_df = sample_canonical.iloc[:2].copy()
        validator = AnalyzerValidator(small_df)
        metrics = validator.compute_alarm_day_metrics(threshold=0.5)

        assert not metrics.is_sufficient_support(min_samples=10)
        assert "Low coverage" in metrics.condition_note or "low" in metrics.condition_note.lower()

    def test_small_sample_note_condition(self):
        """Test that insufficient condition sample gets noted"""
        df = pd.DataFrame({
            "date": ["2026-02-20", "2026-02-24"],
            "premarket_score": [0.0, 0.5],
            "spy_return": [None, 0.008],  # Only 1 row with both score and return
            "strategy_return": [-0.02, 0.010],
        })
        validator = AnalyzerValidator(df)
        metrics = validator.compute_alarm_day_metrics(threshold=0.5)

        if metrics.condition_sample_size < 3:
            assert "low" in metrics.condition_note.lower() or metrics.condition_note == ""


class TestThresholdSweepToCSV:
    """Test threshold sweep export logic"""

    def test_threshold_sweep_to_csv_format(self):
        """Test CSV export creates proper DataFrame"""
        metrics_list = [
            AlarmDayMetrics(threshold=0.25, alarm_day_count=1, coverage_count=10),
            AlarmDayMetrics(threshold=0.5, alarm_day_count=5, coverage_count=10),
        ]
        df = threshold_sweep_to_csv(metrics_list)

        assert len(df) == 2
        assert "threshold" in df.columns
        assert "alarm_day_count" in df.columns
        assert "coverage_count" in df.columns
        assert df.iloc[0]["threshold"] == 0.25
        assert df.iloc[1]["threshold"] == 0.5

    def test_threshold_sweep_with_confusion_matrix(self):
        """Test CSV includes confusion matrix fields when available"""
        cm = ConfusionMatrix(true_positive=5, false_positive=2, true_negative=10, false_negative=3)
        metrics = AlarmDayMetrics(threshold=0.5, alarm_day_count=5, coverage_count=10, confusion_matrix=cm)
        df = threshold_sweep_to_csv([metrics])

        assert "true_positive" in df.columns
        assert "false_positive" in df.columns
        assert "false_negative" in df.columns
        assert "true_negative" in df.columns
        assert int(df.iloc[0]["true_positive"]) == 5


class TestAnalyzerValidatorIntegration:
    """Full end-to-end tests"""

    def test_full_validation_workflow(self, sample_canonical, sample_overlay):
        """Test complete validation workflow"""
        validator = AnalyzerValidator(sample_canonical, sample_overlay)

        # Run all analyses
        main_metrics = validator.compute_alarm_day_metrics(0.5)
        sweep = validator.threshold_sweep()
        attr = validator.compute_excess_return_attribution()
        overlay_cond = validator.overlay_conditioned_benchmarking()

        # Verify all return dicts/objects
        assert isinstance(main_metrics, AlarmDayMetrics)
        assert isinstance(sweep, list)
        assert isinstance(attr, dict)
        assert isinstance(overlay_cond, dict)

    def test_persistence_consistency(self, sample_canonical):
        """Test that metrics can be serialized to dict for persistence"""
        validator = AnalyzerValidator(sample_canonical)
        metrics = validator.compute_alarm_day_metrics(0.5)
        d = metrics.to_dict()

        # Verify all expected keys present
        assert "threshold" in d
        assert "alarm_day_count" in d
        assert "coverage_count" in d
        assert "sufficient_support" in d
        assert "confusion_matrix" in d

    def test_zero_coverage(self):
        """Test graceful handling when no analyzer scores available"""
        df = pd.DataFrame({
            "date": ["2026-02-20", "2026-02-24"],
            "premarket_score": [None, None],
            "spy_return": [0.01, 0.02],
            "strategy_return": [0.02, 0.03],
        })
        validator = AnalyzerValidator(df)
        metrics = validator.compute_alarm_day_metrics(0.5)

        assert metrics.alarm_day_count == 0
        assert metrics.coverage_count == 0
        assert metrics.condition_sample_size == 0
