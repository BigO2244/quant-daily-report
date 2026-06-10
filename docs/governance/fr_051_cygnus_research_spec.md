# FR-051 Cygnus Research Specification

Status: V0_SHELVED — Stage 2 validation FAIL recorded 2026-06-10
Owner: Caerus Research Program
Last Updated: 2026-06-10
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL

## Purpose

Cygnus is the proposed Caerus post-earnings drift shadow strategy. Its
objective is to capture post-earnings announcement drift, estimate-revision
persistence, and positive guidance continuation through deterministic long-only
research holdings.

Cygnus is not a change to Polaris, Orion, Lyra, Phoenix, paper execution, live
execution, broker submission, cron timing, or production portfolio construction.
The first pass is research-only and must not send orders.

## Research Hypothesis

Stocks with positive earnings surprises, constructive price reaction, upward
estimate revisions, and stable liquidity can continue drifting for several
weeks after the earnings event. Cygnus should be more catalyst-driven than the
current momentum family because entries depend on earnings-event availability
and revision confirmation, not only broad cross-sectional price strength.

Primary test:

- A daily, point-in-time earnings drift basket selected from earnings events and
  estimate revisions available by the decision timestamp can produce positive
  risk-adjusted return over 10-60 trading days while remaining attributable and
  reproducible.

## Earnings-Event Definition

An earnings event is valid for Cygnus only when the event has an explicit
availability timestamp or a deterministic conservative availability rule.

Baseline event fields:

- `ticker`
- `fiscal_period`
- `announcement_date`
- `announcement_time`: `before_open`, `after_close`, `during_market`,
  or `unknown`
- `availability_date`
- `reported_eps`
- `consensus_eps`
- `reported_revenue`
- `consensus_revenue`
- `guidance_signal`, when available
- source metadata and ingestion timestamp

Availability rules:

- Before-open announcements are available for same-date close selection.
- After-close announcements are available no earlier than the next trading
  date's close selection.
- During-market announcements are excluded from the baseline unless the source
  proves timestamp availability before the decision timestamp.
- Unknown announcement time is treated as after-close and delayed to the next
  trading date.
- Estimate revisions are available only on or after their vendor publication
  date, not the fiscal period date or later restated snapshot date.
- No future earnings calendar, actual results, next-day analyst revisions, or
  next-day price reactions may be used for a selection date.

## Signal Framework

All Cygnus signals are computed using data available at or before the as-of
close. The initial return convention is `weights_as_of_t`: holdings selected
from information available at close-of-day `t` are evaluated on `t+1` returns.

The first pass should be a transparent composite:

| Component | Direction | Candidate Definition |
|---|---:|---|
| EPS surprise | Higher is better | Standardized reported EPS versus consensus EPS |
| Revenue surprise | Higher is better | Reported revenue versus consensus revenue |
| Guidance quality | Higher is better | Positive explicit guidance or raised outlook |
| Event reaction | Higher is better | Post-event abnormal return over the first eligible close |
| Estimate revision breadth | Higher is better | Net upward analyst revisions after event availability |
| Estimate revision magnitude | Higher is better | Consensus EPS or revenue estimate change after event |
| Drift confirmation | Higher is better | Price above event reaction close and positive 5D/10D trend |
| Crowding or exhaustion penalty | Lower is better | Large pre-event run-up or extreme gap without follow-through |
| Liquidity and data quality | Required | Tradability and complete event/revision metadata |

Initial deterministic score:

```text
cygnus_score =
  0.25 * percentile(eps_surprise_z)
+ 0.15 * percentile(revenue_surprise_z)
+ 0.15 * percentile(event_reaction_abnormal_return)
+ 0.15 * percentile(revision_breadth)
+ 0.15 * percentile(revision_magnitude)
+ 0.10 * percentile(drift_confirmation)
+ 0.05 * guidance_bonus
- pre_event_runup_penalty
- failed_reaction_penalty
```

