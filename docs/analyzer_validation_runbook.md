# Analyzer Validation Quick-Start Runbook

## Running Analyzer Validation

### Automatic (Recommended)
The analyzer validation is automatically generated as part of the daily alpha assessment build:

```bash
python -m research.alpha_assessment.build_alpha_assessment --rebuild-canonical
```

This generates:
- `outputs/alpha_assessment/analyzer_validation_summary.html` (view in browser)
- `outputs/alpha_assessment/analyzer_validation_summary.json` (programmatic access)
- `outputs/alpha_assessment/analyzer_threshold_sweep.csv` (export for analysis)
- `outputs/alpha_assessment/excess_return_attribution.csv` (benchmark metrics)

### Manual Run (for testing)
```python
from research.analyzer_validation import generate_analyzer_validation_summary

results = generate_analyzer_validation_summary(
    canonical_path="outputs/alpha_assessment/canonical_performance.csv",
    overlay_path="outputs/overlay_engine/overlay_backtest.csv",  # optional
    output_dir="outputs/alpha_assessment",
)
```

## Reading the HTML Report

### Dashboard Section
Key metrics at a glance:
- **Alarm Days**: Count of days where `premarket_score <= 0.5`
- **Coverage Count**: Total rows with non-null `premarket_score`
- **Support Status**: ✓ Sufficient (≥10 coverage + ≥3 evaluation rows) or ✗ Low Sample

⚠️ **Interpretation**: If support status is ✗, treat all metrics with high skepticism. Results may change significantly as more data accumulates.

### Confusion Matrix

The 2×2 table shows how often the analyzer correctly predicted SPY direction:

```
                SPY Up    SPY Down
Signal Bearish    FP        TP
Signal Bullish    TN        FN
```

**What to look for**:
- **True Positive (TP)**: Bearish signal correctly predicted downside ✓ Good
- **False Positive (FP)**: Bearish signal but market went up ✗ Bad
- **True Negative (TN)**: Bullish signal correctly predicted upside ✓ Good
- **False Negative (FN)**: Bullish signal but market went down ✗ Bad

**Example Reading**:
```
Hit Rate = 2/4 = 50%
FP Rate = 2/2 = 100%

Interpretation: Signal is 50% accurate at catching downside but creates false alarms too.
```

### Excess Return Attribution

#### β (Beta) Interpretation
- β = 0.5: Strategy moves 50¢ for every $1 SPY move (defensive)
- β = 1.0: Strategy moves 1:1 with SPY (neutral)
- β = 1.5: Strategy moves 1.5x SPY (aggressive)

**For bearish analyzer**: Want β < 1.0 on alarm days (protection).

#### α (Alpha) Interpretation  
- α = +0.0001 per day: +0.025% daily outperformance (~6% annualized)
- α = -0.0001 per day: -0.025% daily underperformance

**For analyzer validation**: Positive α on alarm days suggests skill in decisionmaking.

#### Information Ratio
- IR > 0.5: Good risk-adjusted outperformance
- IR > 1.0: Excellent
- IR < 0.0: Negative (underperforming)

**Rule of thumb**: IR > 0.3 is meaningful at 5+ year horizon; <0.3 likely noise with <1 year data.

#### Upside/Downside Capture
- Upside Capture = Strategy Return (SPY up days) / SPY Return
  - = 0.8: Only capture 80% of upside rallies (lagging)
  - = 1.2: Capture 120% of upside (outperforming)
  
- Downside Capture = Strategy Return (SPY down days) / SPY Return
  - = 0.5: Only lose 50% as much as SPY (strong protection)
  - = 1.0: Lose same as SPY (no hedging)
  - = 1.5: Lose 50% more than SPY (amplified downside)

**For defensive strategy**: Want Upside Capture close to 100%, Downside Capture < 100%.

### Threshold Sweep Table

Shows how metrics change with different signal thresholds:

| Threshold | Alarm Days | Hit Rate | FP Rate | Support |
|---|---|---|---|---|
| 0.25 | 2 | 100% | 0% | ✗ |
| 0.50 | 8 | 50% | 67% | ✓ |
| 0.75 | 12 | 30% | 75% | ✓ |

**Reading**:
- Lower threshold (0.25): Very selective (few false alarms) but misses opportunities
- Medium threshold (0.50): Balanced (hit rate vs false positives)
- Higher threshold (0.75): Cautious (catches more downside, more false alarms)

**Recommendation**: Use threshold where you're comfortable with false positive rate. For portfolio managers: accept 50-70% FP rate if hit rate > 50%.

## Common Questions & Answers

### Q: Why is my hit rate only 50%?
**A**: With 2-4 evaluation rows, this is mostly noise. Wait for ≥10+ rows. Also remember: even random signals have ~50% accuracy by coin flip.

