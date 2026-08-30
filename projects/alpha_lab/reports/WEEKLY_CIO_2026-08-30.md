# Alpha Lab Weekly CIO Packet — 2026-08-30

## Executive decision

**Investment question:** Can a large, positive, volume-confirmed reaction to an
early industry reporter predict delayed gains in liquid, same-industry peers
that have not yet reported?

**Selected candidate:** Industry earnings-information diffusion, a long-only
event sleeve that treats the reporter's observable price/volume reaction as the
information shock and buys only not-yet-reporting peers.

**Economic mechanism:** Investors and analysts process an issuer's earnings
release first as company-specific news. Economically similar peers may reprice
more slowly because coverage, attention, and reporting calendars are staggered.
The hypothesis is not that earnings announcements drift in their own shares;
it is that a reporter's revealed industry information diffuses to eligible
peers after the reporter is already tradable.

**Closest prior work:** This sits between HYP-2026-002 earnings revisions and
HYP-2026-005 supply-chain diffusion but does not reuse either blocked input.
It replaces unavailable analyst-consensus history with the reporter's causal
market reaction and replaces a licensed customer/supplier graph with the
event-time SEC SIC carried in the original filing header. It also differs from
EXP-2026-0008 and the August generic reversal study: its causal input is a
public corporate event and it follows a positive shock rather than buying a
generic loser portfolio.

**What was tested this cycle:** One read-only data-provenance falsifier. A
deterministic sample of 30 retained Item 2.02 source payloads, representing 29
unique accessions across original-stream partitions 0, 156, and 313, was read
from the canonical GCP archive. The sample spans 2011-01-05 through 2026-06-24.
All 30 payloads exposed exactly one four-digit SIC in the SEC header and all 30
header CIKs matched the immutable inventory CIK. The duplicate accession is
consistent with the archive's documented retention of distinct historical
payloads and produced the same usable identity result. No source or artifact
was changed.

**What was not tested:** No price, volume, forward return, protected outcome,
challenge window, holdout, threshold, horizon, portfolio, or cost result was
read or computed. No hypothesis was frozen, no experiment was run, and no
formal trial was consumed. Alpha remains entirely unproven.

**CIO verdict:** The last identified pre-freeze data check passed. The candidate
is sufficiently distinct, investable in long-only form, and cheap to falsify
with existing data. It should advance only to an owner-reviewed freeze, not to
an experiment or any strategy lifecycle state.

**Next decision:** Brett may record `FREEZE HYPOTHESIS` for the proposed
candidate contract below. A later, separate durable `RUN EXPERIMENT` decision
would still be required before any outcome test. Declining or deferring the
freeze leaves the candidate in discussion with no further computation.

## Authority and institutional-memory audit

This was a strategic, research-only cycle. The local checkout was clean at
start, on `project/alpha-lab`, at
`7afe3a3f11a47a566f80e236db9bbb7c1f438c8e`, exactly matching
`origin/project/alpha-lab`. No production, registry, scheduler, broker,
allocation, Paper, Shadow, or Live state was touched.

The audit reconciled the project contracts, all current Alpha Cards,
Experiment Ledger, Model Compendium, Strategy Backlog, current roadmap,
strategy registry, Caerus doctrine, and the legacy-intake directory. The
legacy-intake directory contains no completed intake packet; the short-MA
legacy intake remains pending. It also inspected the clean canonical VM
checkout and finalized evidence under
`caerus-vm:/mnt/disks/alpha-lab/alpha-lab-project`, performed read-only discovery
across the Caerus workspaces, reviewed the independent Ava project, and used
recent Codex task summaries where available.

The canonical global ledger expected at
`caerus-vm:/mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab/ledger/research_events.v1.jsonl`
does not exist. Gate-A staging exists, and the local Phase 1 worktree contains
an owner-ratification packet, but absence of registration is not absence of
research. The missing canonical ledger is a governance/reconciliation defect,
not a finding that the completed research below never happened.

## Opportunity map

### Mechanisms with surviving information

- Broad momentum selection retains weak positive cross-sectional evidence, but
  independent factor-adjusted, implementation-net alpha is still unproven.
- The August generic reversal study found that some long legs and short-horizon
  gross cells could be positive before trading costs. This is a mechanism
  fragment, not a model success: turnover destroyed the spreads and the weak
  short leg did not supply a viable long/short engine.
