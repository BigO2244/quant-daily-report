# Shadow Alpha Chase Design

Audit date: 2026-06-26

Scope: Design only. No Alpha Chase optimizer, sizing, broker, execution, live-pilot, cron, paper, or production behavior was implemented or changed.

## Summary

The desired next state is a shadow-only comparison layer that evaluates what would have happened under an Alpha Chase construction policy without influencing current sleeve-merge targets, broker orders, paper/live execution, live-pilot selection, or cron.

The first useful design is not a new production optimizer. It is a deterministic Phase 4 FR-105 artifact that compares:

1. Current sleeve-merge portfolio.
2. Hypothetical global Alpha Chase portfolio.
3. Optional core-satellite variant.

The design must fail closed to `unavailable` when Phase 0/1 inputs are sparse, and it must never infer alpha scores from target weights or allocation weights.

## Evidence Reviewed

- FR-105 governance doc and current Phase 0/1/2/3 modules.
- Local sparse `outputs/research/fr_105/2026-06-25/*` bundle.
- Existing operational shadow artifacts under `outputs/shadow_candidates/`.
- Existing shadow concentration research module `research/shadow_concentration.py`.
- Existing construction provenance reporting module `core/construction_provenance.py`.
- Prior CIO Alpha Concentration audit.

## Files/Modules Inspected

| File/module | Relevance |
| --- | --- |
| `research/fr105_replay_contract.py` | Phase 0 source contract and candidate provenance foundation. |
| `research/fr105_phase1_baseline.py` | Current-policy baseline metrics. |
| `research/fr105_phase2_topn_frontier.py` | Existing global top-N hypothetical variant builder. |
| `research/fr105_phase3_holding_count.py` | Research-only variant selection policy and guardrails. |
| `research/shadow_concentration.py` | Existing non-capital shadow comparison pattern. |
| `outputs/shadow_candidates/<date>/` | Existing shadow candidate and performance artifact family. |
| `core/construction_provenance.py` | Current sleeve-merge construction explanation artifact. |

## Current Architecture Summary

Current construction is sleeve-merge first:

1. Sleeves create local candidate books.
2. Sleeve-local top-N/rank logic selects local candidates.
3. Regime/sleeve allocation scales sleeve books.
4. Allocator/risk/cash constraints shape final target weights.
5. Execution artifacts convert targets into planned/intended/submitted orders.

This is not a global Alpha Chase optimizer. Current breadth is expected because sleeve breadth, local normalization, position caps, cash logic, deadbands, and execution filters all operate after local candidate selection.

## Proposed Phase 4 Artifact

Path:

`outputs/research/fr_105/<RUN_ID_OR_DATE>/phase4_shadow_alpha_chase_comparison.json`

Top-level schema:

- `metadata`
- `input_artifacts`
- `pit_controls`
- `shadow_policy`
- `portfolio_variants`
- `comparison_metrics`
- `suppression_and_constraint_trace`
- `data_quality`
- `validation_status`

Metadata:

- `schema_version = fr105_phase4_shadow_alpha_chase_comparison.v1`
- `trade_date`
- `run_id`
- `generated_at`
- `git_sha`
- `mode = shadow_only`
- `default_off = true`
- `capitalized = false`
- `broker_orders_allowed = false`
- `trading_behavior_changed = false`
- `production_execution_modules_invoked = []`

Input artifacts:

- Phase 0 replay contract path/status.
- Phase 1 current-policy baseline path/status.
- Phase 2 top-N frontier path/status.
- Phase 3 holding-count path/status.
- Construction provenance path/status, if available.
- Candidate lifecycle path/status, if available.
- Current positions path/status.
- Target portfolio/signals path/status.
- Shadow candidate artifacts path/status, if used for comparison only.

Pit controls:

- `data_asof`
- `universe_asof`
- `price_asof`
- `no_forward_returns_used`
- `no_production_modules_invoked`
- `unavailable_fields`
- `source_artifact_paths`

Shadow policy:

- `policy_id = alpha_chase_shadow_v1`
- `selection_source = artifact_backed_scores_only`
- `score_fields_allowed = [conviction_score, score, expected_alpha]`
- `score_fields_prohibited = [target_weight, allocation_weight, final_target_weight, final_allocation_weight]`
- `weighting_method`: `equal_weight_top_n` initially, later `score_weighted_with_caps`
- `guardrails`
- `failure_modes`

Portfolio variant rows:

- `variant_id`
- `variant_type`: `current_sleeve_merge`, `global_alpha_chase`, `core_satellite`
- `selected_tickers`
- `weights`
- `position_count`
- `cash_weight`
- `gross_exposure`
- `max_single_name_weight`
- `HHI`
- `effective_N`
- `sector_exposure`
- `aggregate_score`
- `score_source_coverage`
- `average_rank`
- `estimated_turnover_from_current_policy`
- `constraint_status`
- `unavailable_reason`
- `unavailable_fields`

Comparison metrics:

- `current_vs_alpha_chase_overlap`
- `names_added`
- `names_removed`
- `names_reduced`
- `names_increased`
- `position_count_delta`
- `HHI_delta`
- `effective_N_delta`
- `max_weight_delta`
- `sector_exposure_delta`
- `estimated_turnover_delta`
- `aggregate_score_delta`
- `score_coverage_delta`
- `suppressed_higher_ranked_candidates`
- `top_not_held_candidates`

## Daily Run Flow

1. Build or validate Phase 0 replay contract.
2. Build or validate Phase 1 current-policy baseline.
3. If Phase 0/1 are sparse, emit Phase 4 with `status=NO_SHADOW_COMPARISON_SPARSE_INPUT`.
4. Build or read Phase 2 global top-N variants.
5. Build or read Phase 3 research-selected variant.
6. Construct Phase 4 variants:
   - Current sleeve-merge from Phase 1/construction provenance.
   - Global Alpha Chase from Phase 2/3 selected candidates.
   - Core-satellite variant only if Brett approves the doctrine and schema.
7. Compute static ex-ante comparison metrics.
8. Record source artifacts and unavailable fields.
9. Write deterministic JSON with sorted tickers and sorted keys.
10. Optionally render a markdown summary under the same output directory.

No broker, paper broker, live-pilot, execution, sizing, or cron module is imported or invoked.

## Variant Definitions

### Current Sleeve-Merge

Source: Phase 1 baseline plus construction provenance.

Purpose: Baseline control, not a recommendation.

Missing behavior: If holdings or targets are missing, set weights and metrics to `unavailable`.

### Global Alpha Chase

Source: Phase 2/3 candidate selection.

Initial method: top-N equal weight, guardrail constrained.

Later method, only after approval: score-weighted with caps and effective-N floor.

Missing behavior: If score source coverage is insufficient, set `unavailable_reason=score_provenance_unavailable`.

### Core-Satellite

Source: current sleeve-merge core plus Alpha Chase satellite.

Initial status: optional design only, not implemented.

Purpose: Test whether concentration can be introduced without fully replacing sleeve architecture.

Requires Brett approval because it defines a doctrine-level compromise.

## Metrics To Compare

- Position count.
- Cash weight.
- Gross exposure.
- Max single-name weight.
- HHI.
- Effective N.
- Sector exposure and cap usage.
- Estimated turnover from current policy.
- Score-backed row count.
- Unavailable score row count.
- Aggregate conviction score.
- Average global rank.
- Top-not-held candidates.
- Suppressed or blocked candidates and reasons.
- Names added, removed, increased, reduced.
- Current-policy overlap.
- Constraint pass/fail status.
- Sparse-input status.

Forward return metrics are out of scope for the initial same-day artifact unless a PIT-safe return convention is explicitly added. Any later forward return must use post-decision data only and label its return convention.

## Required Inputs

- Phase 0 replay contract with populated candidate rows.
- Phase 1 current-policy baseline with positions and metrics.
- Source-labeled candidate score fields.
- Current holdings/weights.
- Target weights.
- Constraint snapshot and trace.
- Candidate lifecycle suppression/clipping reasons.
- PIT universe/security lineage.
- Optional sector mapping.
- Optional shadow candidate artifacts for parallel comparison only.

## Validation Plan

- Sparse-input fixture emits `NO_SHADOW_COMPARISON_SPARSE_INPUT`.
- Complete fixture emits all three variants without broker/execution imports.
- Score guard fixture proves target/allocation weights are not used as alpha scores.
- Determinism fixture proves repeated writes are byte-stable.
- Missing sector/universe/current-position fixture degrades to `unavailable`.
- No-production-module fixture checks prohibited execution/broker modules are not imported.

## Findings

### Finding 1: Phase 4 is the right boundary for Alpha Chase

Severity: High

Alpha Chase should start as a shadow-only comparison artifact after Phase 0/1 completeness, not as an allocator or execution change.

Proposed fix: Implement `phase4_shadow_alpha_chase_comparison.json` only after artifact completeness passes.

Risk classification: Shadow-only.

### Finding 2: Current Phase 0/1 sparse state blocks useful Phase 4 output

Severity: High

The existing local Phase 2/3 outputs already show what happens with sparse inputs: no candidates, no comparison, no selected variant.

Proposed fix: Make Phase 0/1 artifact completion the next patch before Phase 4 implementation.

Risk classification: Artifact-only.

### Finding 3: Core-satellite is a governance question, not just an engineering variant

Severity: Medium

Core-satellite changes the doctrine from "pure alpha chase" to "alpha chase with continuity guardrail."

Proposed fix: Put core-satellite behind Brett approval in the doctrine draft.

Risk classification: Governance/design only until approved.

## Open Questions

1. Should Alpha Chase start as top 5, top 10, or Phase 3 selected top-N?
2. Should the first Alpha Chase variant be equal-weight or score-weighted?
3. What minimum score provenance coverage is required before a shadow comparison is considered useful?
4. Is core-satellite a desired design path or should the shadow test compare only current versus global Alpha Chase?
5. Should forward-return attribution be deferred until after a full Phase 0/1 artifact completeness pass?
