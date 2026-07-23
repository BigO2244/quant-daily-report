# HYP-2026-012 — Asset Growth and Investment

State: `FROZEN`

Experiment: `EXP-2026-0012`

Classification: `FACTOR_HARVEST_CANDIDATE`

## Question and mechanism

Do conservative investors with low asset growth outperform aggressive
investors after value, quality, momentum, and cost controls? Overinvestment,
empire building, and financing frictions may make rapid balance-sheet expansion
a negative return predictor.

## Point-in-time data contract

Require filing-time total assets with accession, fiscal-period, unit,
restatement/correction lineage, effective-dated security identity, and an
explicit 90-day post-year-end availability floor. Although raw SEC Company
Facts contain assets, the certified PIT asset-growth feature panel does not yet
exist; the run must stop `BLOCKED_DATA`.

## Frozen experiment

- Discovery 2012–2018; validation 2019–2024; untouched challenge
  2025-01-01 through 2026-06-30.
- Primary: negative one-year total-asset growth; diagnostic: negative
  two-year compounded asset growth.
- Annual long-only top quintile versus eligible-universe equal weight.
- Primary metric: `worst_case_annualized_excess_return_after_costs`.
- Maximum variants: two.
- Costs: 15 bps one-way base and 30 bps stress; 5% ADV ceiling.
- Controls: size, book-to-market, quality/profitability, momentum, sector.
- Holm 5% across eight families; regimes secondary only.

## Pass and kill criteria

Pass only if locked validation is positive under both terminal scenarios and
cost levels, corrected significance is below 5%, both horizons agree, and
restatement, sector, year, issuer, and $1M capacity gates pass. Kill on
availability leakage, restatement sensitivity, non-positive validation, cost
failure, or horizon sign reversal.

## Freeze record

- Frozen by: Brett Olson, CIO, via explicit `FREEZE HYPOTHESIS`; drafted by Codex.
- Frozen at: 2026-07-23, America/New_York.
- Spec hash: `sha256:80684a91baf876e5e711b97772a69e471e574aa92db6fee7fcb655dcfa47497b` (all bytes before `## Freeze record`).
- Code hash: no HYP-2026-012 evaluator existed at freeze; repository baseline `da5add9`.
- Data snapshot/hash: `NOT_CERTIFIED`; PIT asset-growth features must be materialized.
