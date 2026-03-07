# Analyzer Validation Methodology

## Overview

This document describes the empirical validation framework for the premarket analyzer signal. The analyzer provides a daily risk posture score (0.0-1.0) that combines multiple risk dimensions into a single bearish/bullish classification.

## Analyzer Contract

### Input Data
The analyzer uses three existing repo components to derive a deterministic risk score:

1. **VIX Regime** (`research/vix_regime.py`)
   - Classifies volatility environment (LOW, ELEVATED, HIGH, CRISIS)
   - Provides `position_scale` [0.25-1.0] per regime
   - Component: `vix_component = position_scale`

2. **Portfolio Breaker** (`engine/breaker.py`)
   - Manages portfolio exposure (OFF, PARTIAL, LOCK modes)
   - Provides `exposure_multiplier_today` [0-1], where lower = more defensive
   - Component: `breaker_component = exposure_multiplier_today`

3. **Risk-Off Guard** (`daily_quant_report.py`)
   - Boolean flag when system moves to emergency cash allocation
   - Component: `risk_off_component = 0.0 if risk_off else 1.0`

### Score Derivation
```python
premarket_score = min([breach_component, risk_off_component, vix_component])
```

This formulation ensures the score reflects the most defensive active constraint.

### Signal Buckets
Scores are mapped to intuitive buckets:
- `LOCK` (0.0):     Hard exits, full deleveraging
- `BEARISH` (≤0.5): Alert state, hedging active
- `DEFENSIVE` (<1.0): Reduced positioning
- `RISK_ON` (1.0):   Full participation

### Signal Derivation
```python
bearish_flag = (score <= 0.5) or (risk_off) or (breaker_mode == "LOCK") or (vix_regime in [HIGH, CRISIS])
```

## Validation Framework

### 1. Alarm Day Metrics

**Definition**: A day where `premarket_score <= threshold` (default 0.5).

**Metrics Computed**:
- `alarm_day_count`: Number of days meeting threshold
- `avg_spy_return_alarm`: Average SPY return on alarm days
- `avg_strategy_return_alarm`: Average strategy return on alarm days
- `avg_excess_return_alarm`: Average excess return on alarm days
- `strategy_return_std_alarm`: Volatility of strategy returns on alarm days

**Interpretation**:
- Negative avg SPY return on alarm days suggests timing accuracy
- Lower avg strategy return may indicate successful macro hedging
- Positive excess return on alarm days suggests strategy adds value during stress periods

### 2. Confusion Matrix

**Prediction vs Actual Classification**:

|  | SPY Up | SPY Down |
|---|---|---|
| **Signal Bearish** | False Positive (FP) | True Positive (TP) |
| **Signal Bullish** | True Negative (TN) | False Negative (FN) |

**Metrics**:
- **Hit Rate** = TP / (TP + FP) = % of bearish signals that correctly predicted downside
- **False Positive Rate** = FP / (FP + TN) = % of up days falsely signaled as bearish
- **False Negative Rate** = FN / (FN + TP) = % of down days falsely signaled as bullish
- **Accuracy** = (TP + TN) / Total = % of all predictions correct

**Interpretation**:
- Hit rate > 50% indicates signals have some predictive power
- FPR < 50% means signals avoid crying wolf too often
- FNR < 50% means signals catch most major downside events

### 3. Threshold Sweep

Evaluates signal performance across multiple score cutoffs (default: 0.25, 0.5, 0.75).

**Purpose**:
- Identify optimal threshold for strategy application
- Trade off sensitivity (catch more downside) vs specificity (avoid false alarms)

**Output**: `analyzer_threshold_sweep.csv` with metrics for each threshold

### 4. Excess Return Attribution

**Benchmark-Relative Metrics**:

- **Total Return**: Strategy NAV growth vs SPY price growth
- **Avg Daily Excess Return**: Strategy return - SPY return, averaged
- **Rolling Beta** (20-day): Sensitivity to SPY movements
  - β < 1.0 = defensive (outperforms in down markets)
  - β > 1.0 = aggressive (amplifies in up markets)
- **Rolling Alpha** (20-day): Outperformance vs regression line (intercept)
  - Positive α = consistent outperformance
- **Tracking Error**: Volatility of excess returns
- **Information Ratio** = Excess Return / Tracking Error (annualized)
  - Measures risk-adjusted outperformance
- **Upside Capture Ratio**: Strategy return on SPY up days / SPY return
  - Ratio < 100% = underperformance in rallies
  - Ratio > 100% = outperformance in rallies
- **Downside Capture Ratio**: Strategy return on SPY down days / SPY return
  - Ratio > 100% = better protection in downturns (good for defensive strategy)
  - Ratio < 100% = amplified losses in downturns

### 5. Overlay Conditioning

Evaluates overlay hedge performance conditioned on analyzer alarm state.

**Dimensions**:
1. **Analyzer State**: Alarm days (score ≤ 0.5) vs Normal days (> 0.5)
2. **Overlay State**: Active (multiplier < 1.0) vs Inactive (multiplier = 1.0)

**Metrics by Condition**:
- `sample_size`: Number of rows in condition
- `avg_strategy_return`: Mean strategy return
- `avg_spy_return`: Mean SPY return
- `downside_capture`: Strategy downside capture ratio on alert days with overlay active

