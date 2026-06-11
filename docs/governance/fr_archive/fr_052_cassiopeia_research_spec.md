# FR-052 Cassiopeia Research Specification

Status: Draft
Owner: Caerus Research Program
Last Updated: 2026-06-08
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Implementation Status: SPEC-ONLY — no module. The Cassiopeia name was previously used in code for a regime/model-selection layer; that implementation was re-homed to Argo (FR-053) on 2026-06-08. Cassiopeia remains the canonical EVENT-DRIVEN strategy and is unimplemented.

## Purpose

Cassiopeia is the proposed Caerus event-driven shadow strategy. Its objective
is to convert discrete corporate and market catalyst events into deterministic
long-only research holdings, with explicit event availability metadata and
auditable decision traces.

Cassiopeia is not a change to Polaris, Orion, Lyra, Phoenix, Cygnus, paper
execution, live execution, broker submission, cron timing, or production
portfolio construction. The first pass is research-only and must not send
orders.

## Research Hypothesis

Certain corporate and market events create persistent return effects after the
event becomes public. A conservative, timestamp-aware event strategy can capture
some of that drift while adding a return driver that is distinct from pure
momentum, earnings drift, and crisis reversal.

Primary test:

- A daily, point-in-time event-driven basket selected only from events available
  by the decision timestamp can produce positive risk-adjusted return over
  event-specific holding windows, with measurable differentiation versus
  Polaris, Orion, Lyra, Phoenix, Cygnus, and SPY.

## Event Taxonomy

Cassiopeia should classify events by source, direction, confidence, expected
holding period, and data availability quality.

Baseline taxonomy:

| Event Family | Examples | Direction Handling | First-Pass Status |
|---|---|---|---|
| Analyst rating actions | upgrades, downgrades, initiations, target changes | long only for positive revisions; bearish events used as exclusions | MVP candidate |
| Index events | S&P 500 additions/removals, Russell reconstitution, Nasdaq index changes | long additions; removals used as exclusions | MVP candidate |
| Activist involvement | 13D filings, activist campaigns, board nominations | long constructive activist entries | MVP candidate |
| CEO and leadership transitions | CEO appointment, planned retirement, founder return | event-specific; initially informational unless structured | Later |
| Spin-offs | announced spin, completed spin, parent/remainco events | long only after reliable terms and dates | Later |
| M&A activity | acquisition rumors, announced deals, break risk | initially excluded from long-only MVP | Later |
| Legal/regulatory catalysts | approvals, investigations, settlements | event-specific | Later |

Each event must include:

- `event_id`
- `event_type`
- `event_family`
- `ticker`
- `event_date`
- `event_time`, when available
- `availability_timestamp`
- `availability_date`
- `source`
- `source_url` or source identifier, when available
- `source_ingested_at`
- `direction`: `positive`, `negative`, or `neutral`
- `confidence`
- `raw_event_payload_hash`
- availability rule used

## Initial MVP Event Types

The MVP should prefer event types with defensible availability dates and
relatively structured public sources.

Recommended MVP:

1. Analyst upgrades and target-price increases
   - Use only vendor records or ingested news items with publication timestamps.
   - Long entries only for upgrades or positive target revisions.
   - Downgrades, target cuts, and negative initiations are exclusion/risk flags.

2. Index additions
   - Use official index-provider announcement date and publication time where
     available.
   - Long entries for additions after the announcement is public.
   - Removals are exclusion/risk flags, not short signals.

3. Activist 13D filings
   - Use SEC filing `acceptanceDateTime` as availability timestamp.
   - Long entries only when filing type and holder metadata pass validation.
   - 13G passive filings are separate and should not be treated as activist by
     default.

Deferred until data handling is proven:

- M&A rumors and deal spreads.
- Spin-off terms and when-issued trading.
- CEO transitions without structured context.
- Qualitative litigation/regulatory events.

## Data Availability Rules

Cassiopeia must be availability-date first. Event date is not enough.

Baseline rules:

- Events are selectable only when `availability_timestamp` is less than or
  equal to the strategy decision timestamp.
- If only a date is available, treat the event as available after market close
  on that date, so the first eligible selection date is the next trading day.
- SEC filings use EDGAR `acceptanceDateTime`; filings accepted after market
  close are first eligible on the next trading day.
- Index events use official announcement timestamps. If unavailable, use the
  next trading day after announcement date.
- Analyst events use vendor/news publication timestamp. If only an effective
  date is available, exclude from MVP rather than infer availability.
- Event revisions, corrections, and cancellations must be represented as new
  event records linked to the original `event_id`.
- No future index membership, completed M&A outcome, later activist ownership,
  next-day analyst follow-up, or revised event metadata may influence an
  earlier selection date.

Fail-closed rules:

- Missing availability metadata => ineligible for MVP.
- Missing ticker mapping => ineligible.
- Duplicate events from multiple sources must be deduplicated deterministically
  by event family, ticker, availability timestamp, source priority, and payload
  hash.
- Optional fields may be absent, but absence must be explicit in artifacts.

## Signal Framework

All signals are computed using event and price data available at or before the
as-of close. The initial return convention is `weights_as_of_t`: holdings
selected from close-of-day `t` inputs are evaluated on `t+1` returns.

The first score should be transparent:

| Component | Direction | Candidate Definition |
|---|---:|---|
| Event strength | Higher is better | Upgrade magnitude, index-event importance, activist confidence |
| Source confidence | Higher is better | Official source, SEC filing, trusted vendor timestamp |
| Price confirmation | Higher is better | Positive abnormal return after event availability without exhaustion |
| Liquidity quality | Higher is better | Tradable price and dollar volume |
| Event freshness | Higher is better | Event age inside expected reaction window |
| Multi-event confirmation | Higher is better | Additional positive events within lookback window |
| Negative-event penalty | Lower is better | Recent downgrade, removal, adverse filing, failed reaction |
| Crowding/exhaustion penalty | Lower is better | Extreme pre-event run-up or gap without follow-through |

Initial deterministic score:

```text
cassiopeia_score =
  0.25 * percentile(event_strength)
+ 0.20 * percentile(source_confidence)
+ 0.15 * percentile(price_confirmation)
+ 0.15 * percentile(event_freshness)
+ 0.10 * percentile(liquidity_quality)
+ 0.10 * percentile(multi_event_confirmation)
+ 0.05 * event_family_priority
- negative_event_penalty
- crowding_penalty
```

Every score component must be persisted so attribution can separate analyst,
index, activist, price-confirmation, and event-freshness effects.

## Universe Definition

Baseline Cassiopeia should use the existing `data/universe.csv` equity universe
for the first research pass.

Eligibility filters:

- US long-only equities only.
- Price at as-of close >= `$5`.
- 20D median dollar volume >= `$20M`.
- At least 252 trading days of adjusted OHLCV history.
- At least one positive eligible event within the configured entry window.
- No active negative exclusion event inside its exclusion window.
- Current close and volume must be present for the as-of date.
- Exclude stale prices, zero volume, impossible returns, unresolved ticker
  mappings, and missing corporate-action-adjusted close.
- Benchmark remains `SPY`.

Later variants may use a broader liquid universe because event opportunities
can be sparse, but expansion must have explicit survivorship and ticker-mapping
controls.

## Entry Rules

Cassiopeia should generate holdings after each completed market day.

Baseline entry:

1. Build the event tape using only events available by trade date `t`.
2. Deduplicate event records deterministically.
3. Apply event-family eligibility:
   - analyst upgrades and target increases: entry window 1-20 trading days
     after availability.
   - index additions: entry window from first eligible close through effective
     date plus 5 trading days.
   - activist 13D filings: entry window 1-30 trading days after EDGAR
     acceptance availability.
4. Require valid price/liquidity data at `t`.
5. Require non-negative or peer-relative positive price confirmation unless the
   event family variant explicitly tests immediate entry.
6. Rank eligible candidates by `cassiopeia_score`.
7. Select top 8-15 candidates.
8. Weight equal-weight or inverse-volatility capped weights, with cash allowed
   when fewer candidates qualify.

No intraday data is required for the baseline. Timestamped intraday events are
mapped conservatively to end-of-day decisions.

## Exit Rules

Exit rules should be deterministic and event-family aware:

- Time stop:
  - analyst events: 20-40 trading days.
  - index additions: effective date plus 10 trading days, capped at 40 days.
  - activist events: 30-90 trading days for research variants.
- Minimum hold: prefer 5 trading days unless a risk stop or negative event
  triggers.
- Event invalidation exit: exit when a linked correction, cancellation, removal,
  downgrade, or adverse filing invalidates the thesis.
- Price failure exit: exit if price closes below event-availability close by
  more than 8%.
- Rank-decay exit: exit when current Cassiopeia rank falls below top 2x target
  count.
- Liquidity/stale-data exit: exit when current data fails eligibility.

First research variants:

- `cassiopeia_v0_analyst_positive`: analyst upgrades and positive target
  revisions only.
- `cassiopeia_v1_index_additions`: index additions only.
- `cassiopeia_v2_activist_13d`: activist 13D filings only.
- `cassiopeia_v3_combined_mvp`: combined analyst, index, and activist MVP with
  event-family attribution.

## Holding-Period Assumptions

Expected holding period: variable by event family.

Baseline assumptions:

- Analyst positive events: 10-40 trading days.
- Index additions: announcement through effective date plus 10 trading days.
- Activist 13D filings: 20-90 trading days.
- Combined MVP portfolio: weighted average expected hold should be reported
  daily.

