# FR-063 — Orion/Lyra Redundancy Study

Status: ACTIVE_RESEARCH
Owner: Caerus Research Program
Last Updated: 2026-06-16
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL

## Purpose

FR-063 provides the research-only evidence plan for deciding whether Orion and
Lyra are materially distinct sleeves or redundant expressions of the same
momentum behavior. Polaris is the required baseline. No retirement, promotion,
allocation, strategy-ranking, naming, broker, cron, or execution decision is
approved by this study.

## Current Evidence State

Historical evidence indicates high Orion/Lyra correlation, and current evidence
suggests Lyra may be outperforming Orion. That evidence is not yet sufficient for
retirement because the PIT rebaseline and canonical new-series observation
window are incomplete.

All final disposition decisions belong inside the FR-069 sleeve architecture and
promotion/retirement framework, not as an isolated FR-063 shortcut.

## Study Window Requirements

Required windows:

- Pre-holdout PIT research window from the FR-068/FR-069 PIT foundation.
- Canonical new-series shadow window using `dated_same_day_close_to_close_v1`.
- Recent live-observation window after FR-070 post-buy artifact validation.

Minimum decision-grade evidence:

- PIT universe membership with explicit `universe_method=pit_universe`.
- Holdout exclusion documented before any metric is computed.
- Same benchmark, cost model, rebalance calendar, and data availability rules
  for Orion, Lyra, and Polaris.
- At least one full monthly cycle of canonical shadow artifacts after the
  current execution-layer observation stabilizes.

## Metrics

Core metrics:

- Pairwise daily return correlation: Orion/Lyra, Orion/Polaris, Lyra/Polaris.
- Active share and holdings overlap by rebalance date.
- Sector and factor exposure differences.
- Turnover, concentration, and maximum single-name weight.
- Net return, volatility, Sharpe, Sortino, max drawdown, and drawdown recovery.
- Excess return versus Polaris and SPY.
- Information ratio versus Polaris.
- Regime-sliced performance and attribution.
- Reason-code overlap and signal-family overlap.

Redundancy metrics:

- Orion/Lyra return correlation above 0.90 over the decision window is a
  redundancy warning, not a retirement decision.
- Holdings overlap above 70% and active share below 30% over the same window is
  a redundancy warning.
- If Lyra outperforms Orion after costs but the evidence window is short,
  classify as `RETIREMENT_WATCH`, not `RETIRE`.

## Decision Thresholds

Allowed outputs:

- `DISTINCT_CONTINUE_OBSERVING`
- `REDUNDANT_CONTINUE_OBSERVING`
- `RETIREMENT_WATCH`
- `BLOCKED_INSUFFICIENT_EVIDENCE`

Disallowed outputs:

- `RETIRE_ORION`
- `RETIRE_LYRA`
- `PROMOTE_LYRA`
- `PROMOTE_ORION`
- `REUSE_LYRA_NAME`
- any allocation or production behavior change

Decision-grade retirement review requires all of:

- PIT rebaseline complete for both Orion and Lyra.
- Canonical new-series artifacts complete and fresh.
- Stable correlation, overlap, and active-share evidence across the full window.
- Net-of-cost comparison against Polaris.
- No unresolved data quality, look-ahead, survivorship, or artifact-timing
  blocker.
- FR-069 governance output explicitly accepting the evidence packet.

## Artifacts

Expected research artifacts:

- `outputs/model_quality/<date>/strategy_differentiation_deep_dive.json`
- `outputs/model_quality/<date>/strategy_differentiation_deep_dive.md`
- `outputs/research/fr063_orion_lyra_redundancy/<date>/redundancy_study.json`
- `outputs/research/fr063_orion_lyra_redundancy/<date>/redundancy_study.md`

Governance output:

- FR-069 sleeve evidence envelope for Orion.
- FR-069 sleeve evidence envelope for Lyra.
- FR-063 summary row in the active backlog.
- Explicit owner decision note before any future retirement or promotion FR.

## Non-Goals

- No strategy implementation.
- No backtest tuning.
- No holdout access.
- No production registry change.
- No strategy selection, sizing, allocation, target generation, broker,
  reconciliation, cron, or execution change.

## Acceptance Criteria

1. The study can classify redundancy without recommending production action.
2. Polaris remains the baseline comparator.
3. All decision-grade evidence uses PIT universe membership.
4. Missing evidence produces `BLOCKED_INSUFFICIENT_EVIDENCE`.
5. High correlation alone never authorizes retirement.
6. Any future retirement proposal is opened as a separate owner-approved FR
   under FR-069.
