# FR-050 Phoenix Research Specification

Status: Draft
Owner: Caerus Research Program
Last Updated: 2026-06-03
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL

## Purpose

Phoenix is the proposed Caerus crisis-reversal shadow strategy. Its objective is
to identify temporary market overreactions and express them as deterministic
long-only model holdings for research, attribution, and promotion-readiness
analysis.

Phoenix is not a change to Polaris, Orion, Lyra, paper execution, live
execution, broker submission, cron timing, or portfolio construction. The first
pass is research-only and must not send orders.

## Research Hypothesis

Temporary market dislocations create a short-horizon reversal premium when
price damage is large relative to recent behavior, selling pressure is
unusually high, and the candidate remains liquid and tradable. Phoenix should
have lower correlation to the existing momentum-oriented strategy family
because it buys recent stress rather than recent strength.

Primary test:

- A daily, point-in-time crisis-reversal basket selected after market close and
  held for 5-20 trading days can produce positive risk-adjusted return and
  materially differentiated behavior versus Polaris, Orion, Lyra, and SPY.

## Signal Framework

All signals are computed using data available at or before the as-of close.
The first research convention is `weights_as_of_t`: holdings selected from
close-of-day `t` inputs are evaluated on `t+1` returns.

Phoenix score should be a transparent composite, not a fitted black box:

| Component | Direction | Candidate Definition |
|---|---:|---|
| Short-horizon drawdown | Higher is better | Larger 3D/5D/10D loss versus peers and own history |
| Oversold intensity | Higher is better | Low RSI(2), RSI(5), close below 5D/10D/20D moving average |
| Volume capitulation | Higher is better | Volume / 20D volume above threshold, dollar-volume shock |
| Gap or range dislocation | Higher is better | Large negative open-close or high-low range relative to ATR |
| Reversal eligibility | Higher is better | Prior uptrend, liquidity, non-penny stock, no stale prices |
| Falling-knife penalty | Lower is better | Repeated lower lows, extreme multi-day crash, unresolved trading halt proxy |
| Market crisis gate | Contextual | Broader stress from SPY drawdown, VIX/realized vol, breadth washout |

Initial deterministic score:

```text
dislocation_score =
  0.30 * percentile(-5D_return)
+ 0.20 * percentile(-10D_return)
+ 0.15 * percentile(volume_shock_20D)
+ 0.15 * percentile(atr_range_shock)
+ 0.10 * percentile(oversold_rsi_composite)
+ 0.10 * percentile(distance_below_20D_ma)
- falling_knife_penalty
+ market_stress_bonus
```

The first implementation should persist each component so attribution can
separate drawdown, volume, oversold, and regime effects.

## Universe Definition

First-pass Phoenix universe should use the existing `data/universe.csv` equity
universe to avoid introducing new data governance issues.

Eligibility filters:

- US long-only equities only.
- Price at as-of close >= `$5`.
- 20D median dollar volume >= `$20M`.
- At least 252 trading days of adjusted OHLCV history.
- Current close and volume must be present for the as-of date.
- Exclude symbols with impossible returns, zero volume, stale prices, or missing
  corporate-action-adjusted close.
- Benchmark remains `SPY`.

Later research may evaluate Russell 1000/IWB expansion, but that should be a
separate variant after baseline Phoenix is reproducible.

## Entry Rules

Phoenix should generate holdings after each completed market day.

Baseline entry:

1. Compute all signal features using data through trade date `t`.
2. Require a market stress or local dislocation condition:
   - market stress: SPY 5D return <= `-2%`, SPY close below 20D high by >= `3%`,
     VIX/realized volatility elevated if available; or
   - local stress: candidate 5D return <= `-7%` and volume shock >= `1.5x`.
3. Rank eligible candidates by `dislocation_score`.
4. Select top 8-12 candidates.
5. Weight equal-weight or inverse-volatility capped weights, with cash allowed
   when fewer than the minimum candidates qualify.

No intraday information should be required. No future earnings, analyst, or
post-close data may be used unless timestamped and proven available before the
decision timestamp.

## Exit Rules

Exit rules should be deterministic and position-level:

- Profit/reversion exit: close above 10D moving average or RSI(5) >= 55.
- Rank-decay exit: current Phoenix rank falls below top 2x target count.
- Time stop: exit after 20 trading days.
- Minimum hold: prefer 3 trading days unless a risk stop triggers.
- Risk stop: exit if position return <= `-8%` or <= `-2.5 * ATR(20)` from entry.
- Liquidity/stale-data exit: exit when current data fails eligibility.