Backtest accounting:

- Use end-of-day selection on `t`; first return realized from `t` close to
  `t+1` close.
- Do not use same-day close-to-close returns after observing an event that was
  not available before the close decision timestamp.
- Apply transaction cost assumptions at entry and exit.
- Persist event id, event family, availability timestamp, entry date, event age,
  entry score, entry price proxy, exit date, and exit reason.

## Risk Controls

Research-only risk controls:

- Max gross exposure: `80%` baseline, with remaining weight held as cash.
- Max single-name weight: `10%`.
- Max sector weight: `30%` when sector data is available.
- Max positions: `15`; minimum diversified active basket: `5`.
- Max same-event-family exposure: `50%` in combined MVP.
- Volatility cap: downweight names with 20D realized volatility above `80%`
  annualized.
- Negative-event exclusion: skip names with recent downgrades, index removals,
  adverse filings, or failed reaction flags.
- M&A exclusion: skip active announced acquisition targets in MVP unless a
  later deal-spread variant is explicitly approved.
- Turnover guard: do not churn an existing Cassiopeia holding if it still
  satisfies hold rules and remains above rank-decay threshold.

These controls are research constraints only. They must not alter Polaris,
Orion, Lyra, Phoenix, Cygnus, paper execution, or live execution.

## Expected Artifacts

Research baseline outputs:

- `outputs/research/cassiopeia/<date>/cassiopeia_event_tape.parquet`
- `outputs/research/cassiopeia/<date>/cassiopeia_signal_frame.parquet`
- `outputs/research/cassiopeia/<date>/cassiopeia_rank_table.csv`
- `outputs/research/cassiopeia/<date>/cassiopeia_holdings.json`
- `outputs/research/cassiopeia/<date>/cassiopeia_backtest_summary.json`
- `outputs/research/cassiopeia/<date>/cassiopeia_decision_trace.json`
- `outputs/research/cassiopeia/<date>/cassiopeia_attribution_inputs.json`
- `outputs/research/cassiopeia/performance/cassiopeia_nav_series.csv`
- `outputs/research/cassiopeia/performance/cassiopeia_summary.json`

When promoted to the shadow framework, add:

- `outputs/shadow_candidates/<date>/caerus_cassiopeia.json`
- Cassiopeia row in `comparison.json`
- Cassiopeia row in `shadow_performance.json`
- Cassiopeia row in `shadow_evaluation.json`
- Cassiopeia panel in `promotion_readiness.json`
- Cassiopeia folder in feedback-loop artifacts:
  `outputs/shadow_candidates/<date>/cassiopeia/`

Every artifact should include:

- `schema_version`
- `trade_date`
- `strategy_id: caerus_cassiopeia`
- `strategy_slug: caerus_cassiopeia`
- `governance_label: RESEARCH_ONLY` or `SHADOW_ONLY`
- `execution_impact: NON_EXECUTIONAL`
- event taxonomy and source metadata
- availability timestamp and availability rule
- data coverage and freshness metadata
- explicit unavailable/partial status reasons

## Integration Points

Existing systems to preserve:

- Polaris remains paper/control strategy.
- Orion and Lyra remain existing shadow candidates.
- Phoenix and Cygnus remain research/shadow candidates only according to their
  own governance status.
- SPY remains benchmark.
- Promotion-readiness logic remains informational and non-executing.

Research-only integration:

- New module under `research/cassiopeia/` for event tape construction,
  deduplication, feature engineering, scoring, backtest, and artifact writing.
- Optional CLI wrapper `scripts/research/run_cassiopeia_research.py`.
- No changes to `scripts/cron_execute.sh`, `scripts/cron_precompute.sh`,
  broker/order code, or paper/live execution code.

Shadow integration after research validation:

- Extend `research.shadow_tracking` to support dynamic strategy definitions
  rather than hard-coded Polaris/Orion/Lyra assumptions.
- Add Cassiopeia as `caerus_cassiopeia` only after comparison, performance,
  evaluation, feedback-loop, dashboard, and promotion-readiness readers
  tolerate additional strategy slugs and event metadata.
- Update research registry tools so Cassiopeia can appear in review packets and
  promotion readiness without treating it as paper-eligible.

## Backtest Plan

Stage 1: event-source audit

- Build small, immutable source snapshots for the selected MVP event types.
- Validate timestamp coverage for analyst, index, and EDGAR activist events.
- Audit a sample of each event family manually.
- Confirm no event is selectable before its availability timestamp/date.
- Generate empty-but-valid artifacts for dates without eligible events.

Stage 2: single-family research

- Backtest analyst-positive, index-addition, and activist-13D variants
  separately.
- Report each family independently before combining them.
- Compare event-family returns to SPY, Polaris, Orion, Lyra, Phoenix, and
  Cygnus when history exists.
