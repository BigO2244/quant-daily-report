# FR-105 Global Portfolio Optimizer and Decision Provenance

Status: ACTIVE_RESEARCH
Governance Label: RESEARCH_ONLY / NON_EXECUTIONAL
Date opened: 2026-06-25
Owner: Caerus Research Program

Numbering note: the initiating prompt suggested FR-075, but FR-075 is already
used by `docs/governance/caerus_operational_controls_framework.md`. This
initiative is registered as FR-105 to avoid a governance-number collision.

## Scope Boundary

This document authorizes research planning only.

It does not change production trading behavior, paper trading behavior,
allocation behavior, sizing, broker submission, cron, sleeve construction, risk
thresholds, optimizer logic, portfolio-construction logic, or promotion state.
It does not replace the current allocator. No live, paper, or shadow promotion
is authorized without point-in-time backtests, governed review, and explicit
owner approval.

## 1. Problem Statement

Caerus currently builds portfolio intent by combining sleeve-local candidate
sets and then applying allocation, cap, cash, turnover, and execution controls.
Recent execution transparency work showed that candidate trades can disappear
for valid execution reasons, but the system still lacks a global decision
provenance layer that explains why each name is held, not held, clipped,
filtered, or blocked.

The observed production-shaped portfolio can carry a relatively large number of
positions because multiple sleeves select local top-N books before the allocator
combines them. The research question is whether this architecture preserves
lower-conviction names that dilute alpha, or whether the additional names
provide enough risk, drawdown, liquidity, and turnover benefit to justify the
capital allocation.

## 2. Current Architecture Observed

- Sleeve-local top-N construction selects names inside each sleeve before global
  portfolio assembly.
- Sleeve-strength allocation scales active sleeve outputs based on regime and
  sleeve metadata.
- Position caps constrain final single-name weights and can leave residual cash
  or redistribute sleeve budget.
- Minimum gross exposure and cash deployment logic attempt to keep capital
  invested within risk-policy bounds.
- Turnover and risk controls can suppress or scale changes from target intent.
- Execution min-notional, live price, post-sell rebudget, buying power, and
  broker response controls can further change realized orders.
- The resulting holding count is implicit. It emerges from sleeve membership,
  per-sleeve top-N values, overlap, caps, cash routing, and live construction
  constraints rather than from a single global optimizer objective.

## 3. Hypothesis

The current sleeve-merge architecture may dilute alpha by preserving
lower-conviction names after the highest-ranked ideas have already captured most
of the available expected-return signal. Holding count should become an output
of a research-validated optimizer that weighs expected alpha, risk contribution,
diversification, turnover, liquidity, cash drag, execution residuals, and broker
constraints.

This is a hypothesis, not a production conclusion.

## 4. Research Questions

- Are bottom-quartile holdings adding risk-adjusted value?
- What is the marginal contribution of holdings ranked 11-30?
- How do top 10, top 15, top 20, top 25, and top 30 portfolios compare?
- Does concentration improve raw return at unacceptable drawdown or volatility?
- Does sleeve-level optimization underperform global optimization?
- What is the opportunity cost of execution rebudgeting, min-notional filters,
  and buying-power blocks?
- Which higher-ranked names are not held, and why?
- Which held names survive because of diversification mechanics rather than
  direct conviction?

## 5. Required Backtests

- Point-in-time universe and membership replay with security-id lineage.
- Current policy baseline with existing sleeve-local top-N, allocation, caps,
  turnover, cash, and execution residual assumptions clearly labeled.
- Global top-N frontier for top 10, 15, 20, 25, and 30 portfolios.
- Optimizer-derived holding-count variants where holding count is endogenous.
- Concentration guardrail variants with different max-weight, sector, effective
  N, turnover, liquidity, and min-notional policies.
- Sleeve-local versus global optimizer comparison using the same inputs.
- Turnover, cash drag, liquidity, min-notional, and buying-power sensitivity.
- Rebudget opportunity-cost replay that records intended notional, submitted
  notional, unexecuted notional, and next-period return for blocked or clipped
  candidates without look-ahead in decision construction.

## 6. Metrics

