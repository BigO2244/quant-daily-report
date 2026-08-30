# HYP-YYYY-NNN — Hypothesis Title

## Memory-derived opportunity map and prior-model review — required before freeze

- Model compendium path and SHA-256:
- Experiment ledger reviewed at:
- Strategy backlog reviewed at:
- Completed legacy-intake directory reviewed at:
- Closest registered experiment IDs:
- Closest registered strategy IDs:
- Relevant completed legacy intake IDs, or `NONE`:
- Applicable negative or positive lessons:
- Opportunity-map gaps or combinations considered:
- Why institutional memory led to this proposal:
- Proposed relationship: `NEW_MECHANISM`, `CHILD_EXPERIMENT`,
  `COMBINED_MECHANISMS`, or `DUPLICATE_REJECT`:
- Material economic difference from the closest prior work:
- For `COMBINED_MECHANISMS`, incremental claim beyond each standalone model:
- For `COMBINED_MECHANISMS`, standalone and simple-allocation baselines:
- Parameter-only similarities that are not novel:
- Prior construction or failure mode this test must not repeat:
- Prior-model review completed by:
- Prior-model review completed at:

`DUPLICATE_REJECT`, missing source hashes, or an incomplete search blocks
`FROZEN` status and outcome-bearing testing.

## Claim

One falsifiable sentence.

## Initial classification

Choose one: `ALPHA_CANDIDATE`, `FACTOR_HARVEST_CANDIDATE`,
`DIVERSIFIER_CANDIDATE`, `PROTECTION_CANDIDATE`, or
`EXECUTION_EDGE_CANDIDATE`.

## Economic mechanism

- Who is constrained, slow, forced, inattentive, or bearing risk?
- Why should the return persist after publication?
- Why can Caerus capture it?

## Prediction

- Expected sign:
- Forecast horizon:
- Expected decay:
- Applicable universe:
- Conditions where it should be strongest:
- Conditions where it should fail:

## Point-in-time data contract

- Source:
- Observation timestamp:
- Availability timestamp:
- Security identifier:
- Delisting and corporate-action handling:
- Missing-data rule:
- Aggregate source/universe coverage tolerance and exact denominator:
- Included-row causal lineage requirement (normally `100%` for every included
  observation):
- Deterministic pre-outcome exclusion rule for missing or ambiguous rows:
- Missingness diagnostics and concentration gates by relevant time, industry,
  issuer, and observation role:
- Absolute `100%` source-universe gate owner sign-off: `NOT_USED`, or owner,
  date, rationale, and explicit acknowledgement that any single missing row
  blocks the whole gate:
- Known coverage limitations:

An absolute `100%` source-universe or mapping gate requires explicit owner
sign-off in the frozen hypothesis. Otherwise the hypothesis must predeclare a
numeric aggregate tolerance, maintain `100%` causal lineage for every included
row, exclude ambiguous rows deterministically before outcomes, and freeze
missingness/concentration diagnostics. These rules may not be invented or
relaxed after outcome access.

## Baselines and risk model

- Primary investable baseline:
- Simple signal baseline:
- Factor controls:
- Portfolio-utility comparator, if not an alpha claim:

## Frozen experiment

- Hypothesis family ID:
- Family generation / experiment ID:
- Parent family or experiment IDs:
- Exploratory wave ID and frozen membership:
- Family scope hash:
- Primary metric:
- Expected direction, null value, and minimum economic hurdle:
- Frozen primary variant ID:
- Secondary diagnostics:
- Walk-forward design:
- Resampling/independence unit and effective-sample floor:
- Untouched challenge epoch ID, exact period, and panel hash:
- Cost and capacity assumptions:
- Maximum statistical trial units in this family generation:
- Nested ML selection-trial budget, if applicable:
- Within-family FWER method:
- Wave-level multiple-testing method and alpha/q:

## Pass criteria

Predeclare the minimum result that justifies further work.

## Kill criteria

Predeclare results that end or park the thesis, including leakage, non-positive
locked holdout edge, cost failure, contributor concentration, parameter
fragility, or inadequate data/capacity.

## Cheapest honest next test

The smallest test that can falsify the mechanism before a full backtest.

## Freeze record

- Frozen by:
- Frozen at:
- Spec hash:
- Code hash:
- Data snapshot/hash:
