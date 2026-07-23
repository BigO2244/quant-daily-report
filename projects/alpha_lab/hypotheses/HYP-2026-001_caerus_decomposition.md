# HYP-2026-001 — Current Caerus Decomposition

State: `FROZEN`

Experiment: `EXP-2026-0001`

Classification: `UNPROVEN`

## Question and mechanism

Does the current Caerus research decision rule add cost-adjusted security-
selection value beyond an investable equal-weight eligible-universe baseline,
and which pre-existing components contribute that value? The experiment is an
attribution and ablation of the frozen current rule, not a search for another
Polaris, Orion, or Lyra variant.

The six frozen components are: price momentum, quality, thematic/research
overlay, regime-conditioned sleeve budget, concentration, and exit/rebalance
logic. No weights, thresholds, or components may be added after this freeze.

## Point-in-time data contract

- A research-only Caerus decision tape must reproduce every dated eligible
  security, component score, combined score, selection, weight, and next
  rebalance time without importing or calling production code.
- Effective-dated security identity and membership, observed-price history,
  terminal-return sensitivity, PIT characteristics, and common factors must be
  hash-bound to the run.
- Availability timestamps must be no later than each decision timestamp.
- Delisted names must be reported under both the further-total-loss and zero-
  incremental terminal scenarios. Neither is a verified settlement estimate.
- The current GCP checkout does not yet contain the required decision tape;
  the run must stop `BLOCKED_DATA` until it does.

## Frozen experiment

- Discovery: 2012-01-03 through 2018-12-31.
- Locked validation: 2019-01-01 through 2024-12-31.
- Untouched challenge: 2025-01-01 through 2026-06-30.
- Primary metric: `worst_case_cost_adjusted_incremental_information_ratio`.
- Maximum variants: six leave-one-component-out ablations plus the unchanged
  full rule as their common reference; no parameter variants.
- Costs: 15 bps one-way base and 30 bps one-way stress, with 5% of trailing
  20-day dollar ADV as the capacity ceiling.
- Baselines: eligible-universe equal weight and the unchanged full Caerus rule.
- Multiple testing: Holm correction at 5% across the eight research-family
  primary tests; ablations are attribution diagnostics, not new alpha claims.
- Regimes: the canonical seven point-in-time labels are secondary diagnostics
  only and cannot rescue the unconditional result or change allocation.

## Pass and kill criteria

Pass for owner review only if the worst terminal scenario has positive locked-
validation incremental information ratio, positive net return at base and
stress costs, no single year contributes over 50% of active return, and at
least four of six ablations have stable signed contributions. Kill or park on
leakage, missing decision provenance, non-positive worst-case validation edge,
cost failure, contributor concentration, or material disagreement between the
two terminal scenarios.

Passing does not promote, reweight, rename, or activate any strategy.

## Freeze record

- Frozen by: Brett Olson, CIO, via explicit `FREEZE HYPOTHESIS`; drafted by Codex.
- Frozen at: 2026-07-23, America/New_York.
- Spec hash: `sha256:1928837f611126cb60500e6bb8b296eb52a268ba59d617055b01649d18cc4a58` (all bytes before `## Freeze record`).
- Code hash: no HYP-2026-001 evaluator existed at freeze; repository baseline `da5add9`.
- Data snapshot/hash: `NOT_CERTIFIED`; the data gate is the first authorized run step.