- The HYP-2026-014 source bridge showed that official project-stage and
  financial-conversion evidence can sometimes be reconciled causally. Its
  historical panel was far too sparse for the frozen test.
- HYP-2026-013 qualified one issuer event source but did not establish a broad,
  source-audited event tape.

### Negative or parked evidence

- Residual momentum, stock-specific seasonality, and short-horizon reversal
  each finished their frozen validations without usable evidence.
- Smart concentration passed 0 of 10 rolling joint gates, did not improve the
  Orion drawdown, and did not earn a Shadow or Paper lane.
- The August 64-variant own-MA/sector-relative mean-reversion study had no
  positive stress-net result; the best reported stress-net outcome was
  -7.75%, and short-horizon gross improvements came with 32.7x to 127.5x
  annualized turnover.
- HYP-2026-014 produced only four comparable observations across two utilities
  and two jurisdictions versus frozen minimums of 60 observations, 10
  utilities, and five jurisdictions. No returns were read.
- Ava's separate Step 11 competition call-spread screen ended with 0 of 9
  cases passing and natural-price edge from -$0.5173 to -$0.1817 per normalized
  share. Ava is an independent project, so this is discovery evidence only,
  not an Alpha Lab result.

### Untested or data-blocked mechanisms

- Earnings revisions, historical options information, supply-chain diffusion,
  cross-asset trend, tone surprise, net payout, asset growth, and the broad
  HYP-2026-013 event tape remain blocked or gated exactly as recorded in the
  canonical roadmap and project ledgers.
- Insider clusters have rebuilt original-XML inputs but still require their
  frozen gate and amendment-supersession treatment; this cycle did not rerun it.
- Forward options observations and vendor samples do not satisfy the frozen
  exchange-grade HYP-2026-004 contract.

### Complementary directions

- Reporter-to-peer earnings diffusion is event-driven and can diversify a
  continuous momentum rank if its incremental effect survives industry and
  momentum controls.
- Options activity may be more defensible as a forward volatility/attention
  forecast than as a directional alpha signal, but the current history is too
  short for a cheap formal result.
- A future reversal thesis would need a distinct causal forced-flow event and
  sticky implementation, not another parameter search over the generic daily
  loser rank.

### Repeated constraints

- Exact availability timestamps, event semantics, effective-dated identity,
  true delisting treatment, corporate actions, and survivorship controls.
- Historical panel density and breadth, especially for event-source projects.
- Turnover, short economics, spread/impact stress, and capacity.
- Factor, industry, overlap, and multiple-testing controls.
- Incomplete global ledger and Model Compendium registration across otherwise
  completed work.

## Candidate screen

### 1. Industry earnings-information diffusion — selected

- **Mechanism:** Delayed peer repricing after a positive, volume-confirmed
  earnings reaction by an early reporter.
- **Why it may persist:** Staggered reporting calendars, segmented analyst
  coverage, and issuer-first attention can delay the industry inference.
- **Lineage:** Adjacent to HYP-2026-002 and HYP-2026-005; explicitly distinct
  from analyst revision history, relationship-graph diffusion, PEAD in the
  reporter, and generic reversal.
- **Novelty classification:** `NEW_MECHANISM`.
- **Portfolio role:** `ALPHA`; a sparse, long-only Cassiopeia-style event sleeve.
- **Correlation expectation:** Low-to-moderate positive correlation with raw
  momentum because the trade follows good news, but materially lower overlap
  if entries are event-gated and short-held. This must be measured, not assumed.
- **Data feasibility:** Passed for a cheap falsifier. The canonical archive has
  313,449 hydrated original 8-K/8-K-A filings with parsed acceptance timestamps;
  the observed-price asset has 21,840,452 PIT-certified price/volume rows; this
  cycle's 30-payload audit found usable event-time SIC and matching issuer CIK
  in every retained payload examined. No paid data is needed for the first test.
- **Capacity:** Expected to be moderate-to-high in the liquid equity universe,
  constrained by the number of not-yet-reporting peers, event clustering, and
  sector concentration rather than by borrow.
- **Simplest investable baseline:** At the next causal regular-session entry,
  equally weight liquid, not-yet-reporting four-digit-SIC peers after a positive
  reporter reaction that is large on both return and abnormal volume; hold for
  one predeclared short horizon or exit before the peer's own report. Negative
  reporter shocks create no long position.