### Q: My downside capture is > 100%. That's bad, right?
**A**: Yes. It means the strategy loses *more* than SPY during downturns. Either:
1. Strategy has leverage (amplified beta)
2. Individual holdings are volatile
3. Analyzer signal is contrarian (goes bullish when it should be defensive)

**Action**: Check if `premarket_score` is inversely correlated with drawdowns. If so, invert the logic.

### Q: Should I trade on these signals yet?
**A**: No. You have ~1 week of evaluation data, which is barely enough for sampling error checks. Recommended minimum: 3-6 months of live data before confidence in signal.

### Q: What if I have sufficient support but hit rate = 0%?
**A**: The signal is consistently *wrong*. Inverting it (buy when signal says sell) might be valuable! But verify it's not accidental. Run `--rebuild-canonical` to double-check data quality.

### Q: How often should I re-run this?
**A**: Weekly or as part of daily alpha assessment build. Metrics should stabilize as data accumulates.

## Troubleshooting

### Issue: "Low coverage: only 3 rows with premarket_score"
**Cause**: Analyzer score nulls early in week before platform stabilizes.
**Solution**: Expand date range or wait for more production data.

### Issue: "All confusion matrix fields are 0"
**Cause**: No rows with both `premarket_score` and `spy_return` non-null.
**Solution**: Check: 
1. Is `canonical_performance.csv` populated? (`ls outputs/alpha_assessment/canonical_performance.csv`)
2. Does it have both `premarket_score` and `spy_return` columns? (`head -20 ...`)

### Issue: "benchmark_relative metrics are all None"
**Cause**: Missing `strategy_nav` or `spy_close` in canonical.
**Solution**: Check alpha assessment run for `(missing_required=... spy_close ...)` notes. May need nav_timeseries backfill.

### Issue: "Overlay conditioned metrics missing"
**Cause**: `overlay_backtest.csv` not found or not provided.
**Solution**: 
- Run overlay engine: `python -m research.overlay_engine.build_overlay_engine`
- Or provide explicit path if overlay file is elsewhere

## Plugging Into Decisions

### Trade Level (Daily)
1. Check `premarket_score` at market open
2. If ≤ 0.5 (or your chosen threshold) and hit rate > 50%:
   - Reduce gross exposure 20-30%
   - Add hedges (put spreads, VIX calls)
   - Tighten stop losses

### Strategy Level (Weekly)
1. Pull latest `analyzer_validation_summary.html`
2. If hit rate declining or false positive rate rising:
   - Review signal components (VIX, breaker, risk_off logic)
   - Check if market regime changed (structural break)
3. If information ratio positive and stable:
   - Consider weight increase in portfolio allocation
4. If downside capture degrading:
   - Refresh hedge parameters or overlay signal logic

### Risk Committee Level (Monthly)
1. Present full `analyzer_validation_summary.json`
2. Focus on: Hit rate, FP rate, information ratio, coverage
3. Decision:
   - Approve for live trading: Yes if support sufficient, metrics positive
   - Expand tradable universe: Yes if robust across sectors/asset classes
   - Archive or pivot: Yes if hit rate < 30% persistently

## Batch Analysis (Python)

For programmatic access to validation results:

```python
import json

# Load validation output
with open("outputs/alpha_assessment/analyzer_validation_summary.json") as f:
    results = json.load(f)

# Extract key metrics
main_metrics = results["analyzer_main_metrics"]
hit_rate = main_metrics["confusion_matrix"]["hit_rate"]
info_ratio = results["excess_return_attribution"]["information_ratio"]

# Decision logic
if hit_rate > 0.5 and info_ratio > 0.0:
    print("Signal is useful. Consider live trading.")
else:
    print("Signal needs refinement. Gather more data.")

# Threshold sweep analysis
sweeps = results["threshold_sweep"]
for sweep in sweeps:
    if sweep["sufficient_support"]:
        print(f"Threshold {sweep['threshold']}: Hit rate {sweep['hit_rate']:.1%}")
```

## Performance Targets

These are aspirational, not minimum requirements. Performance will vary by market regime.

| Metric | Weak | Acceptable | Strong |
|---|---|---|---|
| Hit Rate | < 40% | 40-60% | > 60% |
| False Positive Rate | > 70% | 50-70% | < 50% |
| Information Ratio | < 0.0 | 0.0-0.5 | > 0.5 |
| Beta (alarm days) | > 1.0 | 0.7-1.0 | < 0.7 |
| Downside Capture | > 100% | 80-100% | < 80% |
| Coverage Count | < 10 | 10-50 | > 50 |

## Next Steps If Unsure

1. Read `docs/analyzer_validation_methodology.md` for full definitions
2. Run validation weekly; plot hit rate and FP rate over time
3. Compare to random baseline: 50% accuracy, 100% coverage
4. Once 12+ weeks of data exist, run statistical tests (binomial test on hit rate)
5. Engage quant team to interpret in context of other signals
