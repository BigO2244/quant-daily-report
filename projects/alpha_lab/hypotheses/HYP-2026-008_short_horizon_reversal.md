# HYP-2026-008 — Short-Horizon Reversal

State: `FROZEN`

Experiment: `EXP-2026-0008`

Classification: `ALPHA_CANDIDATE`

## Question and mechanism

Do liquid equities with unusually weak recent idiosyncratic returns rebound
over the next week or month after realistic turnover costs? Temporary liquidity
pressure and non-informational order imbalance may mean-revert.

## Point-in-time data contract

Use effective-dated membership/identity, observed returns and liquidity,
PIT beta/volatility/sector/market cap, common factors, and both terminal
scenarios. Signals are formed after the close and first trade at the next
session; names below $5 or $1M trailing 20-day dollar ADV are ineligible.

## Frozen experiment

- Discovery 2012–2018; validation 2019–2024; untouched challenge
  2025-01-01 through 2026-06-30.
- Primary: weekly top quintile of negative five-session market-residual return,
  held five sessions.
- Diagnostics: negative 20-session residual held 20 sessions and a
  volatility-scaled five-session signal.
- Primary metric: `worst_case_annualized_excess_return_after_costs`.
- Maximum variants: three.
- Costs: 25 bps one-way base and 50 bps stress; 2% ADV capacity ceiling.
- Holm 5% across eight families; within-family max-stat correction.
- Regimes are secondary only.

## Pass and kill criteria

Pass only if the primary remains positive under both terminal scenarios and
both cost levels, corrected significance is below 5%, turnover and $1M
capacity pass, at least one diagnostic agrees, and no year dominates. Kill on
leakage, cost erasure, scenario reversal, negative validation, or inadequate
capacity.

## Freeze record

- Frozen by: Brett Olson, CIO, via explicit `FREEZE HYPOTHESIS`; drafted by Codex.
- Frozen at: 2026-07-23, America/New_York.
- Spec hash: `sha256:d0f479a1055c11caac24152dd087e4221c26d67807e3e7de37b2c516276cab00` (all bytes before `## Freeze record`).
- Code hash: no HYP-2026-008 evaluator existed at freeze; repository baseline `da5add9`.
- Data snapshot/hash: `NOT_CERTIFIED`; the data gate is the first authorized run step.
