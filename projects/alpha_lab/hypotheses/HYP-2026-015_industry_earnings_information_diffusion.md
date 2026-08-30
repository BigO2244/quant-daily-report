# HYP-2026-015 — Industry Earnings-Information Diffusion

State: `FROZEN_NOT_AUTHORIZED_TO_RUN`

Experiment: `EXP-2026-0015`

Governance label: `RESEARCH_ONLY`

Execution impact: `NON_EXECUTIONAL`

Canonical lineage: this is a narrow event-information family inside the
existing Caerus Cassiopeia / FR-052 event-driven research candidate. It does
not create, rename, activate, promote, or alter a strategy, Shadow lane,
allocation, broker, scheduler, Paper, Live, or production behavior.

## Memory-derived opportunity map and prior-model review — required before freeze

- Model compendium path and SHA-256: `projects/alpha_lab/MODEL_COMPENDIUM.md`,
  `sha256:123779c566d24b2edb96770cffd90bb0482cbd5d184576796531474c141ea4af`.
- Experiment ledger reviewed at: `projects/alpha_lab/EXPERIMENT_LEDGER.md`,
  `sha256:a6ce3869d206e48634050102ddc65d5e30c99f1059529963a8b941f727fc1be7`.
- Strategy backlog reviewed at: `projects/alpha_lab/STRATEGY_BACKLOG.md`,
  `sha256:860a4d90f9be3fa2022c99dd9fef1c255ecc69d31a52a197ef86caa2c41f17ed`.
- Completed legacy-intake directory reviewed at:
  `projects/alpha_lab/legacy_model_intakes/`; the only file is `README.md`,
  `sha256:887bf9db115db7764381933f315174dc86656f38076eb8ad5ceb751c6592800e`,
  and it certifies that no completed legacy intake exists.
- Closest registered experiment IDs: `EXP-2026-0002` earnings-revision drift,
  `EXP-2026-0005` supply-chain shock diffusion, `EXP-2026-0008` generic
  short-horizon reversal, and `EXP-2026-0013` AI power/grid commitment events.
- Closest registered strategy IDs: `caerus_cassiopeia` (event-driven research),
  `caerus_cygnus` (earnings-drift research), and the momentum comparators
  `caerus_polaris`, `caerus_orion`, and `caerus_lyra`.
- Relevant completed legacy intake IDs, or `NONE`: `NONE`; the short moving-
  average study remains pending source intake and is not used as freeze proof.
- Applicable negative or positive lessons: earnings revisions cannot be tested
  with current consensus snapshots; supply-chain diffusion cannot substitute
  current relationships for event-time links; generic reversal failed gross
  and cost gates; attributable event timestamps and named economic exposure are
  mandatory; and relative superiority is insufficient without positive net
  economics.
- Opportunity-map gaps or combinations considered: generic long-only reversal
  was rejected as duplicate repair; options-activity volatility forecasting was
  deferred for inadequate forward history; reporter-to-peer diffusion survived
  because it uses a distinct public event, a short horizon, and existing causal
  SEC and price data.
- Why institutional memory led to this proposal: the two closest diffusion
  ideas are blocked by licensed analyst histories or relationship graphs, while
  original Item 2.02 filings, exact EDGAR acceptance times, event-time SEC SIC,
  and PIT price/volume observations can expose a simpler industry-attention
  mechanism without those unavailable inputs.
- Proposed relationship: `NEW_MECHANISM`.
- Material economic difference from the closest prior work: the information
  shock is the reporter's fully observed post-filing market reaction and the
  target is a not-yet-reporting same-SIC peer. It is neither revision drift in
  the reporter, supply-chain propagation, generic momentum, nor generic
  reversal.
- For `COMBINED_MECHANISMS`, incremental claim beyond each standalone model:
  `NOT_APPLICABLE`.
- For `COMBINED_MECHANISMS`, standalone and simple-allocation baselines:
  `NOT_APPLICABLE`.
- Parameter-only similarities that are not novel: positive past return,
  abnormal volume, short holding periods, and long-only equal weighting are
  implementation primitives and do not create separate hypotheses.
- Prior construction or failure mode this test must not repeat: no threshold or
  horizon grid, no post-outcome industry definition, no current-vintage peer
  map, no same-session look-ahead, no inference from a data-blocked result, and
  no claim based only on beating a losing baseline.
- Prior-model review completed by: Atlas / Codex acting under Brett Olson's CIO
  authority.
- Prior-model review completed at: 2026-08-30, America/New_York.

The review additionally used the freeze-ready weekly packet at
`projects/alpha_lab/reports/WEEKLY_CIO_2026-08-30.md`,
`sha256:52bcbf3f505ffd291a880cece4686de261e3848d5a1ff057290018d299fe339d`.
That packet's read-only 30-payload audit found exactly one four-digit SEC SIC
and a matching CIK in every sampled retained original; it read no return or
challenge outcome.

## Claim

After a qualifying Item 2.02 reporter has a positive, volume-confirmed first
regular-session reaction, liquid same-four-digit-SEC-SIC peers that have not
yet reported in the same calendar quarter will earn a positive five-session
transaction-cost-net return, incremental to a contemporaneous two-digit-SIC
industry control, when entered at the next regular-session open.

## Initial classification

`ALPHA_CANDIDATE`

## Economic mechanism

- Investors and analysts initially process an earnings release as issuer-
  specific news even when it reveals demand, pricing, input-cost, or capacity
  information shared by close industry peers.
- Issuer-first attention, segmented coverage, and staggered reporting calendars
  can delay the cross-issuer inference until peers report or other investors
  propagate it.
- The mechanism should decay quickly because the reporter's reaction is public;
  the claim is a short event-driven diffusion effect, not persistent momentum.
- Caerus can capture it with public SEC filings, observable market reaction, and
  a liquid long-only peer basket. No private information, paid analyst history,
  short book, or relationship graph is required for the primitive test.
- A result explained by broad industry return, raw momentum, the reporter's own
  post-earnings drift, or a few crowded industries is not the claimed alpha.

## Prediction

- Expected sign: positive for the eligible peer basket after a qualifying
  positive reporter shock.
- Forecast horizon: five regular trading sessions after next-open entry.
- Expected decay: strongest during sessions 1-5; no claim is made beyond session
  5.
- Applicable universe: PIT-active US common equities with an effective-dated
  security identity, price at least `$5`, trailing 20-session median dollar ADV
  at least `$10M`, complete reaction/holding price history, and event-time
  four-digit SEC SIC.
- Conditions where it should be strongest: large reporter reaction, abnormal
  dollar volume, at least three eligible not-yet-reporting peers, and industries
  with staggered reporting calendars.
- Conditions where it should fail: one-name industries, broad macro/sector
  shocks, peers that already reported, ambiguous SIC or identity, reaction
  measured before public availability, or immediate complete industry repricing.

## Point-in-time data contract

- Source: immutable original SEC 8-K/8-K-A payloads containing Item 2.02 with
  exact EDGAR acceptance time; SEC header CIK and four-digit SIC observed in the
  retained payload; effective-dated security identity and membership; and the
  PIT observed price/liquidity panel. The existing derived earnings tape is
  discovery-only because its current readiness artifact is `BLOCKED` on
  original-source and issuer-announcement semantics.
- Observation timestamp: the exact EDGAR acceptance timestamp of the retained
  original filing and the regular-session close at which the reporter reaction
  becomes fully observable.
- Availability timestamp: filings accepted strictly before 09:30
  America/New_York define that day's regular reaction session; filings accepted
  at or after 09:30 define the next regular reaction session. Peer entry is
  always the next regular-session open after the full reaction session closes.
- Security identifier: effective-dated security ID joined from filing-header
  CIK. Ticker is display-only. Reporter and peer must each have a unique causal
  mapping on the relevant timestamp.
- Delisting and corporate-action handling: adjusted-price lineage must preserve
  splits and distributions. Any selected reporter, peer, or control with a
  missing path or unverified terminal event inside the reaction or five-session
  holding window blocks the outcome-bearing build; no survivor deletion or
  assumed terminal return is permitted.
- Missing-data rule: unknown acceptance time, unavailable original bytes,
  missing payload hash, ambiguous CIK, missing four-digit SIC, non-unique
  security mapping, stale price, incomplete volume history, unresolved
  corporate action, or unresolved terminal outcome fails closed before returns.
- Known coverage limitations: SEC SIC is issuer-reported and economically
  coarse; some foreign issuers and non-8-K reporters are absent; Item 2.02 does
  not prove the issuer's original announcement time; and the currently certified
  price panel does not independently assert delisting settlement values.

Bound source evidence at freeze:

- Original-filings manifest:
  `data_spine/sec_original_filings_stream/20260722T212948Z-8bec6cab476f/manifest.json`,
  file `sha256:90bd5b5d43da8d8e02b924308cbc049cee117535db597769a1c43a057036278f`,
  bundle hash `25f7cdf591a1f80339309b0ca1a2c5abc18a01529fca9e3d7e3eb004dcfd7ad4`.
- Earnings readiness:
  `provider_readiness/pit_earnings_events_v1.json`,
  `sha256:44e9d240a34560794f70f29cf73ddee1ad569192529eacfadadf135ee60d89ac`,
  historical PIT verified but status `BLOCKED`; it cannot satisfy the original-
  payload gate by itself.
- Observed-price readiness:
  `provider_readiness/pit_observed_prices_v1.json`,
  `sha256:6a97d2ae3311ae3ad24ee289a37099afde70473b782f26e21c5487d7563af7d0`,
  status `READY`; underlying panel
  `sha256:7b6518bc30d84820b5113465fb23d54de36012195ed1672ed19aca9e216c99c0`.

## Baselines and risk model

- Primary investable baseline: on each candidate entry date, equal weight the
  PIT-eligible, not-yet-reporting issuers in the reporter's two-digit SEC SIC
  division but outside its four-digit SIC, with the same liquidity rules,
  next-open entry, five-session holding window, cash treatment, and costs.
- Simple signal baseline: buy the reporter itself at the peer-entry open and
  hold for the same five sessions under identical liquidity and cost rules.
- Raw-momentum baseline: equal weight the same number of PIT-eligible names from
  the reporter's two-digit SIC with the highest trailing 20-session return,
  measured at the reaction close and excluding reporter and candidate peers.
- Factor controls: daily `MKT-RF`, `SMB`, `HML`, `RMW`, `CMA`, `UMD`, two-digit
  SIC return, prior 20-session return, and market volatility. Controls are
  evaluation-only and cannot filter the primary portfolio.
- Portfolio-utility comparator: matched-date Polaris, Orion, Lyra, and SPY
  returns, reported separately and never used for selection or tuning.

## Frozen event, signal, and portfolio

- Qualifying filing: original 8-K/8-K-A with Item 2.02, exact acceptance time,
  one header CIK, one four-digit header SIC, immutable payload hash, and unique
  effective-dated reporter security identity. Amendments link to the original;
  an unresolved amendment or duplicate payload fails closed.
- Reaction session: the first full regular session after the filing is public
  under the availability rule above. Reporter reaction is close-to-close total
  return from the immediately preceding regular close through the reaction
  close.
- Positive reporter shock: reporter reaction at least `+5.00%` and reaction-
  session dollar volume at least `2.00x` the median dollar volume of the prior
  20 completed sessions. Both thresholds are inclusive and frozen.
- Not-yet-reporting peer: same four-digit SEC SIC observed from the peer's latest
  original SEC header available by the reaction close, with no Item 2.02 filing
  accepted from the first calendar day of that quarter through the reaction
  close. The reporter is excluded.
- Breadth: at least three eligible peers must exist for an event. All eligible
  peers are equally weighted; no ranking, optimizer, momentum filter, or
  discretionary industry override is permitted. Unallocated weight remains
  cash.
- Entry and exit: enter at the next regular-session open after the reaction
  close; exit at the close of the fifth regular session. A peer report during
  the holding window is retained and reported as a contamination diagnostic;
  it does not create a hindsight-based exit or exclusion.
- Event overlap: a peer may hold only one position. A later qualifying reporter
  event cannot reset, extend, or increase an open position. Same-SIC events with
  the same reaction session are combined into one event cluster and the peer
  basket is formed once.
- Negative reporter shocks: descriptive symmetry diagnostic only; they create
  no primary long position and no extra variant.

## Frozen experiment

- Hypothesis family ID: `FAMILY-2026-0015`.
- Family generation / experiment ID: generation 1 / `EXP-2026-0015`.
- Parent family or experiment IDs: `NONE`; nearest comparisons are
  `EXP-2026-0002`, `EXP-2026-0005`, `EXP-2026-0008`, and `EXP-2026-0013`.
- Exploratory wave ID and frozen membership:
  `WAVE-2026-003-ORTHOGONAL-EVENT-01`; ordered membership
  `[EXP-2026-0015]`. This reservation is not a canonical ledger event and must
  be registered before any outcome-bearing run.
- Family scope hash:
  `sha256:5b0c632d2e451b16e7faafc759078ab13c9281aed63ca92d484a0135a26e46bc`
  over the frozen family/mechanism/signal/holding/universe/variant census.
- Primary metric: mean five-session transaction-cost-net candidate peer-basket
  return minus the contemporaneous primary industry-baseline return, clustered
  by reporter issuer, four-digit SIC, and calendar quarter.
- Expected direction, null value, and minimum economic hurdle: positive; null
  `0.00%`; at least `+0.50%` mean net incremental return per independent event
  cluster at base cost.
- Frozen primary variant ID: `PRIMARY_V1` only: `+5.00%` reporter return,
  `2.00x` abnormal dollar volume, three-peer minimum, `$5` price, `$10M` median
  ADV, next-open entry, five-session hold, equal weight.