- CAGR
- Total return
- Sharpe
- Max drawdown
- Volatility
- Hit rate
- SPY excess return
- Turnover
- Cash drag
- HHI
- Effective N
- Max single-name exposure
- Sector exposure
- Min-notional and execution residuals
- Accepted, rejected, filled, clipped, and blocked order outcomes
- Opportunity cost of unexecuted intended notional

## 7. Guardrails For Future Concentration Tests

- Initial max single-name cap of 20-25%.
- Sector caps and sector-concentration reporting.
- Effective-N floor.
- Turnover cap and turnover-cost sensitivity.
- Liquidity and ADV constraints.
- Min-notional checks.
- Broker residual and posttrade reconciliation.
- Buying-power and post-sell rebudget replay.
- No production promotion until evidence passes governed review.
- No silent allocation, sizing, broker, scheduler, or risk-policy changes.

## 8. Acceptance Criteria For Research To Shadow

Research may move to shadow review only after all criteria are met:

- PIT membership and price lineage are decision-grade for the tested universe.
- Current-policy baseline is reproduced with documented tolerances.
- Global optimizer variants beat or justify their risk-adjusted tradeoff against
  the current baseline after turnover, cash drag, and liquidity assumptions.
- Bottom-quartile and rank-11-to-30 marginal contribution is measured over the
  full window and relevant subwindows.
- SPY excess return and hit-rate metrics use explicit benchmark and return
  conventions.
- Opportunity-cost analysis is based only on information available at decision
  time plus forward returns applied after the decision.
- Concentration guardrails are documented and machine-readable.
- MCP/reporting can explain why a name is held, why a higher-ranked name was not
  held, and what opportunity cost came from blocked or clipped trades.
- Owner review explicitly approves a shadow-only experiment. Paper/live
  promotion remains out of scope.

## 9. MCP And Reporting Requirements

Future MCP and reporting surfaces should answer:

- Why is this name held?
- Which model, sleeve, rank, score, and target weight produced this holding?
- Why was a higher-ranked name not held?
- Was the name excluded by risk, liquidity, turnover, min-notional, buying power,
  rebudgeting, or broker response?
- What intended notional was unexecuted?
- What opportunity-cost basis was used?
- Which code stage made the final decision?
- How did the current portfolio compare to global top-N and optimizer-derived
  alternatives?

The candidate trade lifecycle artifact is the execution-stage foundation for
this provenance surface. FR-105 should extend it only with schema-stable,
nullable provenance fields until upstream research artifacts can populate them
without inventing values.

## Dependencies

- FR-068 point-in-time universe and survivorship remediation.
- FR-069 research lab and modular sleeve evidence framework.
- FR-074 execution reliability and candidate lifecycle observability.
- FR-DH canonical research data hydration and catalog.
- Existing concentration-frontier and conviction-allocation research artifacts,
  treated as preliminary until PIT decision-grade inputs are complete.

## Non-Goals

- No allocator replacement.
- No production portfolio-construction change.
- No sleeve top-N change.
- No risk-threshold change.
- No sizing change.
- No broker/execution change.
- No live or paper promotion.
- No backtest using look-ahead membership, survivorship-biased membership, or
  forward returns in decision construction.

## Initial Status

FR-105 is active research. The first implementation step should be a
research-only design packet for the PIT global-optimizer replay contract and the
decision-provenance data model. The first evidence step should reproduce current
policy behavior before evaluating concentrated or optimizer-derived variants.

## Phase 0 Status

Status: IMPLEMENTED_RESEARCH_ONLY

Phase 0 defines a replay contract and nullable provenance schema for global
optimizer research. The artifact path is:

`outputs/research/fr_105/<RUN_ID_OR_DATE>/global_optimizer_replay_contract.json`

The Phase 0 contract is produced by:

`python scripts/research/build_fr105_replay_contract.py --trade-date YYYY-MM-DD`

This tooling is research-only. It reads existing execution, lifecycle,
portfolio, reconciliation, broker-position, and configuration artifacts when
available; it writes only FR-105 research artifacts. It does not invoke or
change allocator behavior, portfolio construction, sizing, broker submission,
execution, cron, live trading, paper trading, or promotion state.

## Phase 0 Replay Schema Overview

Required top-level sections:

- `metadata`
- `source_artifacts`
- `universe_snapshot`
- `sleeve_candidates`
- `current_portfolio`
- `constraints_snapshot`
- `execution_residuals`
- `provenance_schema_version`
- `validation_status`

