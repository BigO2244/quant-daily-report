# HYP-2026-013 — AI Power/Grid Commitment Events

Governance label: `RESEARCH_ONLY`

Execution impact: `NON_EXECUTIONAL`

Canonical lineage: this is a narrow, timestamp-aware event family within the
existing Caerus Cassiopeia / FR-052 event-driven research candidate. It does
not create, rename, activate, promote, or alter a canonical strategy, Shadow
lane, allocation, broker, scheduler, or production behavior.

## Claim

When an official utility, ISO/RTO, PUC, FERC, or issuer document first makes a
new AI/data-center electricity-load commitment public and directly identifies a
liquid listed economic exposure, an equal-weight long-only basket of those
exposures will earn positive factor-adjusted, transaction-cost-net returns over
the following 20 trading days relative to predeclared same-industry controls.

## Initial classification

`ALPHA_CANDIDATE`

## Economic mechanism

- Large AI data-center load is lumpy, geographically constrained, and can
  require generation, transmission, substation, cooling, or service investment.
- Market participants may react first to the developer or hyperscaler and only
  gradually incorporate the contracted load into directly exposed utilities and
  named infrastructure counterparties.
- Official project and regulatory records are costly to assemble and map, while
  their publication time can be audited; the return claim is therefore about
  delayed incorporation after public availability, not private information.
- Caerus can capture only direct, named, public exposures at the next regular
  session after a conservative availability rule. Broad AI, power, industrial,
  or robotics thematic ownership is not a substitute.
- A result explained by general AI enthusiasm, sector beta, rates, commodity
  shocks, generic utility performance, or momentum is not the claimed alpha.

## Prediction

- Expected sign: positive for directly named public exposures after a qualifying
  commitment becomes available.
- Forecast horizon: 20 trading days after next-regular-session-open entry.
- Expected decay: strongest on days 2-20 after availability and substantially
  weaker by day 60.
- Applicable universe: PIT-active US common equities with price >= `$5`,
  trailing 20-day median dollar ADV >= `$10M`, a resolved effective-dated
  security identity, and a qualifying direct exposure.
- Conditions where it should be strongest: explicit load magnitude or capacity
  requirement, binding/interconnection/service-agreement language, and a named
  issuer whose economic role is specified in the same official record.
- Conditions where it should fail: speculative plans, unnamed beneficiaries,
  generic AI commentary, a pre-announced project without a new commitment,
  complete same-day repricing, or an event driven only by broader rates or
  sector moves.

## Point-in-time data contract

- Source: immutable official records from utility/ISO/RTO/PUC/FERC public
  releases or dockets, and issuer SEC 8-K filings only when the qualifying
  commitment is identified in the original payload; canonical FR-068 PIT
  identity/membership; observed price/liquidity panel; common-factor and sector
  controls.
- Observation timestamp: source publication timestamp when supplied; otherwise
  official publication date with the time explicitly unknown. The raw source
  URL, payload hash, issuer/exposure mapping, and amendment/supersession lineage
  must be retained.
- Availability timestamp: exact source publication time when supplied. If only
  an official date exists, the event is available after that date's regular
  market close and first tradable at the next regular-session open. SEC filings
  use EDGAR acceptance time, but an acceptance timestamp does not substitute for
  an absent qualifying event in the original payload.
- Security identifier: effective-dated security ID joined from issuer CIK or
  named legal entity. Ticker is display-only. A supplier, contractor, or peer
  is eligible only if the official record itself names the issuer and economic
  role; inferred or later-known relationships fail closed.
- Delisting and corporate-action handling: resolve membership and identity at
  each event date; retain delisted names through their last observed price;
  report both frozen terminal-return sensitivity scenarios if a return evaluator
  is later authorized.
- Missing-data rule: fail closed on unknown official publication date, unresolved
  named exposure, unavailable source payload, unverified AI/data-center load
  relation, unclear event novelty, absent PIT membership, stale price, or missing
  factor/sector control. Unknown is never scored as neutral.
- Known coverage limitations: utility/ISO/PUC/FERC publication systems are
  fragmented; dockets can be revised; some commitments are confidential; named
  equipment suppliers are rare; EIA current-vintage controls are context only
  and are not an event source or a historical-vintage substitute.

## Frozen event, signal, and portfolio

- Qualifying event: an official record must state a new or amended data-center
  or AI-compute load commitment, interconnection/service agreement, or binding
  grid approval; identify the location or serving system; and directly identify
  the listed issuer's role as load-serving utility, regulated asset owner,
  generation/transmission developer, or equipment counterparty. A bare award,
  press rumor, generic capacity forecast, or unnamed load is ineligible.
- Event novelty: the record must contain a source-specific event identifier and
  be the first retained public record for that commitment. Corrections,
  withdrawals, and superseding records link to the original and invalidate it
  when they remove the qualifying condition.
