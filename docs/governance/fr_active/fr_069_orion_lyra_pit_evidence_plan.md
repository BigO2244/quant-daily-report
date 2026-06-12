# FR-069 Orion/Lyra PIT Evidence Packet Plan

Status: PHASE_B_SCAFFOLD
Last Updated: 2026-06-12
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL

This plan defines the evidence packet required before any Orion/Lyra promotion,
retirement, rename, or consolidation decision. Phase B makes no such decision.
Orion and Lyra continue evaluation.

## Required Point-In-Time Evidence

- PIT universe family and `universe_snapshot_hash` for every run.
- PIT prices and corporate-action handling.
- Availability policy for every signal input.
- Benchmark policy, primarily SPY unless superseded by owner approval.
- Holdout exclusion flag and pre-registration record.
- Artifact envelope carrying `sleeve_id`, `strategy_id`, `universe_method`,
  `price_source`, `benchmark`, `metrics`, `holdings`, `attribution`,
  `reason_codes`, `governance_label`, and `execution_impact`.

## Correlation And Differentiation

Future evidence must compare Orion and Lyra against each other and Polaris:

- return correlation over matched windows;
- active share and holdings overlap;
- factor and sector exposure overlap;
- attribution overlap;
- drawdown co-movement;
- signal-turnover similarity;
- regime-conditional return behavior.

High correlation alone is not a retirement decision. It is a trigger for deeper
differentiation evidence and owner review.

## Return, Risk, And Drawdown Metrics

- cumulative and annualized return;
- excess return versus SPY and Polaris;
- volatility;
- Sharpe/IR where sample size supports it;
- max drawdown and drawdown duration;
- hit rate and tail-loss diagnostics;
- cost sensitivity.

## Turnover And Concentration

- average and peak turnover;
- average holding count;
- top-position concentration;
- sector/factor concentration;
- liquidity/capacity constraints;
- rebalance cadence sensitivity.

## Regime Decomposition

- VIX or volatility regime buckets;
- trend/risk-on/risk-off segmentation where PIT-safe;
- drawdown/recovery windows;
- inflation/rate regime only if data availability is PIT documented;
- explicit `INSUFFICIENT_SAMPLE` labels when regime evidence is sparse.

## Promotion Or Retirement Thresholds

Promotion requires decision-grade PIT evidence, observation-window gates,
material differentiation, risk controls, and explicit owner approval.

Retirement requires explicit owner approval plus evidence that the sleeve is
redundant or inferior after:

- PIT rebaseline;
- matched-window comparison;
- risk and cost adjustment;
- regime analysis;
- artifact and data-quality review.

Phase B does not retire Orion or Lyra, does not rename Lyra, and does not reuse
the Lyra name for another strategy.

## Minimum Observation Window

Use existing 20/40/60-day readiness windows where relevant. If artifacts do not
support a decision-grade window, mark the packet as `INSUFFICIENT_OBSERVATION`
rather than inferring a conclusion.