The metadata section records `trade_date`, `generated_at`, `git_sha`,
`mode=research_only`, `fr_id=FR-105`, and the schema version. Source-artifact
fields record known paths for candidate lifecycle, target portfolio, sleeve
artifacts, execution results, reconciliation, broker positions, and price source.
Unknown values must remain explicit `null` or `"unavailable"`; Phase 0 must not
fabricate alpha, risk, ranking, sleeve, or portfolio values.

Each `sleeve_candidates` row uses the nullable provenance fields needed for
future optimizer research: ticker, sleeve, strategy, source model, lifecycle,
rank, score, conviction score, expected alpha, expected risk, target/current
weight and notional, delta notional, inclusion/exclusion reasons, data as-of,
and source artifact path.

The `constraints_snapshot` records available policy inputs such as max
single-name weight, turnover cap, min-trade dollars, cash target, buying power,
rebudget policy, and min-notional policy. Constraint values that are not present
in source artifacts must remain `null` or `"unavailable"`.

The `execution_residuals` section summarizes the candidate lifecycle when
available: planned, executable, intended, submitted, and filled counts;
suppressed and clipped counts; suppression and clipping reason counts; and
estimated unexecuted notional.

## Phase 0 Validation Contract

The validator checks that:

- required top-level sections exist;
- schema version and provenance schema version are present;
- required lists are well formed;
- unavailable values are explicit `null` or `"unavailable"`;
- known artifact paths are recorded in source-artifact fields;
- `mode=research_only` is enforced;
- no production execution modules are recorded as invoked.

## Phase 1 Status

Status: IMPLEMENTED_RESEARCH_ONLY

Phase 1 adds the current-policy baseline/control harness. The artifact path is:

`outputs/research/fr_105/<RUN_ID_OR_DATE>/phase1_current_policy_baseline.json`

The Phase 1 baseline is produced from a Phase 0 replay contract by:

`python scripts/research/run_fr105_phase1_baseline.py --trade-date YYYY-MM-DD`

The harness reproduces only the observable current-policy baseline snapshot from
the Phase 0 contract. It does not introduce a global optimizer, alternative
holding count, frontier selection, new sleeve ranking, new allocation method, or
new execution model. When positions, candidate lifecycle rows, price as-of
values, universe lineage, target portfolio artifacts, or execution residuals
are missing, the artifact records explicit `null` values and unavailable-field
diagnostics rather than fabricating a baseline.

Phase 1 remains non-runtime research infrastructure. It reads Phase 0 contracts
and writes only FR-105 research artifacts. It does not invoke or change
allocator behavior, portfolio construction, sizing, broker submission,
execution, cron, live trading, paper trading, or promotion state.

## Phase 1 PIT Controls

The Phase 1 artifact records:

- `trade_date`
- `data_asof`
- `universe_asof`
- `price_asof`
- `source_artifact_paths`
- `no_forward_returns_used=true`
- `no_production_modules_invoked=true`
- `unavailable_fields`

The baseline metrics are limited to values available in the Phase 0 contract:
position count, gross exposure, cash weight, max single-name weight, HHI,
effective N, turnover, planned candidates, submitted orders, filled orders,
suppressed count, clipped count, and estimated unexecuted notional. Missing
values must remain `null` or `"unavailable"`.

## Phase 0/1 Artifact Completeness

Status: IMPLEMENTED_RESEARCH_ONLY

Phase 0/1 artifact completeness is a deterministic readiness layer. It answers
whether the evidence required to evaluate future Alpha Chase research exists
for a run. The artifact path is:

`outputs/research/fr_105/<RUN_ID_OR_DATE>/phase01_artifact_completeness.json`

The report is produced by:

`python scripts/research/check_fr105_phase01_artifact_completeness.py --trade-date YYYY-MM-DD`

The report classifies required evidence as `FOUND`, `MISSING`, or
`UNAVAILABLE`. It does not infer missing fields and does not treat target
weights or allocation weights as alpha scores. Missing evidence blocks Alpha
Chase evaluation rather than being worked around.

This tooling is artifact-only. It reads existing FR-105 Phase 0/1 research
artifacts and writes only FR-105 research readiness output. It does not invoke
or change allocator behavior, portfolio construction, sizing, broker
submission, execution, cron, live trading, paper trading, live-pilot behavior,
or promotion state.

## Phase 2 Status

Status: IMPLEMENTED_RESEARCH_ONLY

Phase 2 adds the global top-N frontier research artifact. The artifact path is:

`outputs/research/fr_105/<RUN_ID_OR_DATE>/phase2_global_topn_frontier.json`

The Phase 2 frontier is produced from Phase 0 and Phase 1 artifacts by:

`python scripts/research/run_fr105_phase2_topn_frontier.py --trade-date YYYY-MM-DD`

The frontier constructs hypothetical equal-weight global top-N candidate
portfolios for N values such as 5, 10, 15, 20, 25, and 30. Candidate selection
is deterministic and uses only available Phase 0 provenance fields:
conviction score, score, expected alpha, rank, and ticker. Duplicate tickers are
deduplicated inside each variant; the best available candidate record for that
ticker is retained for research comparison.

Phase 2 compares those hypothetical variants with the Phase 1 current-policy
baseline using available static metrics only: position count, HHI, effective N,
max single-name weight, overlap, names added/removed, and estimated static
turnover from current-policy weights when current weights are available. Phase 2
does not compute forward returns unless PIT-safe return data is explicitly
available and validated. The current implementation sets
`no_forward_returns_used=true`.

If candidate data, current-policy positions, PIT as-of fields, or baseline
metrics are missing, Phase 2 emits explicit `null` values, unavailable reasons,
and sparse-data diagnostics. It does not fabricate candidate ranks, scores,
alpha, risk, returns, prices, or portfolio weights.

Phase 2 remains non-runtime research infrastructure. It does not promote an
optimizer, does not replace allocation, and does not invoke or change allocator
behavior, optimizer behavior, portfolio construction, sizing, broker
submission, execution, cron, live trading, paper trading, or promotion state.

## Phase 3 Status

Status: IMPLEMENTED_RESEARCH_ONLY

Phase 3 adds optimizer-derived holding-count research. The artifact path is:

`outputs/research/fr_105/<RUN_ID_OR_DATE>/phase3_optimizer_derived_holding_count.json`

The Phase 3 holding-count research artifact is produced from Phase 0, Phase 1,
and Phase 2 artifacts by:

`python scripts/research/run_fr105_phase3_optimizer_holding_count.py --trade-date YYYY-MM-DD`

Phase 3 is a research scoring layer only. It does not promote a production
optimizer and does not change the current allocator, sleeve construction,
portfolio construction, sizing, broker submission, execution, cron, live
trading, paper trading, or promotion state.

## Phase 3 Research Decision Policy

The initial research policy ranks Phase 2 global top-N variants using only
ex-ante or same-time fields available in the frontier artifact:

- aggregate conviction score;
- average rank;
- effective N;
- HHI;
- max single-name weight;
- estimated turnover from current policy;
- data-completeness diagnostics.

The policy explicitly does not use forward returns, realized returns,
post-decision price moves, production optimizer outputs, unrecorded allocator
state, or broker side effects. The current implementation sets
`no_forward_returns_used=true`.

Phase 3 guardrails:

- max single-name weight must be `<= 0.25`;
- effective N must be `>= 5` where available;
- estimated turnover from current policy must be within the configured turnover
  cap where available;
- duplicate tickers are not allowed;
- selected count must be greater than zero;
- selected count must not exceed available unique candidate count;
- data completeness must be acceptable.

Tie-breakers are deterministic: higher research score, higher aggregate
conviction, lower HHI, lower estimated turnover, lower top-N, then
lexicographic variant id.

If Phase 2 is sparse or no variant can pass the research policy, Phase 3 emits
an explicit no-selection status. Sparse inputs produce
`NO_SELECTION_SPARSE_INPUT` and preserve `null` / `"unavailable"` diagnostics
rather than fabricating scores, returns, ranks, or holding-count conclusions.

## Next Phases

- Shadow Alpha Chase framework: default-off framework artifact at
  `outputs/research/fr_105/shadow_alpha_chase_framework.json`.
- Phase 4: Shadow portfolios / paper-only candidate tracking after Phase 0/1
  completeness is no longer sparse.
- Phase 5: Promotion review.