- Secondary diagnostics: gross and stress-net return; hit rate; 1/3/5-session
  decay; factor/industry/momentum attribution; reporter-only and raw-momentum
  baselines; negative-shock symmetry; peer-report contamination; event, issuer,
  SIC, quarter, and year concentration; turnover, drawdown, and capacity.
- Walk-forward design: source/data audit and discovery from 2012-01-01 through
  2018-12-31; frozen annual expanding validation from 2019-01-01 through
  2024-12-31. No threshold, industry map, exclusion, baseline, or holding change
  may use validation or challenge outcomes.
- Resampling/independence unit and effective-sample floor: reporter-issuer ×
  four-digit-SIC × calendar-quarter cluster; at least 150 independent clusters,
  100 unique peers, and 20 four-digit SICs in validation. No year may contribute
  more than 30% and no four-digit SIC more than 25% of clusters.
- Untouched challenge epoch ID, exact period, and panel hash:
  `CHALLENGE-2025H1-2026H1-01`, 2025-01-01 through 2026-06-30,
  `sha256:7b6518bc30d84820b5113465fb23d54de36012195ed1672ed19aca9e216c99c0`;
  status `SEALED_UNOPENED_UNREGISTERED`. Missing canonical ledger registration
  blocks challenge access.
- Cost and capacity assumptions: next-open equity execution; 15 bps per side
  base and 30 bps per side stress; each order no more than 5% of trailing
  20-session median dollar ADV at `$100K`, `$1M`, and `$10M`. Failed capacity is
  reported, not silently removed.
- Maximum statistical trial units in this family generation: one.
- Nested ML selection-trial budget, if applicable: `ZERO`; ML, optimization,
  threshold search, and parameter grid are forbidden.
- Within-family FWER method: verified Holm at one-sided `alpha=0.10`; with one
  frozen primary trial the adjusted and raw p-values coincide.
- Wave-level multiple-testing method and alpha/q: Benjamini-Yekutieli at frozen
  `q=0.10`; no outcome may be read until the wave and family are registered in
  the authenticated canonical ledger.

## Pass criteria

Further work is justified only if the data build first passes all source,
identity, causality, coverage, and true-return gates; the primary variant's
2019-2024 validation mean net incremental event return is at least `+0.50%`;
its adjusted one-sided 90% lower confidence bound is above zero; the result is
positive at 30 bps per-side stress cost; the breadth and concentration floors
hold; no single issuer contributes more than 15% of aggregate active return;
and `$1M` capacity passes the 5%-ADV rule. A later separately authorized
challenge read must satisfy the same direction, stress-cost, breadth, and
concentration gates. Passing authorizes only evidence review or a separately
approved forward Shadow nomination; it does not activate one.

## Kill criteria

Park or kill if original source, acceptance-time, CIK/SIC, effective-dated
identity, or true-return lineage cannot pass before outcome access; fewer than
150 independent validation clusters, 100 unique peers, or 20 SICs survive; the
primary net incremental result is below `+0.50%`, non-positive at stress cost,
or has a non-positive corrected lower bound; industry or raw momentum explains
the effect; one issuer/SIC/year dominates; peer-report contamination reverses
the sign; capacity fails at `$1M`; or any result-informed threshold, horizon,
industry rule, or exclusion is introduced. The entire return/volume/holding
family is one hypothesis and cannot be rescued by a parameter grid.

## Cheapest honest next test

Build one no-return event/peer eligibility manifest from the immutable original
Item 2.02 payloads and PIT identity/SIC history. It must prove 100% source hash
and exact acceptance-time retention, 100% unique reporter mapping, at least 99%
unique eligible-peer mapping, at least 150 validation-period event clusters,
100 peers, 20 four-digit SICs, deterministic overlap handling, and a complete
reaction/holding return-path inventory with zero unresolved terminal events.
Stop `BLOCKED_DATA` before joining any reporter reaction or forward return if a
gate fails. This test is not authorized by the present freeze.

## Freeze record

- Frozen by: Brett Olson, CIO, via explicit `FREEZE HYPOTHESIS`; drafted by
  Atlas / Codex.
- Frozen at: 2026-08-30, America/New_York.
- Spec hash: `sha256:3ca51f2f477c548d0b9ad266f004b4f61ba532f1d23961847c05db1e5fd033d6`
  (all bytes before `## Freeze record`).
- Code hash: no HYP-2026-015 evaluator or runner exists at freeze; repository
  baseline `da11ea3d4cf22c07957b3cdc5f1959af0b30f732`.
- Data snapshot/hash: `NOT_CERTIFIED_FOR_OUTCOME_TEST`; only the read-only source
  evidence listed above is bound. No return, holdout, challenge, or trial was
  accessed or consumed.
