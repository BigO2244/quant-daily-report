# HYP-2026-005 — Supply-Chain Shock Diffusion

Governance label: `RESEARCH_ONLY`

Execution impact: `NON_EXECUTIONAL`

Canonical lineage: this is an Alpha Lab research hypothesis with no assigned
canonical Caerus strategy identity. It does not create a registry entry, name a
new strategy, or alter Cygnus, Cassiopeia, or any existing sleeve.

## Claim

When a major customer reports a positive point-in-time earnings/revision shock,
a broad long-only basket of liquid public suppliers selected from relationships
known before the shock and weighted only by disclosed revenue dependence will
earn positive factor-adjusted, transaction-cost-net returns over the next 10
trading days relative to otherwise comparable suppliers and to holding the
shocked customers themselves.

## Initial classification

`ALPHA_CANDIDATE`

## Economic mechanism

- Investors process direct issuer news before tracing second-order demand through
  customer/supplier relationships, especially when suppliers are smaller or
  covered by different analysts.
- Public revenue-dependence disclosures quantify which downstream firms should
  benefit, while relationship uncertainty and graph maintenance impose research
  costs.
- Caerus can propagate only publicly available customer shocks across one-hop,
  effective-dated relationships and enter suppliers after the upstream event.
- Industry demand, common factors, customer momentum, commodity shocks, or a
  supplier's own news can create false lead-lag and must be separated.

## Prediction

- Expected sign: positive for suppliers with the highest positive dependency-
  weighted customer shocks.
- Forecast horizon: 10 trading days from conservative next-session entry.
- Expected decay: begins after customer repricing, strongest over days 2-10, and
  weak or absent by day 30.
- Applicable universe: PIT-active US common-equity suppliers with price >= `$5`,
  trailing 20-day median dollar volume >= `$10M`, and at least one one-hop public
  customer relationship with a known-before-event effective date and revenue
  dependence >= 10%.
- Conditions where it should be strongest: large positive customer surprise,
  high disclosed supplier dependency, recent relationship confirmation, and no
  offsetting supplier-specific or input-cost news.
- Conditions where it should fail: relationship data backfilled after the fact,
  diversified customers with weak pass-through, already synchronized industry
  repricing, supplier capacity constraints, negative margin effects, or the
  supplier's own contemporaneous event.

## Point-in-time data contract

- Source: effective-dated supply-chain relationships and known revenue dependency
  from a vendor such as FactSet Revere, accepted only if historical publication/
  effective-date lineage is preserved; original 10-K/10-Q major-customer
  disclosures for audit; PIT earnings/estimate data under the HYP-2026-002 data
  standard; canonical FR-068 PIT security/universe artifacts; approved adjusted
  OHLCV/corporate actions; SEC filing/earnings event tape for supplier-news
  exclusions.
- Observation timestamp: original disclosure/publication time for each
  relationship and dependency value; customer shock source time; supplier-news
  source time; official daily price close.
- Availability timestamp: later of source publication and Caerus ingestion.
  Relationship existence, direction, confidence, and dependency must be frozen
  before the customer shock. The trade is eligible only at the next regular-
  session open after all customer-shock inputs are public.
- Security identifier: effective-dated security IDs on both customer and supplier
  nodes, joined through CIK/CUSIP/FIGI lineage; ticker is display-only.
- Delisting and corporate-action handling: retain failed/delisted nodes; resolve
  both endpoints on every event date; preserve mergers, spin-offs, renames, and
  relationship termination dates; adjust return series without rewriting graph
  history.
- Missing-data rule: fail closed on unknown relationship discovery/publication
  date, direction, endpoint identity, dependency percentage, customer event time,
  PIT membership, supplier-news coverage, or price/corporate-action lineage.
  Later vendor knowledge may not be projected backward.
- Known coverage limitations: vendors often expose effective relationships but
  not when the database first knew them; private customers/suppliers; incomplete
  dependency percentages; stale terminated links; changing segment definitions;
  one customer mapping to several securities; and sparse large-company
  disclosures.

## Frozen event, signal, and portfolio

- Customer shock: the customer satisfies HYP-2026-002's PIT event eligibility and
  has both positive standardized EPS surprise and positive 20-day net FY1 EPS
  revision breadth. Each component is cross-sectionally z-scored within customer
  sector; the customer shock is their equal-weight average.
- Graph: one-hop customer -> supplier links only; dependency is the percentage of
  supplier revenue attributable to that customer as disclosed before the event;
  require dependency >= 10%; cap dependency at 50% for scoring.
- Supplier score: sum across eligible shocked customers of `customer_shock *
  min(dependency, 50%) * relationship_confidence`, then sector-neutralize and
  percentile-rank. If the vendor lacks a frozen numeric confidence field, use
  `1.0` for source-audited links and exclude all others.
- Exclude a supplier if its own earnings release or material 8-K becomes public
  from one trading day before customer availability through entry, or if an
  eligible negative customer shock offsets the positive aggregate.
- Select the top decile, capped at 10 suppliers; equal weight; maximum 10% per
  name; unused weight remains cash. Enter at next regular-session open and hold
  10 trading days. Overlapping events combine subject to the 10% cap.