**Interpretation**:
- Overlay should improve downside capture on alarm days
- Effective hedges show higher downside capture ratio (less negative returns in down markets)
- No data when strategy/SPY are missing indicates data sparsity

## Sample Size Considerations

### Minimum Support Thresholds
- **Coverage**: ≥10 days with `premarket_score` not null
- **Condition**: ≥3 evaluation rows with both score and SPY return

### Small Sample Labeling
Metrics with insufficient support are marked:
- JSON: `"sufficient_support": false`
- CSV: Can be filtered on support flag
- HTML: Results shown but caveat provided

### Why These Thresholds?
- 10+ coverage rows = enough to estimate average behavior
- 3+ condition rows = minimum for confusion matrix (avoid "100% accuracy" on 1-2 rows)

## Output Artifacts

### 1. `analyzer_validation_summary.json`
Complete validation results as JSON structure. Keys:
- `analyzer_main_metrics`: Default threshold (0.5) results
- `excess_return_attribution`: Benchmark-relative metrics
- `overlay_conditioned`: Overlay effectiveness by condition
- `threshold_sweep`: List of metrics for each threshold tested
- `metadata`: Data sources and generation timestamp

### 2. `analyzer_validation_summary.html`
Human-readable report with:
- Main metrics dashboard
- Confusion matrix visualization
- Excess return attribution table
- Threshold sweep comparison table
- Support status indicators
- Analysis notes and interpretation guidance

### 3. `analyzer_threshold_sweep.csv`
Metrics for each threshold, suitable for plotting or further analysis. Columns:
- `threshold`, `alarm_day_count`, `coverage_count`
- `avg_spy_return_alarm`, `avg_strategy_return_alarm`, `avg_excess_return_alarm`
- Confusion matrix fields (if available): `true_positive`, `false_positive`, etc.
- `hit_rate`, `false_positive_rate`, `false_negative_rate`, `accuracy`
- `sufficient_support`: Boolean flag

### 4. `excess_return_attribution.csv`
Single-row summary of benchmark-relative performance metrics. Columns:
- `strategy_total_return`, `spy_total_return`
- `avg_daily_excess_return`, `excess_return_std`
- `avg_rolling_beta`, `avg_rolling_alpha`
- `tracking_error_annualized`, `information_ratio`
- `upside_capture`, `downside_capture`

## Interpretation Guide

### Strong Evidence of Analyzer Usefulness
1. Hit rate > 50% (signals catch more downside than they miss)
2. False positive rate < 50% (don't cry wolf too often)
3. Rolling beta < 1.0 on alarm days (positioning reduces in volatility)
4. Information ratio > 0.5 (reasonable risk-adjusted outperformance)
5. Downside capture > 100% on alarm days with overlay (hedges work)

### Weak Evidence (Insufficient Support)
1. Coverage < 10 rows (too few observations)
2. Condition sample < 3 (can't reliably compute confusion matrix)
3. All metrics with "insufficient support" flag should be interpreted cautiously
4. Small sample results can reverse dramatically with new data

### Data Sparsity Issues
Current challenges:
- `strategy_return` nulls: Only 3-5 evaluation rows in first ~1 week of data
- `holdings_mtm` missing: Prevents full nav accumulation
- `benchmark_close` sparse: Limits historical SPY comparison

**Mitigation**: System now captures full analyzer payload for reproducible future backtesting once holdings/nav data becomes available.

## Relationship to Other Analysis

### Alpha Assessment
- **Input**: Canonical performance CSV (daily snapshots)
- **Output**: Field coverage metrics, quality warnings
- **Use**: Validator consumes canonical output; not a separate pipeline

### Overlay Engine
- **Input**: Strategy signals + analyzer state
- **Output**: Overlay multiplier time series
- **Use**: Validator cross-references overlay state for hedge effectiveness

### ICMonitor
- **Input**: Daily signal snapshots
- **Output**: Information coefficient
- **Note**: Analyzer validation complements IC analysis; both measure signal quality

## Implementation Details

### Code Location
- **Core Module**: `research/analyzer_validation.py`
- **Tests**: `Tests/test_analyzer_validation.py`
- **Integration**: Called from `research/alpha_assessment/build_alpha_assessment.py`

### Dependencies
- `pandas`: Data manipulation
- `numpy`: Numerical computations (regression, stats)
- Standard library: json, dataclasses, pathlib

### Computation Complexity
- **Alarm day aggregation**: O(n) where n = rows with score
- **Confusion matrix**: O(m) where m = rows with both score and SPY return
- **Rolling beta**: O(n * window_size); default window 20 days
- **HTML generation**: O(results_dict) serialization

### Reproducibility
All computations are deterministic:
- No random sampling or shuffling
- No parameter tuning on data (hard-coded thresholds)
- Threshold sweep uses fixed list [0.25, 0.5, 0.75]

## Future Enhancements

### Phase 2: Richer Conditioning
- Add vix_regime-conditional evaluation
- Sector-level signal effectiveness
- Drawer-depth conditional (portfolio size dependent)

### Phase 3: Attribution Deep Dive
- Fama-French multi-factor decomposition
- Risk factor exposures on analyzer alarm days
- Contribution of each component (VIX, breaker, risk_off) to performance variance

### Phase 4: Forecasting
- Out-of-sample validation (walk-forward)
- Retraining frequency optimization
- Alternative signal formulations (e.g., weighted sum vs min)