- Primary signal: equal weight across all qualifying direct-exposure events
  first available during the preceding five trading days, capped at 10 names and
  10% per name. Multiple valid events for one issuer combine but do not increase
  its cap. Unfilled weight remains cash.
- Entry and exit: decide after each close using only then-available records;
  enter at the next regular-session open; hold 20 trading days. No same-day
  reaction, price-momentum filter, optimizer, stop-loss, sector override, or
  dynamic reweighting is allowed in the primary variant.

## Baselines and risk model

- Primary investable baseline: on each candidate event date, equal weight in
  PIT-eligible public issuers in the candidate's predeclared exposure stratum
  (regulated utility or electrical/grid equipment), excluding the named event
  issuer, with identical next-open entry, hold, cash, caps, and costs.
- Simple signal baseline: hold the directly named issuer for the same window
  after any official energy/grid record, irrespective of AI/data-center linkage
  or commitment language.
- Factor controls: daily `MKT-RF`, `SMB`, `HML`, `RMW`, `CMA`, `UMD`, a
  predeclared low-volatility/BAB proxy, sector returns, prior 20-day return,
  rates, natural-gas and power-price controls where available. Controls are
  evaluation-only.
- Portfolio-utility comparator: matched-date Polaris, Orion, Lyra, and SPY
  returns, reported separately and never used for tuning.

## Frozen experiment

- Primary metric: annualized factor-adjusted intercept of the cost-net candidate
  minus primary-baseline calendar-time return.
- Secondary diagnostics: 1/5/10/20/60-day residual return decay; event-count,
  issuer, location, year, and source concentration; raw/SPY-relative return;
  bucket monotonicity only if at least 100 qualified events exist; factor/sector
  exposures; turnover; drawdown; capacity; same-day gap; and overlap/correlation
  with existing research sleeves.
- Walk-forward design: discovery/source-contract audit from 2018-01-01 through
  2021-12-31; frozen source taxonomy and exposure strata; expanding annual
  evaluation from 2022-01-01 through 2024-12-31. No event source, mapping rule,
  threshold, or source-priority change may use later returns.
- Untouched challenge period: 2025-01-01 through 2026-06-30. It is read once
  only after the raw event-tape PIT audit, all discovery/validation results, and
  permitted variants are permanently recorded.
- Cost and capacity assumptions: next-open equity execution; 15 bps per side
  base and 30 bps per side stress. Each order must be <= 5% of trailing 20-day
  dollar ADV at `$100K`, `$1M`, and `$10M`; failed names are reported, not
  silently removed.
- Maximum variants: 3 total, including the primary. The only alternatives are a
  10-day hold and records with an exact publication time. Different event types,
  exposure definitions, thresholds, or holding periods require a new hypothesis.
- Multiple-testing correction: Romano-Wolf/max-T stationary-block bootstrap at
  one-sided `alpha=0.10` across the three variants, with all records for the
  same project/issuer-month kept in the same resample block.

## Pass criteria

Further work is justified only if the primary variant has positive 2022-2024
factor-adjusted net alpha with an adjusted one-sided 90% lower confidence bound
above zero, remains positive at 2x costs, has at least 100 independent qualified
events and 30 issuers, no issuer contributes more than 20% of active return, no
year more than 50%, and supports `$1M` under the 5%-ADV rule. A later challenge
read must independently satisfy the same directional and concentration gates.
Passing authorizes only continued research or a separately approved forward
observation request.

## Kill criteria

Kill or park if official publication/availability lineage cannot be proven;
fewer than 100 independent events survive; issuer/exposure mapping is not
directly documented; validation or challenge net alpha is non-positive;
corrected significance fails; 2x costs erase the result; rates, sector,
commodity, or momentum controls explain it; a location/issuer/year dominates;
or a permitted hold variant reverses the sign. Any return-informed taxonomy or
challenge-period tuning voids the experiment.

## Cheapest honest next test

Construct a no-return, source-audited sample of 30 official records across at
least 10 issuers and 2019-2024. Require 100% source-payload retention, official
publication-date lineage, direct named-exposure mapping, event novelty review,
and correction lineage. The first run is a data/provenance gate only; it may not
read returns, the 2025+ challenge period, or create a trading signal. Failure to
assemble this sample stops the historical evaluator.

## Freeze record

- Frozen by: Brett Olson, CIO, via explicit `FREEZE HYPOTHESIS, then RUN EXPERIMENT`; drafted by Codex.
- Frozen at: 2026-07-30, America/New_York.
- Spec hash: `sha256:121baabdbeb3c00c9de7b875a621b4ab620352cc910057d16450af74140104ab`.
- Code hash: no HYP-2026-013 evaluator exists at freeze; repository baseline pending the frozen data-gate adapter.
- Data snapshot/hash: `NOT_ACQUIRED`; source-audited event tape and PIT certification are part of the frozen experiment.
