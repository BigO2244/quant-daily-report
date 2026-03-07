"""
Coverage Expansion Audit + Data Flow Analysis

CURRENT STATE (as of 2026-03-06)
================================

Canonical Performance Row Coverage:
- date: 17/17 (100%)
- strategy_nav: 1/17 (5.9%)
- strategy_return: 1/17 (5.9%)
- spy_close: 4/17 (23.5%)
- spy_return: 4/17 (23.5%)
- vix_close: 1/17 (5.9%)
- vix_regime: 2/17 (11.8%)
- excess_return: 1/17 (5.9%)
- gross_exposure: 2/17 (11.8%)
- net_exposure: 2/17 (11.8%)
- cash_weight: 1/17 (5.9%)
- realized_pnl: 3/17 (17.6%)
- unrealized_pnl: 4/17 (23.5%)

Analyzer Evaluation Rows: 3 (insufficient for statistics)

KEY OVERLAPS:
- score + spy_return: 3 rows
- score + strategy_return: 0 rows
- All three (score + spy + strat): 0 rows

AUTHORITATIVE SOURCES IN REPO
==============================

1. strategy_nav (target: 100% coverage via nav_timeseries.csv)
   Current source: daily_quant_report.py → nav_timeseries.csv
   Status: SPARSE (1 row, should be ~17)
   
   Root cause analysis:
   - daily_quant_report.py writes nav_timeseries.csv (line 665-667)
   - Appends to existing CSV (line 885)
   - BUT: Only gets called in main() after nav computation
   - Likely not being called consistently in daily runs
   
   Fix: Ensure daily_quant_report.py always writes nav_timeseries
         (may need to move call to earlier in pipeline)
   
   Backfill opportunity: 
   - Check if paper/nav2.py has historical nav data
   - Check if signals/*.json has any nav metadata
   - Check paper state files for nav history

2. strategy_return (derived from strategy_nav via pct_change)
   Source: daily_quant_report.py navvtimeseries.csv
   Status: SPARSE (same as strategy_nav)
   Fix: Once nav_timeseries is populated, return computed

3. spy_close + spy_return (target: 100% via benchmark producer)
   Current source: paper/perf_artifact_producers.py → benchmark_close_history.csv
   Status: PARTIAL (4/17 rows, 23.5%)
   
   Root cause:
   - Producer only runs when nav_timeseries exists (in alpha_assessment build)
   - yfinance doesn't have data for all dates (market holidays?)
   - Starting date (2026-02-05) is historical, may need different logic
   
   Fix: Run benchmark producer independently of nav
         Fetch full SPY history via yfinance upfront
         Handle date misalignment (weekends, holidays)

4. vix_close + vix_regime (target: 100% via new producer)
   Current source: research/vix_regime.py (computes from vix_close)
   Status: SPARSE (1 row vix_close, 2 rows vix_regime)
   
   Root cause:
   - vix_regime.py computes regime but doesn't persist vix_close
   - No standalone VIX history producer yet
   
   Fix: Create new vix_history_producer() in paper/perf_artifact_producers.py
         Fetch VIX history via yfinance
         Align dates with SPY and strategy days

5. gross_exposure, net_exposure, cash_weight (target: 100%)
   Current source: daily_quant_report.py → nav_timeseries.csv
   Status: SPARSE (2/17 rows)
   
   Root cause: Same as strategy_nav (nav_timeseries not consistently written)
   
   Fix: Same as #1 - ensure daily writes to nav_timeseries

6. realized_pnl, unrealized_pnl (target: 60-80% coverage)
   Current source: daily_quant_report.py (paper module computations)
   Status: SPARSE (3-4 rows)
   
   Root cause:
   - These come from paper/mark_to_market.py computations
   - Only added to canonical if nav computation succeeds
   
   Fix: Ensure mark_to_market runs daily and writes to nav_timeseries

7. premarket_score (target: 100%, already at 47%)
   Current source: daily_quant_report.py + producer
   Status: GOOD (47% coverage, producer working)
   
   Fix: Already implemented in Phase 3, no action needed

IMPROVEMENT STRATEGY
====================

Phase 1: Ensure Daily Nav Persistence (DAY 1-2)
- Modify daily_quant_report.py to ALWAYS write nav_timeseries.csv
- Call it as the last step in main() to ensure all nav data is computed
- Test with next daily run (2026-03-07)
- Expected result: nav, strategy_return, gross_exposure, net_exposure, cash_weight
  jump to 100% coverage once consistent daily writes happen

Phase 2: Fill SPY/VIX History (DAY 2-3)
- Create standalone benchmark_history_backfill() function
- Run yfinance upfront to get SPY close history (2026-02-05 to today)
- Create new vix_history_producer() for VIX data
- Handle date alignment (skip weekends/holidays)
- Append to existing benchmark_close_history.csv
- Expected result: spy_close, spy_return jump to 80%+

Phase 3: Align Benchmark/VIX with Strategy Days (DAY 3)
- In canonical builder, left-join benchmark/VIX on `date`
- For missing days, use forward-fill or interpolation
  (SPY close should be stable; VIX can be forward-filled)
- Expected result: Both jump to 90%+

Phase 4: Backfill PnL Data (DAY 4?, dependent on data availability)
- Check if paper state files have old NAV/holdings
- If available, reconstruct nav_timeseries via paper/nav2.py
- Expected result: realized_pnl, unrealized_pnl → 60-80%

EXPECTED OUTCOMES
=================

Phase 1 (nav persistence): 
- Canonical rows with strategy_nav + strategy_return: 17 → 17 (100%)
- Canonical rows with exposure/cash: 2 → 17 (100%)
- Still missing: spy, vix (unless provided simultaneously)

Phase 2 (benchmark/vix history):
- Canonical rows with spy_close + spy_return: 4 → 15-17 (88-100%)
- Canonical rows with vix: 1 → 15-17 (88-100%)

Phase 3 (alignment):
- All date-joinable fields: 90%+ coverage

Phase 4 (PnL backfill, optional):
- realized_pnl, unrealized_pnl: 3 → 10-12 (59-71%)

ANALYZER VALIDATION IMPROVEMENT
================================

Current: 3 evaluation rows (insufficient)
After Phase 1+2: ~12-15 evaluation rows (marginal)
After Phase 3: ~15-17 evaluation rows (GOOD, enables statistical testing)
After Phase 4: ~15-17 rows with PnL context (enables full attribution)

Phase 1+2 Target: 
- Evaluation rows: 8-10 → sufficient for basic hit rate estimation
- Support for: accuracy, hit rate, false positive rate (with caveats)

Phase 3 Target:
- Evaluation rows: 14-16 → sufficient for confidence intervals
- Can do: rolling beta, information ratio, capture ratios

DATA GAPS THAT CANNOT BE BACKFILLED
====================================

1. Historical strategy NAV before 2026-02-27
   - Paper trading only started recently
   - No retroactive pos

ition data available
   - Solution: Accept gap, begin consistent daily writes from now on

2. Strategy returns on early dates (2026-02-05 to 2026-02-26)
   - Paper state files may not have complete nav
   - Solution: Same as above, or check if run_paper.py has logged output

3. Full attribution data (multi-factor betas, sector exposures)
   - Not currently computed daily
   - Solution: Out of scope for Phase 1 (can add in future)
"""

# This module documents the audit; no code to run here.
# The actual fixes are implemented in subsequent modules.
