# HYP-2026-007 — Stock-Specific Return Seasonality

State: `FROZEN`

Experiment: `EXP-2026-0007`

Classification: `ALPHA_CANDIDATE`

## Question and mechanism

Do issuer-specific calendar-month return patterns repeat out of sample after
costs? Recurring fiscal, compensation, rebalancing, and investor-attention
cycles may create predictable issuer-level seasonality not captured by broad
market seasonality.

## Point-in-time data contract

Use effective-dated identity/membership, observed total-return history, and the
two terminal scenarios. A month-of-year score may use only the same calendar
month from the preceding five completed years, requires at least three valid
prior observations, and may not use current-year returns.

## Frozen experiment

- Discovery 2012–2018; validation 2019–2024; untouched challenge
  2025-01-01 through 2026-06-30.
- Monthly long-only top quintile versus eligible-universe equal weight.
- Primary: five-year same-calendar-month mean, cross-sectionally demeaned.
- Placebo diagnostic: adjacent-calendar-month history using the identical rule.
- Primary metric: `worst_case_annualized_excess_return_after_costs`.
- Maximum variants: two.
- Costs: 15 bps one-way base and 30 bps stress; 5% ADV ceiling.
- Holm 5% across eight families and a frozen primary-versus-placebo comparison.
- Regime slices are secondary only.

## Pass and kill criteria

Pass only if locked validation is positive under both terminal scenarios and
cost levels, corrected significance is below 5%, the primary exceeds the
placebo, no month/year dominates, and $1M capacity passes. Kill on leakage,
non-positive worst-case return, placebo equivalence, scenario reversal, or
calendar/contributor concentration.

## Freeze record

- Frozen by: Brett Olson, CIO, via explicit `FREEZE HYPOTHESIS`; drafted by Codex.
- Frozen at: 2026-07-23, America/New_York.
- Spec hash: `sha256:3309270af1272bbd54cdbe6b03bc776d0151d83f9dc245454bb7c191e0e00588` (all bytes before `## Freeze record`).
- Code hash: no HYP-2026-007 evaluator existed at freeze; repository baseline `da5add9`.
- Data snapshot/hash: `NOT_CERTIFIED`; the data gate is the first authorized run step.
