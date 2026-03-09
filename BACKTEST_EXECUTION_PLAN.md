# Alpha Stack Value Sleeve: Backtest Execution Plan

**Status**: Ready to start  
**Date**: 2026-03-08  
**Scope**: Phase 1 hardening + comparative backtests (Trend-only, Value-only, Combined)  

---

## Phase 1 Hardening (8 Hours - To Be Completed)

### Task 1: Sector-Level Filing Lag Audit (2h)

**Objective**: Validate that Healthcare/REIT filing lags don't exceed safe threshold.

**Implementation**:
1. Create `alpha_stack/research/filing_lag_audit.py` with function:
   ```python
   def audit_filing_lags(
       fundamentals_store,
       universe_tickers,
       sector_map,
       as_of_date = "2026-03-08"
   ) -> pd.DataFrame:
       """
       For each sector, compute p50, p75, p90 filing lags.
       Return: sector | median_lag | p75_lag | p90_lag | pct_exceeding_60d
       """
   ```
2. Run audit on 3 sample dates (2025-12-31, 2026-01-31, 2026-02-28)
3. Report findings (which sectors exceed 60d threshold)
4. Decision: Accept 60d assumption or adjust for Healthcare/REIT

**Deliverable**: `filing_lag_audit_results.csv`

---

### Task 2: Stale-Data Tier Weighting (3h)

**Objective**: Implement age-based score adjustment in value factor computation.

**Implementation**:
1. Add method to `FundamentalsDataStore`:
   ```python
   def get_fundamental_with_metadata(
       self, symbol, field, as_of_date
   ) -> Tuple[Optional[float], Optional[int]]:
       """
       Returns (value, days_old) where days_old = as_of_date - filed_date.
       """
   ```

2. Modify `alpha_stack/features/value.py`:
   ```python
   def compute_value_features_with_tier_weighting(
       fundamentals_store,
       universe_tickers,
       as_of_date,
       sector_map,
       apply_age_decay=True  # New param
   ) -> pd.DataFrame:
       """
       Add columns: 
       - ey_age, fcfy_age, bp_age (days)
       - tier (1, 2, 3 based on max age)
       - tier_weight (100%, 80%, 60%)
       - z_ey_adjusted, z_fcfy_adjusted, z_bp_adjusted (tier-weighted)
       """
   ```

3. Tier definitions:
   - **Tier 1** (<60d): weight = 100%
   - **Tier 2** (60-90d): weight = 80%
   - **Tier 3** (>90d): weight = 60% or exclude

4. Test both variants (with/without decay) in backtest

**Deliverable**: Updated `compute_value_features()` in features/value.py

---

### Task 3: Factor-Age Logging (2h)

**Objective**: Track factor ages during backtest for post-hoc analysis.

**Implementation**:
1. Add logging to `ValueSleeveBacktest.generate_signals()`:
   ```python
   # Log factor ages for each date in backtest
   factor_age_log = {
       "date": date,
       "ticker": ticker,
       "ey_age_days": int(as_of_date - ey_filed_date),
       "fcfy_age_days": int(...)
       "bp_age_days": int(...)
   }
   ```

2. Persist to CSV: `backtest_factor_ages.csv` alongside other outputs

3. Post-backtest: Analyze IC by factor age tier

**Deliverable**: Factor age tracking during backtest

---

### Task 4: Data Quality Audit (1h)

**Objective**: Confirm that outlier exclusions match expected counts.

**Implementation**:
1. Create audit in `alpha_stack/research/data_quality_audit.py`:
   ```python
   def audit_data_quality(
       fundamentals_store,
       universe_tickers,
       as_of_date
   ) -> dict:
       """
       Count:
       - Negative equity (should be ~3%)
       - Negative operating CF (should be ~20%)
       - Negative net income (should be ~15%)
       - Missing any field (should be ~18%)
       """
   ```

2. Verify counts match expected distributions from validation report

3. Document any surprises

**Deliverable**: Data quality audit results

---

## Phase 2: ValueSleeveBacktest Class (6 Hours)

**Objective**: Build a Value sleeve backtest similar to TrendSleeveBacktest

**File**: `sleeves/sleeve_value/backtest.py` (new)

**Key Methods**:
1. `prepare_data()`: 
   - Fetch prices for all universe tickers
   - Compute value factors on rolling basis (monthly)
   - Score and rank

2. `generate_signals()`:
   - Compute target weights for top quintile
   - Apply sizing and risk limits
   - Return target_weights_df (date × ticker)

3. `compute_stats()`:
   - Run backtest via backtest_engine
   - Compute Sharpe, MaxDD, turnover, sector concentration

**Output**: Accepts same interface as TrendSleeveBacktest (reuses backtest_engine)

