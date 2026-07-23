# HYP-2026-011 — Net Payout and Share Issuance

State: `FROZEN`

Experiment: `EXP-2026-0011`

Classification: `FACTOR_HARVEST_CANDIDATE`

## Question and mechanism

Do firms returning capital through net repurchases and dividends outperform
net issuers after accounting for value, quality, momentum, and costs?
Managerial market timing and agency-driven investment may make issuance and
payout informative.

## Point-in-time data contract

Require filing-time shares outstanding, repurchase cash flow, issuance cash
flow, and dividends with accession/correction lineage, fiscal-period alignment,
currency/unit normalization, and effective-dated identity. Current compact SEC
facts do not yet materialize the certified net-payout feature panel; the run
must stop `BLOCKED_DATA`.

## Frozen experiment

- Discovery 2012–2018; validation 2019–2024; untouched challenge
  2025-01-01 through 2026-06-30.
- Annual signal available 90 days after fiscal year end, refreshed only on a
  later accepted filing.
- Primary: net payout yield; diagnostic: one-year split-adjusted share change.
- Long-only top quintile versus eligible-universe equal weight.
- Primary metric: `worst_case_annualized_excess_return_after_costs`.
- Maximum variants: two.
- Costs: 15 bps one-way base and 30 bps stress; 5% ADV ceiling.
- Holm 5% across eight families; regimes secondary only.

## Pass and kill criteria

Pass only if locked validation is positive at both cost levels and terminal
scenarios, corrected significance is below 5%, both definitions agree, and
sector/year/issuer concentration and $1M capacity pass. Kill on filing leakage,
unit/correction failure, non-positive validation, or definition fragility.

## Freeze record

- Frozen by: Brett Olson, CIO, via explicit `FREEZE HYPOTHESIS`; drafted by Codex.
- Frozen at: 2026-07-23, America/New_York.
- Spec hash: `sha256:719ee3831b1f076810b6391e6ee4b78e4522d913eb8bd86e54162fc569651a74` (all bytes before `## Freeze record`).
- Code hash: no HYP-2026-011 evaluator existed at freeze; repository baseline `da5add9`.
- Data snapshot/hash: `NOT_CERTIFIED`; PIT net-payout features must be materialized.