- Report turnover, hit rate, average hold, drawdown, volatility, sector
  exposure, event-age distribution, and correlation to existing strategy daily
  returns.

Stage 3: combined MVP research

- Combine approved MVP event families with event-family exposure caps.
- Evaluate whether returns are diversified across event families or dominated
  by one source.
- Persist event-family attribution and selected-event examples.

Stage 4: robustness

- Walk-forward parameter sensitivity:
  - top N: 5, 8, 10, 15
  - analyst hold max: 20, 40, 60 days
  - activist hold max: 30, 60, 90 days
  - entry window by event family
  - price-confirmation required/not required
- Test transaction costs at 10, 25, and 50 bps.
- Exclude event families one at a time.
- Confirm no result depends on future event corrections, completed outcomes, or
  unavailable timestamps.

Stage 5: research shadow dry run

- Generate Cassiopeia daily holdings to `outputs/research/cassiopeia/`.
- Reconcile daily holdings against prior day to estimate turnover.
- Build immutable holdings snapshots compatible with attribution tools.
- Keep outside `outputs/shadow_candidates/` until schema readers support the
  additional strategy slug and event metadata fields.

Stage 6: non-blocking shadow integration

- Add Cassiopeia to daily shadow outputs only after compatibility tests pass.
- Keep status `SHADOW_ONLY`.
- Do not include Cassiopeia in paper execution, live-vs-shadow reconciliation
  against Polaris, or capital allocation.

## Promotion-Readiness Plan

Cassiopeia should not be promotion-eligible from backtest alone.

Minimum evidence before any promotion discussion:

- 60 clean forward shadow trading days.
- No broken NAV chain.
- Daily holdings generated and attributable.
- Event availability metadata present for every selected position.
- Stable event-source ingestion and no unexplained stale artifacts.
- Positive excess return versus SPY and Polaris over relevant windows.
- Drawdown and turnover within predeclared limits.
- Low-to-moderate correlation with Polaris/Orion/Lyra and differentiated
  event-family attribution.
- Operator review of worst 10 trades and at least 20 selected source events.

Readiness should remain informational until a separate governance task approves
any capital allocation or execution change.

## Proposed Implementation File List

Research-only first pass:

- `fr_052_cassiopeia_research_spec.md`
- `research/cassiopeia/__init__.py`
- `research/cassiopeia/events.py`
- `research/cassiopeia/event_sources.py`
- `research/cassiopeia/features.py`
- `research/cassiopeia/strategy.py`
- `research/cassiopeia/backtest.py`
- `research/cassiopeia/artifacts.py`
- `scripts/research/run_cassiopeia_research.py`
- `Tests/test_cassiopeia_events.py`
- `Tests/test_cassiopeia_event_sources.py`
- `Tests/test_cassiopeia_features.py`
- `Tests/test_cassiopeia_backtest.py`
- `Tests/test_cassiopeia_artifacts.py`

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

- Event data can create severe look-ahead bias if availability timestamps are
  missing, inferred aggressively, or overwritten by vendor restatements.
- Analyst event histories may be incomplete or vendor-specific.
- Index event source data may be sparse or license-restricted.
- Activist 13D signals can be lumpy and low-frequency.
- Combined event-family scoring can hide one dominant source of returns.
- Event-driven returns may overlap with momentum unless attribution proves
  event-specific contribution.
- M&A and rumor events are high-risk for false positives and should be excluded
  from MVP.
- Existing shadow tracking has hard-coded three-strategy assumptions.

Assumptions:

- Existing adjusted OHLCV cache is sufficient for first-pass price and reaction
  features.
- SEC EDGAR `acceptanceDateTime` is available for activist filing events.
- A reliable timestamped source exists for analyst actions before
  implementation.
- Official or defensibly timestamped index announcement data can be obtained.
- Long-only expression is mandatory.

Open questions:

- Which vendor will provide timestamped analyst upgrades/downgrades and target
  changes with stable historical availability?
- Which index families should be included first: S&P only, Russell only, or a
  broader provider set?
- Should index additions enter at announcement, effective date, or a staged
  schedule between the two?
- Should activist 13D holdings percentage or filer identity affect score
  strength in v0?
- Should CEO transitions be delayed until a structured event-context source is
  available?
- Should Cassiopeia initially require price confirmation, or should immediate
  event-following entry be tested as a separate variant?

## Current Canonical Boundary

Cassiopeia remains the canonical event-driven strategy and is spec-only. The
regime/model-selection implementation has been re-homed to Argo (FR-053). The
FR-063 strategy differentiation deep dive may compare Cassiopeia only when
registered research evidence exists; it must not create an event-driven module,
promote Cassiopeia, or reuse Argo artifacts as Cassiopeia evidence.