---

## Phase 3: ComparativeBacktestRunner (4 Hours)

**Objective**: Orchestrate all 4 backtest configurations and compile results

**File**: `scripts/comparative_backtest_runner.py` (new)

**Runs**:
1. **Trend-only**: 100% Trend sleeve
2. **Value-only**: 100% Value sleeve
3. **Combined (Static)**: 50% Trend + 50% Value (no rebalancing between sleeves)
4. **Combined (Allocator)**: 50% Trend + 50% Value (allocator may adjust split)

**Outputs** (to `outputs/backtests/`):
- `trend_only_timeseries.csv` (date, nav, ...)
- `value_only_timeseries.csv` (date, nav, ...)
- `combined_static_timeseries.csv` (date, nav, ...)
- `combined_allocator_timeseries.csv` (date, nav, ...)
- `comparative_metrics_summary.csv` (config | sharpe | maxdd | cagr | ir | ...)
- `ic_timeseries.csv` (date, ey_ic, fcfy_ic, bp_ic, composite_ic)
- `factor_decay_curves.json` (lag | ic values)
- `sector_concentration.csv` (date, sector, weight, % of portfolio)

---

## Phase 4: Metrics & Analysis (8 Hours)

**Objective**: Compute all required metrics from backtest outputs

### Metrics to Produce

| Metric | Formula | File |
|--------|---------|------|
| **CAGR** | (ending_nav / starting_nav)^(1/years) - 1 | summary.csv |
| **Sharpe** | mean_ret / std_ret × sqrt(252) | summary.csv |
| **Sortino** | mean_ret / downside_std × sqrt(252) | summary.csv |
| **MaxDD** | peak-to-trough | summary.csv |
| **Turnover** | sum(\|w_t+1 - w_t\|) / 2 annualized | summary.csv |
| **Beta** | cov(portfolio, SPY) / var(SPY) | summary.csv |
| **Alpha** | ret - (rf + beta × mkt_ret) | summary.csv |
| **Win Rate** | % positive days | summary.csv |
| **IC** | Rank correlation (factors, forward returns) | ic_timeseries.csv |
| **Decay** | IC at lag=[1, 5, 10, 20, 60, 126] | factor_decay_curves.json |
| **Sector HHI** | sum(sector_weight^2) | sector_concentration.csv |

### Analysis Scripts

Create in `alpha_stack/research/`:
1. `ic_analyzer.py` — Compute rank IC by factor and date
2. `decay_analyzer.py` — Factor persistence curves
3. `sector_analyzer.py` — Sector concentration over time
4. `regime_analyzer.py` — Performance conditioned on Trend state

---

## Phase 5: Report Generation (4 Hours)

**Output**: `outputs/alpha_stack_validation/COMPARATIVE_BACKTEST_SUMMARY.md`

**Sections**:
1. Executive Summary (returns, risks, allocation recommendation)
2. Configuration Comparison (Trend vs Value vs Combined)
3. Performance Attribution (IC, decay, sector effects)
4. Risk Analysis (drawdowns, correlation, beta)
5. Transaction Costs (turnover × 10bps cost model)
6. Recommendation (proceed to Quality, harden Value further, or keep in research)

---

## Timeline & Effort

```
Week 1 (Starting 2026-03-08):
├─ Day 1-2: Phase 1 Hardening (8h total)
│  ├─ Filing lag audit (2h)
│  ├─ Tier weighting implementation (3h)
│  ├─ Factor age logging (2h)
│  └─ Data quality audit (1h)
├─ Day 3-4: Build ValueSleeveBacktest (6h)
├─ Day 5: Build ComparativeBacktestRunner (4h)
└─ End: Launch first backtest run

Week 2:
├─ Day 1-2: Run all 4 configurations (may be parallel)
├─ Day 3-4: Compute metrics & analysis (8h)
└─ Day 5: Generate final report (4h)

Week 3:
├─ Day 1: Decision & sign-off
└─ Deploy or iterate
```

**Total Effort**: ~34 hours (over 2-3 weeks)

---

## Success Gates

### Gate 1: Value IC > 0.02
**If failed**: Investigate factor definition or data quality

### Gate 2: Combined Sharpe > max(Trend, Value) × 0.95
**If failed**: Sleeves not diversifying; reconsider allocation

### Gate 3: Filing Lag < 65d (p90)
**If failed**: Healthcare/REIT handling required

---

## Next Immediate Action

1. Execute Phase 1 Hardening tasks (today-tomorrow)
2. Create ValueSleeveBacktest class (by Day 3-4)
3. Run first backtest (Value-only) to validate framework

**Owner**: AI Research Agent  
**Blocker**: None (all data available)  
**Status**: Ready to execute