- **Cheapest honest falsifier:** One frozen historical pass using the existing
  event tape and PIT panel, compared with industry, raw-momentum, and
  reporter-only baselines, with predeclared overlap, liquidity, cost, and
  multiplicity controls. No parameter grid.
- **Kill condition:** Park if causal peer eligibility cannot be formed at scale,
  if the event count or four-digit-SIC peer breadth misses a frozen coverage
  floor, or if the predeclared net incremental effect is non-positive after
  industry/momentum controls and stress costs.

### 2. Turnover-controlled long-only generic reversal — screened out

- **Mechanism:** Temporary non-event price pressure mean-reverts after a large
  drawdown.
- **Why it may persist:** Liquidity demand and mechanical selling can create
  short-lived overshoot, but the August study did not isolate those causes.
- **Lineage:** EXP-2026-0008 and the completed August 64-variant own-MA and
  sector-relative study.
- **Novelty classification:** `DUPLICATE_REJECT`; weekly rebalance, sticky
  membership, or another holding-period choice would be post-result parameter
  repair unless tied to a newly observed causal event.
- **Portfolio role:** Potential `ALPHA` diversifier to momentum, if a distinct
  forced-flow trigger were later supplied.
- **Correlation expectation:** Negative to momentum by construction; likely
  market- and size-sensitive without stronger controls.
- **Data feasibility:** Existing PIT prices are adequate. Data is not the
  blocker.
- **Capacity:** Superficially high in liquid equities, but the observed turnover
  and crowding make net capacity poor.
- **Simplest investable baseline:** Long the most extreme liquid drawdown names,
  broad-market hedged, with one fixed formation and holding horizon.
- **Cheapest honest falsifier:** Already supplied by the August study for the
  generic family: every one of 64 variants failed stress-net.
- **Kill condition:** Met for the generic formulation. Reconsider only with a
  genuinely new, timestamped forced-flow mechanism, not a neighboring rank or
  horizon.

### 3. Options activity as a realized-volatility forecast — deferred

- **Mechanism:** Concentrated option demand may reveal informed uncertainty or
  hedging pressure that reaches realized volatility even when its directional
  sign is unreliable.
- **Why it may persist:** Volatility information can be dispersed across strikes
  and maturities and is costly to arbitrage perfectly.
- **Lineage:** HYP-2026-004 forward proxy, the 2026-08-19 ORATS sample audit, and
  the 2026-08-21 London Strategic Edge fit review.
- **Novelty classification:** `NEW_MECHANISM`; it changes the target from
  underlying direction to volatility and must not rewrite HYP-2026-004.
- **Portfolio role:** `PROTECTION` or `DIVERSIFIER`; possible input to risk
  budgeting, never execution from this research cycle.
- **Correlation expectation:** Low directional correlation to the existing
  momentum stack; likely positively related to market-volatility regimes.
- **Data feasibility:** Forward collection can support later observation. The
  available samples do not certify broad historical bid/ask, condition,
  sequence, and open-interest lineage.
- **Capacity:** High as a forecast if used only to modulate liquid exposure;
  direct option implementation capacity was not assessed.
- **Simplest investable baseline:** Reduce or hedge exposure when a predeclared
  cross-sectional option-activity concentration measure predicts elevated
  subsequent realized volatility.
- **Cheapest honest falsifier:** Accumulate a minimum predeclared forward panel,
  then compare the forecast with underlying-only volatility baselines. This is
  not cheap enough for the current weekly cycle.
- **Kill condition:** Park if the forward panel lacks stable field semantics or
  the signal adds no out-of-sample volatility information beyond underlying
  returns and standard volatility measures.

## Freeze-ready candidate packet

If Brett authorizes a freeze, the durable record should predeclare one positive
reporter-shock signal, four-digit SEC SIC peer membership as observed at the
filing, next-regular-session causal entry, exclusion of peers that have already
reported, one holding/peer-report exit rule, one liquidity floor, one net-cost
assumption plus one stress cost, event-overlap treatment, an industry return
baseline, a raw-momentum baseline, a reporter-only baseline, a frozen coverage
floor, and one primary net incremental-return decision rule. Negative reporter
events should be descriptive only because the investable baseline is long-only.