Every score component must be persisted so attribution can separate earnings
surprise, guidance, estimate-revision, and price-confirmation effects.

## Universe Definition

Baseline Cygnus should use the existing `data/universe.csv` equity universe to
avoid adding universe-governance risk in the first pass.

Eligibility filters:

- US long-only equities only.
- Price at as-of close >= `$5`.
- 20D median dollar volume >= `$20M`.
- At least 252 trading days of adjusted OHLCV history.
- Earnings event availability date must be known or conservatively inferred.
- Earnings event age must be within the configured entry window.
- Required event fields must be present for the selected signal variant.
- Exclude symbols with missing current close, stale prices, zero volume,
  impossible returns, or unresolved split/corporate-action anomalies.
- Benchmark remains `SPY`.

Later variants may evaluate a broader liquid universe because earnings-drift
opportunities may be sparse in a 201-name universe, but that should be a
separate research variant with explicit survivorship-bias controls.

## Entry Rules

Cygnus should generate holdings after each completed market day.

Baseline entry:

1. Build the event tape using only earnings events whose `availability_date` is
   less than or equal to trade date `t`.
2. Compute surprise, reaction, revision, guidance, and drift-confirmation
   features using only data available by `t`.
3. Require event age between 1 and 10 trading days after availability for new
   entries.
4. Require positive event quality:
   - EPS surprise > 0 or revenue surprise > 0; and
   - first eligible post-event reaction is non-negative or abnormal return is
     above peer median; and
   - no failed-reaction flag.
5. Rank eligible candidates by `cygnus_score`.
6. Select top 8-15 candidates.
7. Weight equal-weight or inverse-volatility capped weights, with cash allowed
   when fewer candidates qualify.

No intraday feed should be required for the baseline. Announcement-time handling
must be conservative when timestamps are missing.

## Exit Rules

Exit rules should be deterministic and position-level:

- Time stop: exit after 60 trading days from event availability.
- Minimum hold: prefer 10 trading days unless a risk stop or data-quality exit
  triggers.
- Revision-decay exit: exit when net estimate revisions turn negative after
  entry.
- Drift-failure exit: exit if price closes below the first eligible post-event
  close by more than 6%.
- Rank-decay exit: exit when current Cygnus rank falls below top 2x target
  count.
- Earnings-cycle exit: exit before the next earnings event if the current event
  thesis has aged out.
- Data-quality exit: exit when current price, event, or revision metadata fails
  eligibility.

First research variants:

- `cygnus_v0_event_reaction`: EPS/revenue surprise plus event reaction, no
  revision dependency.
- `cygnus_v1_revision_confirmed`: v0 plus upward estimate-revision breadth.
- `cygnus_v2_guidance_quality`: v1 plus explicit guidance signal where
  available, with missing guidance treated neutrally.

## Holding-Period Assumptions

Expected holding period: 10-60 trading days.

Backtest accounting:

- Use end-of-day selection on `t`; first return realized from `t` close to
  `t+1` close.
- Do not use the announcement day's close-to-close return unless the event was
  available before the selection decision timestamp.
- Apply transaction cost assumptions at entry and exit.
- Persist event date, availability date, entry date, event age, entry score,
  entry price proxy, exit date, and exit reason.

## Risk Controls

Research-only risk controls:

- Max gross exposure: `80%` baseline, with remaining weight held as cash.
- Max single-name weight: `10%`.
- Max sector weight: `30%` when sector data is available.
- Max positions: `15`; minimum diversified active basket: `5`.
- Volatility cap: downweight names with 20D realized volatility above `80%`
  annualized.
- Pre-event run-up penalty: penalize candidates with excessive 20D pre-event
  run-up unless post-event revisions confirm.
- Failed-reaction exclusion: exclude strong reported beats that sold off on the
  first eligible reaction window.
- Turnover guard: do not churn an existing Cygnus holding if it still satisfies
  hold rules and remains above rank-decay threshold.

