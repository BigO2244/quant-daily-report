# HYP-2026-006 — Residual Momentum

State: `FROZEN`

Experiment: `EXP-2026-0006`

Classification: `ALPHA_CANDIDATE`

## Question and mechanism

Do stocks with strong firm-specific intermediate-horizon returns continue to
outperform after removing broad market exposure? Slow information diffusion
and investor underreaction may persist after conventional market beta is
removed.

## Point-in-time data contract

Use effective-dated membership and identity, observed total-return history,
PIT beta/volatility/sector/market-cap characteristics, common factors, and the
two-scenario terminal-return sensitivity envelope. Every feature must be
available by the decision close; portfolio returns begin at the next session.

## Frozen experiment

- Discovery: 2012-01-03 through 2018-12-31; validation: 2019-01-01 through
  2024-12-31; untouched challenge: 2025-01-01 through 2026-06-30.
- Monthly long-only top quintile versus eligible-universe equal weight.
- Primary signal: 12-month minus most recent month total return, residualized
  with the PIT 252-day beta and trailing market return.
- Two diagnostics: 6-minus-1 and 3-minus-1 residual momentum.
- Primary metric: `worst_case_annualized_excess_return_after_costs`.
- Maximum variants: three.
- Costs: 15 bps one-way base, 30 bps stress; 5% of 20-day dollar ADV capacity.
- Holm 5% across eight family primary tests; within-family max-stat correction.
- Canonical regimes are secondary, known-at-decision diagnostics only.

## Pass and kill criteria

Pass for owner review only if the primary signal is positive in locked
validation under both terminal scenarios and both cost levels, corrected
significance is below 5%, at least two variants agree in sign, no year
contributes over 50%, and capacity supports $1M. Kill on leakage, non-positive
worst-case edge, cost failure, scenario sign reversal, or concentration.

## Freeze record

- Frozen by: Brett Olson, CIO, via explicit `FREEZE HYPOTHESIS`; drafted by Codex.
- Frozen at: 2026-07-23, America/New_York.
- Spec hash: `sha256:f55fbea348b02e18ae81cbfe261c434e018db6d48d6a8fe13c382003bd74cc94` (all bytes before `## Freeze record`).
- Code hash: no HYP-2026-006 evaluator existed at freeze; repository baseline `da5add9`.
- Data snapshot/hash: `NOT_CERTIFIED`; the data gate is the first authorized run step.
