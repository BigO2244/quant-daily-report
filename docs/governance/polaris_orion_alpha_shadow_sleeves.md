# Polaris_Alpha and Orion_Alpha Shadow Sleeves

Status: Official SHADOW
Governance label: RESEARCH_ONLY
Execution impact: NON_EXECUTIONAL
Capital impact: NONE
Activated: 2026-06-23

## Purpose

Polaris_Alpha and Orion_Alpha are official forward shadow sleeves for testing
whether concentrated momentum construction improves alpha capture while keeping
drawdowns within acceptable bounds.

The sleeves preserve the existing Polaris and Orion baselines exactly as
comparison controls. They do not allocate capital, submit orders, alter paper
trading, alter the live pilot, alter scheduler behavior, or change production
portfolio construction.

## Definitions

| Sleeve | Strategy ID | Baseline | Lifecycle | Construction |
|---|---|---|---|---|
| Polaris_Alpha | `caerus_polaris_alpha` | `caerus_polaris` | SHADOW | Top 4 Polaris-ranked names, equal weight, 20% max position, residual cash measured |
| Orion_Alpha | `caerus_orion_alpha` | `caerus_orion` | SHADOW | Top 3 Orion rank-decay names, equal weight, 25% max position, residual cash measured |

Score-squared weighting is intentionally excluded from both sleeves.

## Required Metrics

Daily shadow artifacts must include, at minimum:

- return
- drawdown
- turnover
- concentration
- HHI and effective N
- cash/residual exposure
- alpha-per-dollar-deployed proxy
- pairwise comparison versus the preserved baseline sleeve

## Review Checkpoints

20 trading days:

- artifact chain complete
- no live/paper/allocator/broker/scheduler impact
- compare return, drawdown, turnover, concentration, and alpha-per-dollar proxy
  versus the preserved baseline

60 trading days:

- evaluate whether concentration improves alpha capture after costs
- verify drawdown does not increase disproportionately
- verify turnover and cash drag remain acceptable
- decide whether to continue shadow, retire/shelve, or request a separate owner
  approval for further promotion work

## Promotion Guardrails

Shadow status is not approval for paper, live, pilot capital, allocation, or
production use. Any promotion beyond SHADOW requires:

- owner approval
- decision-grade PIT evidence
- complete lineage
- explicit production/paper/live change request
- separate validation of allocator and execution implications