The freeze must count the whole reporter-threshold/volume-threshold/holding-rule
family as one hypothesis family. No grid or post-outcome tuning is allowed.
The data-build stage must fail closed before returns if SIC peer coverage,
event-time issuer mapping, causality, or true return treatment misses the frozen
gate. The challenge period remains sealed until its separate governance gate.

## Exact reconciliation queue

No item below was imported automatically. The queue distinguishes missing
registration from missing research.

1. **Canonical global ledger:** After owner ratification of the Phase 1 Gate-A
   control plane, create or publish the canonical append-only ledger at
   `caerus-vm:/mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab/ledger/research_events.v1.jsonl`
   from the staged, validated path. Do not backfill by inference or bypass the
   ratification packet in
   `/Users/brettolson/Documents/Caerus/alpha-lab-phase1-20260822/`.
2. **HYP-2026-014 committed-load/rate-base evidence:** Register the completed
   source bridge and parked historical-panel feasibility outcome in the global
   ledger and Model Compendium without assigning a return result or promotion
   meaning. Authoritative evidence:
   `caerus-vm:/mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab/data_spine/committed_load_rate_base_historical_panel/20260811T202627Z-1c8fefad34c0/data/audit_summary.json`.
   Preserve the observed 4/2/2 coverage against the frozen 60/10/5 gates and
   the fact that no returns were read.
3. **Orion smart concentration:** Register as completed external/legacy Caerus
   research, not as an Alpha factory trial: 0/10 rolling joint passes, 38.45%
   full-period CAGR versus Orion's 38.74%, unchanged -55.95% maximum drawdown,
   and no Shadow/Paper activation. Authoritative Git evidence is commit
   `63b26b1` in
   `/Users/brettolson/Documents/Caerus/quant-daily-report-main/.git`.
4. **August generic mean reversion:** Register as completed external/legacy
   research with a negative stress-net verdict and preserve all 64 variants,
   not only the best row. Authoritative report:
   `/Users/brettolson/Documents/Caerus/local-formbook-backtest-20260827/OUTCOME_BACKTEST_RESULTS.md`,
   SHA-256
   `72c8b2e7b71ca2249df24e2e672a60df39af5bcd9dd5cd2e058a5ce2936d21d6`.
   Preserve the formation report SHA-256
   `0a1bf11eb604415aab188e4b8d09d5f17f594c0ca2ca3c524b9a9e91dcf57ebf`,
   summary SHA-256
   `cfc93cd88481f97094fc707adc09e8eca16a5bc11c2fc48b63f523c9caa0d123`,
   and manifest SHA-256
   `f80635920871f126369535f09478b99644c15dbcf85cae098799c22330299846`.
5. **Ava Step 11:** Do not import automatically. Brett or the designated
   research governor should decide whether this independent project is relevant
   enough for a completed legacy-model intake or should remain an explicitly
   excluded external project. Exact evidence:
   `/Users/brettolson/Documents/ChatGPT/Ava - AI Alpaca Trading Agent/research/reports/step11_result_v1.json`
   and
   `/Users/brettolson/Documents/ChatGPT/Ava - AI Alpaca Trading Agent/AVA_DEPRECATION_HANDOFF.md`.

## Authoritative evidence paths

- Weekly packet:
  `/Users/brettolson/Documents/Caerus/alpha-lab-project/projects/alpha_lab/reports/WEEKLY_CIO_2026-08-30.md`
- Original 8-K/8-K-A bundle manifest:
  `caerus-vm:/mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab/data_spine/sec_original_filings_stream/20260722T212948Z-8bec6cab476f/manifest.json`
  (`bundle_hash=25f7cdf591a1f80339309b0ca1a2c5abc18a01529fca9e3d7e3eb004dcfd7ad4`)
- Earnings-event readiness:
  `caerus-vm:/mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab/provider_readiness/pit_earnings_events_v1.json`
- PIT price/volume readiness:
  `caerus-vm:/mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab/provider_readiness/pit_observed_prices_v1.json`
- Canonical current discussion record:
  `/Users/brettolson/Documents/Caerus/alpha-lab-project/projects/alpha_lab/CURRENT_STATE.md`
  and
  `/Users/brettolson/Documents/Caerus/alpha-lab-project/projects/alpha_lab/STRATEGY_BACKLOG.md`

**Primary status: `CANDIDATE_READY_FOR_FREEZE`**