These controls are research constraints only. They must not alter Polaris,
Orion, Lyra, Phoenix, paper execution, or live execution.

## Data Dependencies And Availability-Date Rules

Required:

- Adjusted daily OHLCV for universe symbols.
- Adjusted daily OHLCV for `SPY`.
- Existing `data/universe.csv`.
- Point-in-time earnings event data with announcement date and announcement
  time or a conservative availability rule.
- Point-in-time consensus EPS and revenue estimates as of the pre-event
  snapshot.
- Point-in-time post-event estimate revisions with publication dates.
- Trading calendar / available market dates.

Optional for richer attribution:

- Company guidance text or normalized guidance fields.
- Analyst revision counts by broker.
- Sector map for sector exposure.
- Earnings-calendar metadata for next-event exits.
- Existing regime/breadth artifacts for environment slicing.

Fail-closed data rules:

- Missing event availability date => use conservative next-trading-date
  availability only if announcement date is reliable; otherwise exclude.
- Missing pre-event consensus => no EPS/revenue surprise score for that event.
- Missing post-event revision data => revision-dependent variants mark the
  symbol ineligible; baseline event-reaction variant may continue with
  `revision_status: UNAVAILABLE`.
- Missing benchmark data => no SPY-relative or promotion-readiness claim.
- Missing optional guidance/sector/regime data => artifact status `PARTIAL`,
  not fabricated values.
- Vendor restatements must not rewrite historical availability without a
  recorded source snapshot or ingestion timestamp.

## Expected Artifacts

Research baseline outputs:

- `outputs/research/cygnus/<date>/cygnus_event_tape.parquet`
- `outputs/research/cygnus/<date>/cygnus_signal_frame.parquet`
- `outputs/research/cygnus/<date>/cygnus_rank_table.csv`
- `outputs/research/cygnus/<date>/cygnus_holdings.json`
- `outputs/research/cygnus/<date>/cygnus_backtest_summary.json`
- `outputs/research/cygnus/<date>/cygnus_decision_trace.json`
- `outputs/research/cygnus/<date>/cygnus_attribution_inputs.json`
- `outputs/research/cygnus/performance/cygnus_nav_series.csv`
- `outputs/research/cygnus/performance/cygnus_summary.json`

When promoted to the shadow framework, add:

- `outputs/shadow_candidates/<date>/caerus_cygnus.json`
- Cygnus row in `comparison.json`
- Cygnus row in `shadow_performance.json`
- Cygnus row in `shadow_evaluation.json`
- Cygnus panel in `promotion_readiness.json`
- Cygnus folder in feedback-loop artifacts:
  `outputs/shadow_candidates/<date>/cygnus/`

Every artifact should include:

- `schema_version`
- `trade_date`
- `strategy_id: caerus_cygnus`
- `strategy_slug: caerus_cygnus`
- `governance_label: RESEARCH_ONLY` or `SHADOW_ONLY`
- `execution_impact: NON_EXECUTIONAL`
- data coverage and freshness metadata
- event source and availability-rule metadata
- explicit unavailable/partial status reasons

## Integration Points

Existing systems to preserve:

- Polaris remains paper/control strategy.
- Orion and Lyra remain existing shadow candidates.
- Phoenix remains research-only until separately promoted.
- SPY remains benchmark.
- Promotion-readiness logic remains informational and non-executing.

Research-only integration:

- New module under `research/cygnus/` for event tape construction, feature
  engineering, scoring, backtest, and artifact writing.
- Optional CLI wrapper `scripts/research/run_cygnus_research.py`.
- No changes to `scripts/cron_execute.sh`, `scripts/cron_precompute.sh`,
  broker/order code, or paper/live execution code.

Shadow integration after research validation:

- Extend `research.shadow_tracking` to support dynamic strategy definitions
  rather than hard-coded Polaris/Orion/Lyra assumptions.