The first research variant should compare:

- `phoenix_v0_equal_weight_10`: top 10, equal weight, 20D time stop.
- `phoenix_v1_inverse_vol_10`: top 10, inverse 20D vol weights, 20D time stop.
- `phoenix_v2_crisis_gate`: same as v1 but only active during market stress.

## Holding-Period Assumptions

Expected holding period: 5-20 trading days.

Backtest accounting:

- Use end-of-day selection on `t`; first return realized from `t` close to
  `t+1` close, matching the existing shadow convention.
- Apply transaction cost assumptions at entry and exit.
- Do not use same-day close-to-close return after observing the same close for
  selection.
- Persist entry date, age, entry score, entry price proxy, and exit reason for
  each position.

## Risk Controls

Research-only risk controls:

- Max gross exposure: `80%` baseline, with remaining weight held as cash.
- Max single-name weight: `10%`.
- Max sector weight: `30%` when sector data is available.
- Max positions: `12`; minimum diversified active basket: `5`.
- Volatility cap: downweight names with 20D realized volatility above `80%`
  annualized.
- Extreme event exclusion: skip names with one-day return <= `-25%` unless a
  separate review variant explicitly studies gap-crash reversals.
- Turnover guard: do not churn an existing Phoenix holding if it still satisfies
  hold rules and remains above rank-decay threshold.
- Benchmark and portfolio-level drawdown metrics must be reported, but should
  not trigger production behavior.

These controls are research constraints only. They must not alter Polaris,
Orion, Lyra, paper execution, or live execution.

## Data Dependencies

Required:

- Adjusted daily OHLCV for universe symbols.
- Adjusted daily OHLCV for `SPY`.
- Existing `data/universe.csv`.
- Trading calendar / available market dates.

Optional for richer attribution:

- VIX daily close or realized-vol proxy.
- Sector map for sector exposure.
- Existing regime/breadth artifacts if available.
- Corporate-action or split-adjustment metadata when available.

Fail-closed data rules:

- Missing required price/volume data => no signal for that symbol/date.
- Missing benchmark data => no promotion-readiness or SPY-relative claim.
- Missing optional VIX/sector/regime data => artifact status `PARTIAL`, not
  fabricated values.

## Expected Artifacts

Research baseline outputs:

- `outputs/research/phoenix/<date>/phoenix_signal_frame.parquet`
- `outputs/research/phoenix/<date>/phoenix_rank_table.csv`
- `outputs/research/phoenix/<date>/phoenix_holdings.json`
- `outputs/research/phoenix/<date>/phoenix_backtest_summary.json`
- `outputs/research/phoenix/<date>/phoenix_decision_trace.json`
- `outputs/research/phoenix/<date>/phoenix_attribution_inputs.json`
- `outputs/research/phoenix/performance/phoenix_nav_series.csv`
- `outputs/research/phoenix/performance/phoenix_summary.json`

When promoted to the shadow framework, add:

- `outputs/shadow_candidates/<date>/caerus_phoenix.json`
- Phoenix row in `comparison.json`
- Phoenix row in `shadow_performance.json`
- Phoenix row in `shadow_evaluation.json`
- Phoenix panel in `promotion_readiness.json`
- Phoenix folder in feedback-loop artifacts:
  `outputs/shadow_candidates/<date>/phoenix/`

Every artifact should include:

- `schema_version`
- `trade_date`
- `strategy_id: caerus_phoenix`
- `governance_label: RESEARCH_ONLY` or `SHADOW_ONLY`
- `execution_impact: NON_EXECUTIONAL`
- data coverage and freshness metadata
- explicit unavailable/partial status reasons

## Integration Points

Existing systems to preserve:

- Polaris remains paper/control strategy.
- Orion and Lyra remain existing shadow candidates.
- SPY remains benchmark.
- Promotion-readiness logic remains informational and non-executing.

Research-only integration:

- New module under `research/phoenix/` for features, scoring, backtest, and
  artifact writing.
- Optional CLI wrapper `scripts/research/run_phoenix_research.py`.
- No changes to `scripts/cron_execute.sh`, `scripts/cron_precompute.sh`, or
  broker/order code.

Shadow integration after research validation:

- Extend `research.shadow_tracking` to support dynamic strategy definitions
  rather than hard-coded Polaris/Orion/Lyra assumptions.
- Add Phoenix as `caerus_phoenix` only after comparison, performance, evaluation,
  feedback-loop, dashboard, and promotion-readiness readers tolerate an
  additional strategy slug.
