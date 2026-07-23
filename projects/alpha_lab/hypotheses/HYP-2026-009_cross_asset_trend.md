# HYP-2026-009 — Cross-Asset Trend

State: `FROZEN`

Experiment: `EXP-2026-0009`

Classification: `DIVERSIFIER_CANDIDATE`

## Question and mechanism

Can a long-or-cash trend rule across liquid equity, Treasury, credit, commodity,
and currency proxies improve Caerus portfolio utility across regimes? Slow
macro adjustment and institutional de-risking may create persistent trends.

## Point-in-time data contract

Require a split/dividend-safe, effective-dated daily panel for predeclared
liquid proxies in all five asset groups, with inception, closure, replacement,
and availability history. Free equity-only factors are insufficient. The
current checkout lacks this certified cross-asset panel and must stop
`BLOCKED_DATA`.

## Frozen experiment

- Discovery 2012–2018; validation 2019–2024; untouched challenge
  2025-01-01 through 2026-06-30.
- Monthly long-or-cash signals: 12-, 6-, and 3-month time-series momentum plus
  an equal-weight ensemble; no shorting or leverage.
- Primary metric: `validation_delta_portfolio_information_ratio`.
- Maximum variants: four.
- Costs: 10 bps one-way base and 20 bps stress.
- Baselines: cash proxy, equal-weight long-only proxy basket, and unchanged
  Caerus return stream for portfolio-utility comparison.
- Holm 5% across eight families; regimes secondary only.

## Pass and kill criteria

Pass only if locked validation improves portfolio information ratio and
drawdown under both cost levels without reducing annual return by more than
2 percentage points, and at least three asset groups contribute. Kill on
missing PIT lineage, equity-only concentration, unstable sign, or no portfolio
utility.

## Freeze record

- Frozen by: Brett Olson, CIO, via explicit `FREEZE HYPOTHESIS`; drafted by Codex.
- Frozen at: 2026-07-23, America/New_York.
- Spec hash: `sha256:39c5b2d3e085d9dad24c2fc25def3360a1a43e84a587b4b39bf3c9d50fd38739` (all bytes before `## Freeze record`).
- Code hash: no HYP-2026-009 evaluator existed at freeze; repository baseline `da5add9`.
- Data snapshot/hash: `NOT_ACQUIRED`; certified cross-asset history is required.