- Add Cygnus as `caerus_cygnus` only after comparison, performance,
  evaluation, feedback-loop, dashboard, and promotion-readiness readers
  tolerate additional strategy slugs.
- Update research registry tools so Cygnus can appear in review packets and
  promotion readiness without treating it as paper-eligible.

## Backtest Plan

Stage 1: offline event-tape validation

- Build a point-in-time earnings event tape with explicit source, ingestion,
  and availability metadata.
- Audit a sample of events across before-open, after-close, and unknown-time
  announcements.
- Confirm no event is selectable before its availability date.
- Generate empty-but-valid artifacts for dates without eligible events.

Stage 2: baseline strategy research

- Build features over the existing universe from the first date with reliable
  event and estimate history.
- Run `cygnus_v0_event_reaction`, `cygnus_v1_revision_confirmed`, and
  `cygnus_v2_guidance_quality`.
- Compare to SPY, Polaris, Orion, Lyra, and Phoenix when Phoenix history exists.
- Evaluate full-period and earnings-season-sliced performance.
- Report turnover, hit rate, average hold, drawdown, volatility, sector
  exposure, event-age distribution, and correlation to existing strategy daily
  returns.

Stage 3: robustness

- Walk-forward parameter sensitivity:
  - top N: 5, 8, 10, 15
  - entry window: 1-5, 1-10, 2-15 trading days after availability
  - max hold: 20, 40, 60 trading days
  - surprise threshold: positive, top-half, top-quartile
  - revision confirmation required/not required
- Test transaction costs at 10, 25, and 50 bps.
- Exclude earnings seasons one at a time.
- Confirm no result depends on future consensus, future revisions, or same-day
  unavailable announcement data.

Stage 4: research shadow dry run

- Generate Cygnus daily holdings to `outputs/research/cygnus/`.
- Reconcile daily holdings against prior day to estimate turnover.
- Build immutable holdings snapshots compatible with attribution tools.
- Keep outside `outputs/shadow_candidates/` until schema readers support the
  additional strategy slug and event metadata fields.

Stage 5: non-blocking shadow integration

- Add Cygnus to daily shadow outputs only after compatibility tests pass.
- Keep status `SHADOW_ONLY`.
- Do not include Cygnus in paper execution, live-vs-shadow reconciliation
  against Polaris, or capital allocation.

## Promotion-Readiness Plan

Cygnus should not be promotion-eligible from backtest alone.

Minimum evidence before any promotion discussion:

- 60 clean forward shadow trading days, including at least one earnings season
  if possible.
- No broken NAV chain.
- Daily holdings generated and attributable.
- Event availability metadata present for every selected position.
- Stable data availability and no unexplained stale artifacts.
- Positive excess return versus SPY and Polaris over relevant windows.
- Drawdown and turnover within predeclared limits.
- Low-to-moderate correlation with Polaris/Orion/Lyra and differentiated
  event-driver attribution.
- Operator review of worst 10 Cygnus trades and at least 20 selected earnings
  events.

Readiness should remain informational until a separate governance task approves
any capital allocation or execution change.

## Proposed Implementation File List

Research-only first pass:

- `docs/governance/fr_051_cygnus_research_spec.md`
- `research/cygnus/__init__.py`
- `research/cygnus/events.py`
- `research/cygnus/features.py`
- `research/cygnus/strategy.py`
- `research/cygnus/backtest.py`
- `research/cygnus/artifacts.py`
- `scripts/research/run_cygnus_research.py`
- `Tests/test_cygnus_events.py`
- `Tests/test_cygnus_features.py`
- `Tests/test_cygnus_backtest.py`
- `Tests/test_cygnus_artifacts.py`

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

- Earnings and estimate data can create severe look-ahead bias if availability
  dates are inferred incorrectly.
- Vendor consensus histories may be restated or overwritten unless snapshots
  are stored immutably.