- Update research registry tools so shadow comparison and promotion readiness
  can include Phoenix without treating it as paper-eligible.

## Backtest Plan

Stage 1: offline research

- Build features over the existing universe from 2014 onward.
- Run no-download tests from existing price cache first.
- Compare v0/v1/v2 Phoenix variants against SPY, Polaris, Orion, and Lyra.
- Evaluate full-period and regime-sliced returns.
- Report turnover, hit rate, average hold, drawdown, volatility, skew proxy,
  sector exposure, and correlation to existing strategy daily returns.

Stage 2: robustness

- Walk-forward parameter sensitivity:
  - top N: 5, 8, 10, 12
  - hold max: 10, 15, 20 days
  - entry drawdown threshold: 5D loss at 5%, 7%, 10%
  - crisis gate on/off
- Exclude crisis years one at a time.
- Test transaction costs at 10, 25, and 50 bps.
- Confirm no result depends on same-day future returns.

Stage 3: shadow candidate dry run

- Generate Phoenix daily holdings to `outputs/research/phoenix/`.
- Reconcile daily holdings against prior day to estimate turnover.
- Build immutable holdings snapshots compatible with attribution tools.
- Keep outside `outputs/shadow_candidates/` until schema readers support the
  additional strategy slug.

Stage 4: non-blocking shadow integration

- Add Phoenix to daily shadow outputs only after compatibility tests pass.
- Keep status `SHADOW_ONLY`.
- Do not include Phoenix in paper execution, live-vs-shadow reconciliation
  against Polaris, or capital allocation.

## Promotion-Readiness Plan

Phoenix should not be promotion-eligible from backtest alone.

Minimum evidence before any promotion discussion:

- 60 clean forward shadow trading days.
- No broken NAV chain.
- Daily holdings generated and attributable.
- Stable data availability and no unexplained stale artifacts.
- Positive excess return versus SPY and Polaris over relevant windows.
- Drawdown and turnover within predeclared limits.
- Low-to-moderate correlation with Polaris/Orion/Lyra.
- Operator review of worst 10 Phoenix trades and crisis-window behavior.

Readiness should remain informational until a separate governance task approves
any capital allocation or execution change.

## Proposed Implementation File List

Research-only first pass:

- `docs/governance/fr_050_phoenix_research_spec.md`
- `research/phoenix/__init__.py`
- `research/phoenix/features.py`
- `research/phoenix/signals.py`
- `research/phoenix/backtest.py`
- `research/phoenix/artifacts.py`
- `scripts/research/run_phoenix_research.py`
- `Tests/test_phoenix_features.py`
- `Tests/test_phoenix_backtest.py`
- `Tests/test_phoenix_artifacts.py`

Later shadow-framework pass:

- `research/shadow_tracking/strategies.py`
- `research/shadow_tracking/run.py`
- `core/feedback_loop_artifacts.py`
- `scripts/research/build_research_clarity_wave.py`
- `scripts/research/build_daily_research_packet.py`
- `research_registry/research/shadow_comparison.py`
- `research_registry/research/promotion_readiness.py`
- `Tests/test_shadow_tracking.py`
- `Tests/test_research_registry_shadow_comparison.py`
- `Tests/test_research_registry_promotion_readiness.py`

## Risks, Assumptions, And Open Questions

Risks:

- Crisis reversal can catch falling knives; stops and time exits must be tested
  honestly.
- Short-horizon reversal can be highly sensitive to transaction costs.
- Survivorship bias may exist if `data/universe.csv` is current-membership only.
- Existing shadow tracking has hard-coded three-strategy assumptions.
- Optional VIX/sector/regime data gaps could limit attribution.
- Parameter tuning on crisis windows can overfit.

Assumptions:

- Existing adjusted OHLCV cache is sufficient for a first research pass.
- Long-only expression is mandatory.
- Cash is allowed when too few candidates qualify.
- Phoenix should be evaluated as independent alpha, not as a replacement for
  Polaris.

Open questions:

- Should Phoenix eventually use a broader Russell 1000/IWB universe?
- Should the strategy require market-wide stress, or allow single-name
  dislocations in calm markets?
- Should high-idiosyncratic-risk names be excluded after extreme one-day gaps?
- What promotion threshold should be used for correlation reduction versus
  existing momentum strategies?
- Should Argo later allocate to Phoenix only during defined stress regimes?

## Non-Goals

- No production trading behavior changes.
- No broker/order submission changes.
- No options overlay changes.
- No changes to Polaris, Orion, or Lyra selection logic.
- No automatic promotion, demotion, or capital allocation.