- No multi-hop propagation, graph embedding, machine learning, price momentum,
  optimizer, stop, or regime overlay is permitted in the primary variant.

## Baselines and risk model

- Primary investable baseline: equally weighted eligible suppliers of the same
  shocked customers, ignoring shock magnitude and dependency, with identical
  entry, hold, caps, and costs.
- Simple signal baseline: equally weighted shocked customers themselves over the
  same 10-day interval.
- Factor controls: daily `MKT-RF`, `SMB`, `HML`, `RMW`, `CMA`, `UMD`, a
  predeclared low-volatility/BAB proxy, supplier and customer sector returns,
  supplier beta, prior 20-day return, and commodity/input-price controls where
  the supplier industry requires them.
- Portfolio-utility comparator, if not an alpha claim: matched-date Polaris,
  Orion, Lyra, and SPY returns, reported without using them for tuning.

## Frozen experiment

- Primary metric: annualized factor-adjusted intercept of the cost-net candidate-
  minus-primary-supplier-baseline calendar-time return.
- Secondary diagnostics: supplier 10-day residual rank IC; 1/5/20/30-day decay;
  direct-customer baseline difference; bucket monotonicity; dependency and
  relationship-age cohorts; sector/common-shock controls; raw/SPY-relative
  return; turnover; drawdown; capacity; graph coverage/staleness; customer,
  supplier, edge, event, and year concentration; and existing-sleeve overlap/
  correlation.
- Walk-forward design: 2014-2018 discovery/calibration only; expanding annual
  walk-forward evaluation for 2019-2024. The graph snapshot for every fold is
  rebuilt only from relationships available before each event. Same customer
  events and connected suppliers remain grouped in inference/resampling.
- Untouched challenge period: 2025-01-01 through 2026-06-30, read once after the
  graph PIT audit, shock-data validation, and all permitted variant results are
  recorded.
- Cost and capacity assumptions: next-open equity execution; 15 bps per side
  base and 30 bps per side stress unless quote-based spread plus impact is
  higher. Each supplier order must be <= 5% of trailing 20-day median dollar
  volume at `$100K`, `$1M`, and `$10M`; do not remove failed names silently.
- Maximum variants in this family: 4 total, including the primary. Allowed
  alternatives are EPS-surprise-only customer shocks, revision-only shocks,
  unweighted qualifying links, and a 20-day hold. Multi-hop graphs, embeddings,
  altered dependency thresholds, or other horizons require a new hypothesis ID.
- Multiple-testing correction: Romano-Wolf/max-T block bootstrap at one-sided
  `alpha=0.10` across all four variants; blocks group customer-event month and
  every connected supplier so graph-linked observations remain dependent.

## Pass criteria

Further work is justified only if the frozen primary variant:

1. has challenge-period annualized factor-adjusted net alpha >= 3% versus the
   supplier baseline with the adjusted one-sided 90% lower confidence bound
   above zero;
2. has positive supplier 10-day residual rank IC in at least four of the six
   2019-2024 validation years and in the challenge period;
3. beats holding the shocked customers, remains positive at 2x costs, and
   remains positive after supplier/customer industry and momentum controls;
4. includes at least 300 independent customer events, 1,000 eligible supplier-
   event edges historically, and 50 customer events in the challenge period;
5. derives no more than 15% of active return from one customer, 20% from one
   supplier, 50% from the top five graph edges, or 50% from one year; and
6. supports `$1M` reference capital under the 5%-ADV rule.

Passing authorizes continued research or a separately approved forward-shadow
request only; it does not create or activate a strategy.

## Kill criteria

Kill or park if relationship discovery dates cannot be proven PIT-safe; graph
direction/dependency is materially incomplete; required event/edge counts fail;
challenge alpha is non-positive; corrected significance fails; 2x costs erase
the edge; customer momentum, industry, commodity, or supplier-own-news controls
explain it; contributor/capacity limits fail; or minor graph-vintage choices
reverse the sign. Any backfilled relationship used before its known date or any
holdout-informed graph/filter choice voids the experiment.

## Cheapest honest next test

Acquire a vendor sample of 100 customer-supplier edges spanning at least 30
customer earnings events and independently reconstruct each edge from original
filing disclosures. Require 95%+ agreement on direction, dependency, endpoint
identity, first-known date, and termination status. Without looking at the
challenge period, run the frozen one-hop score on the sample and require positive
10-day residual direction versus eligible suppliers. Failure of first-known-date
lineage, adequate event coverage, or positive direction stops the full graph
license/build.

## Freeze record

- Frozen by: Brett Olson, CIO, via explicit `FREEZE HYPOTHESIS, then RUN EXPERIMENT`; drafted by Codex.
- Frozen at: 2026-07-14, America/New_York.
- Spec hash: `sha256:4bd27ef0d8a87e2fe4e263e731bbf59c5b4d481772e51278b547a0d7b5d17317` (all bytes before `## Freeze record`).
- Code hash: no experiment code existed at freeze; repository baseline `4d15ade69799a0eff161d5e9819e4d9d574de66d`.
- Data snapshot/hash: `NOT_ACQUIRED`; vendor graph/event acquisition and PIT audit are part of the frozen experiment.