- Small-universe event sparsity may create unstable results.
- Post-earnings drift can overlap with ordinary momentum; attribution must prove
  distinct event contribution.
- Surprise definitions differ by vendor and may not match market expectations.
- Guidance data may be incomplete, qualitative, or hard to normalize
  deterministically.
- Existing shadow tracking has hard-coded three-strategy assumptions.

Assumptions:

- Existing adjusted OHLCV cache is sufficient for first-pass price and reaction
  features.
- A reliable event/estimate source with publication or ingestion timestamps can
  be obtained before implementation.
- Long-only expression is mandatory.
- Missing guidance should be neutral, not bearish, unless a variant explicitly
  studies guidance absence.

Open questions:

- Which data vendor will supply point-in-time earnings estimates and revision
  histories with auditable availability dates?
- Should Cygnus enter immediately after event availability or wait for one
  additional close of confirmation?
- Should estimate revisions be required for all variants or only for
  revision-confirmed variants?
- How should after-close announcements on Fridays or market holidays be mapped
  to availability and first eligible reaction dates?
- Should guidance be parsed from text, consumed from normalized vendor fields,
  or deferred until the baseline event/revision strategy is validated?

## Current Canonical Boundary

Cygnus remains the canonical earnings / post-earnings drift research strategy.
The superseded FR-056 design draft is non-canonical and must not broaden Cygnus
into a generic price/factor drift sleeve without an explicit roadmap decision.
The FR-064 multi-asset framework is a separate portfolio research framework and
does not change Cygnus strategy identity, data requirements, or implementation
status.

---

## Addendum 2026-06-10 — Implementation Wave 1 Plan (v0 Event-Reaction)

This addendum extends the canonical spec per `CURRENT_RESEARCH_ROADMAP.md`
Section 6 (extend, do not fork). It resolves the open questions blocking
implementation and sequences a buildable first wave. It changes no execution,
broker, cron, registry, or paper/live behavior.

### A1. Conflict B resolution (recommendation for owner decision)

Affirm the canonical FR-051 definition: Cygnus is **earnings drift**, not
generic price/factor drift. Retire FR-056 formally in
`CURRENT_RESEARCH_ROADMAP.md` Section 4. Rationale: the edge thesis (market
underreaction to a discrete, timestamped information event) is what makes
Cygnus a distinct return stream from the Polaris/Orion momentum family; a
generic drift sleeve would reproduce the 97%+ correlation problem documented
in FR-063.

### A2. Data source decision (resolves the primary open question)

Wave 1 uses **SEC EDGAR as the sole event source** and builds only
`cygnus_v0_event_reaction`. Rationale:

- EDGAR acceptance timestamps are point-in-time by construction, free, and the
  repo already operates EDGAR ingestion (`edgar_ingestion.py`,
  `alpha_stack/datastore/sec_edgar.py`, the insider-activity overnight agent).
- Every consensus-estimate vendor evaluated so far either restates history or
  lacks auditable availability dates at acceptable cost. Deferring the
  vendor decision unblocks the venue test now; revision-dependent variants
  (v1/v2) remain gated on the vendor question.

Event definition for Wave 1:

- Event = 8-K filing with Item 2.02 (Results of Operations) and/or the
  associated earnings exhibit (EX-99), keyed by EDGAR `acceptanceDateTime`.
- Availability rule: acceptance before 09:00 ET => available for same-date
  close selection; acceptance 09:00-16:00 ET => treated as during-market and
  available next trading date (conservative); acceptance after 16:00 ET =>
  next trading date. Friday/holiday acceptances map to the next trading date.
- 10-Q/10-K filings without a preceding 8-K event within 5 trading days form a
  secondary, lower-weight event class (`filing_only`).

### A3. Wave 1 signal substitutions (no consensus dependency)

The v0 composite from the canonical spec is implemented with these
substitutions, keeping component persistence requirements unchanged:

- `eps_surprise_z` -> **deferred** (vendor-gated); weight redistributed.
- `revenue_surprise_z` -> **revenue YoY acceleration**: YoY revenue growth from
  the filed XBRL figure versus the prior filed quarter's YoY growth (both
  PIT-safe filed values).
- `event_reaction_abnormal_return` -> first eligible close-to-close return
  minus SPY return (unchanged from canonical definition).
- `drift_confirmation`, run-up penalty, failed-reaction penalty: unchanged.

Wave 1 composite:

```text
cygnus_v0_score =
  0.40 * percentile(event_reaction_abnormal_return)
+ 0.25 * percentile(revenue_yoy_acceleration)
+ 0.20 * percentile(drift_confirmation)
+ 0.15 * filing_quality_bonus   # on-time filer, 8-K + exhibit present
- pre_event_runup_penalty
- failed_reaction_penalty
```

### A4. Pre-registered pass/fail criteria (frozen before first backtest)

| Criterion | Threshold |
|---|---|
| Rank IC of v0 score vs 10D forward returns | >= 0.02, t-stat >= 2 |
| Rank IC vs 20D and 60D forward returns | positive, monotone-ish decay |
| Net IR vs SPY (25 bps costs) | >= 0.30 |
| Excess-return correlation vs Polaris excess | <= 0.50 |
| Event coverage | >= 60% of universe earnings events captured by tape |
| Cost sensitivity | thesis survives at 50 bps |

A failing variant is reported and shelved, not re-tuned until the criteria
pass. Walk-forward split: tune on events through 2021, validate 2022-2024,
holdout 2025-forward run once.

### A5. Wave 1 sequencing and effort

1. **Stage 1 (event tape, ~1 week):** EDGAR 8-K Item 2.02 tape for the
   existing 201-name universe, 2016-present, with acceptance-timestamp audit
   per canonical Stage 1. The small universe is acceptable for Wave 1 because
   the venue is large-cap; the FR-067 Stage 0 PIT machinery lifts this later.
2. **Stage 2 (v0 strategy + backtest, ~1-2 weeks):** canonical Stage 2 with
   the A3 composite, Polaris/Orion/Lyra/SPY comparisons, and the A4 criteria
   table as the only headline output.
3. **Stage 3+ (robustness, dry run, shadow):** unchanged from canonical spec.
   Shadow integration remains gated on the dynamic-strategy-slug prerequisite
   shared with FR-067.

### A6. Updated open questions

Resolved by this addendum: event source (EDGAR), v0 surprise definition
(filed-revenue acceleration), entry timing (conservative availability rules
above). Still open: consensus/revision vendor for v1/v2; guidance parsing
(deferred per canonical spec); whether `filing_only` events earn a permanent
place or are dropped after Stage 2 attribution.

### A7. Stage 2 v0 verdict (2026-06-10)

Cygnus v0 Stage 2 validation verdict: **FAIL**. The v0 event-reaction variant is
shelved and must not be re-tuned. The 2025-forward holdout remains untouched and
preserved; it was not run for this verdict.

| Criterion | Result | Verdict |
|---|---:|---|
| Rank IC of v0 score vs 10D forward returns | IC 0.0318, t-stat 1.59 | FAIL — t-stat below 2 |
| IC 20D/60D decay | positive decay profile | PASS |
| Net IR vs SPY at 25 bps | 0.44 | PASS |
| Excess-return correlation vs Polaris proxy | 0.043 | PASS |
| Event coverage | 1.05 | PASS |
| Cost sensitivity at 50 bps | IR -0.32 | FAIL |
| Overall | 4/6 | FAIL |

The tune window also failed, so the failure is not a validation-only anomaly.
Cygnus v1 is gated on EPS-surprise / consensus data with auditable point-in-time
availability. Future diagnostics may compute Newey-West or date-clustered
t-statistics to better characterize event clustering, but that diagnostic is
optional and does not change the v0 verdict.
