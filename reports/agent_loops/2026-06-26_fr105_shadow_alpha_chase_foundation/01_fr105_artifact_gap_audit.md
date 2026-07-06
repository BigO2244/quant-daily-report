# FR-105 Artifact Gap Audit

Audit date: 2026-06-26

Scope: Review, artifact completion plan, and reporting design only. No optimizer, sizing, broker, execution, live-pilot, cron, order-submission, or production portfolio-construction behavior was changed.

## Summary

FR-105 is already documented as `ACTIVE_RESEARCH` / `RESEARCH_ONLY` / `NON_EXECUTIONAL`. The repo also contains untracked research-only FR-105 Phase 0, Phase 1, Phase 2, and Phase 3 modules and tests.

The gap is not that no FR-105 schema exists. The gap is artifact completeness. The only local generated FR-105 bundle found was `outputs/research/fr_105/2026-06-25/`, and it is sparse: Phase 0 passed schema validation but did not resolve candidate lifecycle, target portfolio, sleeve artifacts, execution results, broker positions, price source, or universe lineage. As a result, Phase 1 has no current-policy positions or trades, Phase 2 has no candidate pool, and Phase 3 has no selected research variant.

No local 2026-06-26 precompute, execution, or run artifacts were found in this checkout during this review. Any claim about today's successful trade therefore requires copying or resolving the canonical 2026-06-26 artifact bundle before FR-105 can make same-day statements.

## Evidence Reviewed

- `docs/governance/fr_active/fr_105_global_portfolio_optimizer_and_decision_provenance.md`
- `research/fr105_replay_contract.py`
- `research/fr105_phase1_baseline.py`
- `research/fr105_phase2_topn_frontier.py`
- `research/fr105_phase3_holding_count.py`
- `scripts/research/build_fr105_replay_contract.py`
- `scripts/research/run_fr105_phase1_baseline.py`
- `scripts/research/run_fr105_phase2_topn_frontier.py`
- `scripts/research/run_fr105_phase3_optimizer_holding_count.py`
- `Tests/test_fr105_replay_contract.py`
- `Tests/test_fr105_phase1_baseline.py`
- `Tests/test_fr105_phase2_topn_frontier.py`
- `Tests/test_fr105_phase3_holding_count.py`
- `outputs/research/fr_105/2026-06-25/*`
- Existing reporting/provenance work in `core/construction_provenance.py` and `core/email_reporting_sections.py`
- Prior CIO audit package under `reports/agent_loops/2026-06-26_cio_email_alpha_concentration_review/`

## Files/Modules Inspected

| File/module | Relevance |
| --- | --- |
| `docs/governance/fr_active/fr_105_global_portfolio_optimizer_and_decision_provenance.md` | Defines FR-105 scope, phases, non-goals, and research-only boundary. |
| `research/fr105_replay_contract.py` | Phase 0 replay contract builder and validator. |
| `research/fr105_phase1_baseline.py` | Phase 1 current-policy baseline/control artifact builder. |
| `research/fr105_phase2_topn_frontier.py` | Phase 2 hypothetical global top-N frontier builder. |
| `research/fr105_phase3_holding_count.py` | Phase 3 research-only holding-count selector. |
| `Tests/test_fr105_*` | Existing sparse-input, validation, and no-production-module-import coverage. |
| `outputs/research/fr_105/2026-06-25/` | Local generated FR-105 artifact evidence. |
| `outputs/runs/`, `outputs/precompute/` | Local source artifact inventory for run/precompute evidence. |
| `outputs/shadow_candidates/` | Existing operational shadow evidence and candidate comparison surfaces. |

## Current Phase 0/1 Artifact State

| Area | Existing artifact support | Local 2026-06-25 generated state | Gap |
| --- | --- | --- | --- |
| Candidate universe | `universe_snapshot` exists in Phase 0 schema. | `status=unavailable`, `ticker_count=None`, no source artifact path. | Need PIT universe source, as-of, security-id lineage, and included/excluded universe counts. |
| Global candidate ranking | Phase 2 can rank Phase 0 candidates by conviction, score, expected alpha, rank, ticker. | Candidate pool count is zero. | Need Phase 0 candidate rows populated from canonical sleeve/lifecycle artifacts. |
| PIT/as-of lineage | Phase 0 and Phase 1 have data/universe/price as-of fields. | `data_asof=None`, `price_asof=None`, universe as-of only set to trade date in sparse contract. | Need real source as-of values from decision artifacts, price source, and universe membership. |
| Score source provenance | Phase 0 candidate schema has `score`, `conviction_score`, `expected_alpha`. | No candidate rows. | Need source-labeled score fields; never infer alpha scores from target weights or allocation weights. |
| Sleeve source provenance | Phase 0 candidate schema has `sleeve_id`, `strategy_id`, `source_model`, `source_artifact_path`. | No sleeve artifacts resolved. | Need each sleeve/source artifact path and local/global rank context. |
| Current holdings | Phase 0 `current_portfolio` and Phase 1 baseline positions exist. | `positions_count=None`, no positions. | Need pre/post/current holdings artifact resolution and as-of source label. |
| Target weights | Phase 0 candidates have target fields and source artifact slot. | No target portfolio path found. | Need target portfolio/signals/precompute target source wired into Phase 0. |
| Constraint application | Phase 0 constraints snapshot exists. | Config constraints found for max position, turnover, min trade dollars; sector/liquidity/effective-N/cash/rebudget unavailable. | Need constraint trace with active/applied/available status, affected tickers, before/after values. |
| Suppression reasons | Phase 0 execution residuals can summarize candidate lifecycle. | Lifecycle artifact not found; residual counts null. | Need candidate lifecycle source and reason counts for the target date/run. |
| Holding-count frontier | Phase 2/3 schemas exist. | Phase 2 variants all `candidate_pool_unavailable`; Phase 3 `NO_SELECTION_SPARSE_INPUT`. | Need populated Phase 0/1 before frontier has decision value. |
| Shadow comparison output | Existing operational shadow artifacts exist under `outputs/shadow_candidates`; no FR-105 Phase 4 artifact exists. | Not implemented for FR-105 Alpha Chase. | Need default-off shadow comparison artifact, not broker/paper/live integration. |

## Missing Artifact List

P0 missing or incomplete artifacts for Phase 0/1 completion:

- Canonical 2026-06-26 run/precompute artifact bundle in this checkout.
- PIT candidate universe snapshot with security-id lineage and as-of fields.
- Populated Phase 0 `sleeve_candidates` from candidate lifecycle and/or canonical sleeve artifacts.
- Source-labeled global candidate rank table.
- Source-labeled score provenance table.
- Sleeve artifact inventory with per-sleeve candidate source path/status.
- Target portfolio/signals artifact path resolution.
- Current holdings and weights source resolution.
- Constraint application trace, not only config snapshot.
- Suppression/clipping reason table from lifecycle artifacts.
- Phase 1 current-policy baseline generated from populated Phase 0.
- Artifact completeness report that says which Phase 0/1 fields are decision-grade versus unavailable.

## Proposed Artifact Schemas

### Phase 0/1 Completeness Report

Path:

`outputs/research/fr_105/<RUN_ID_OR_DATE>/phase01_artifact_completeness.json`

Top-level fields:

- `schema_version`
- `trade_date`
- `run_id`
- `mode = research_only`
- `alpha_chase_default = off`
- `trading_behavior_changed = false`
- `source_artifacts`
- `required_artifacts`
- `field_completeness`
- `blocking_gaps`
- `warnings`
- `validation_status`

Each `required_artifacts` row:

- `artifact_key`
- `expected_path`
- `resolved_path`
- `status`: `FOUND`, `MISSING`, `STALE`, `UNAVAILABLE`
- `required_for_phase`: `phase0`, `phase1`, `phase4_shadow`
- `source_of_truth`
- `fallback_allowed`: boolean
- `fallback_behavior`: usually `unavailable`

### Global Candidate Ranking Artifact

Path:

`outputs/research/fr_105/<RUN_ID_OR_DATE>/global_candidate_ranking.json`

Rows:

- `ticker`
- `security_id`
- `asof`
- `sleeve_sources`
- `sleeve_local_rank`
- `global_rank`
- `score`
- `score_source`
- `conviction_score`
- `expected_alpha`
- `expected_risk`
- `target_weight`
- `current_weight`
- `inclusion_status`
- `exclusion_reason`
- `source_artifact_path`
- `unavailable_fields`

Rules:

- `score`, `conviction_score`, and `expected_alpha` must be artifact-backed.
- Target weights and allocation weights must never be used as alpha scores.
- Missing values must be `null` or `"unavailable"`.

### Constraint Trace Artifact

Path:

`outputs/research/fr_105/<RUN_ID_OR_DATE>/phase01_constraint_trace.json`

Rows:

- `constraint_id`
- `constraint_type`
- `source_artifact_path`
- `configured_value`
- `active_status`
- `applied_status`
- `affected_tickers`
- `pre_constraint_weight`
- `post_constraint_weight`
- `reason`
- `unavailable_fields`

## Findings

### Finding 1: FR-105 schema is ahead of local artifact availability

Severity: High

FR-105 Phase 0/1 modules and tests exist, but the local generated bundle is sparse and not decision-grade for Alpha Chase. It validates shape, not completeness.

Proposed fix: Add a Phase 0/1 artifact completeness report and treat sparse status as a blocking condition for any Alpha Chase interpretation.

Risk classification: Artifact-only / shadow-only.

Validation required: Fixture with complete artifacts, missing artifacts, and stale artifacts.

### Finding 2: Same-day 2026-06-26 artifacts are absent locally

Severity: High

No local 2026-06-26 `outputs/precompute` or `outputs/runs` files were found. The repo has a successful-trading context from the operator, but not canonical same-day local artifact evidence.

Proposed fix: Resolve or copy the canonical 2026-06-26 artifact bundle before generating same-day FR-105 outputs.

Risk classification: Artifact-only.

Validation required: Source inventory must report exact artifact path, trade date, run id, and status.

### Finding 3: Phase 0 currently captures config constraints better than applied constraints

Severity: Medium

The sparse Phase 0 contract found `paper/config_paper.json` values for max position, turnover, and min trade dollars, but did not capture actual constraint application by ticker.

Proposed fix: Add a separate constraint trace sourced from construction provenance, candidate lifecycle, risk artifacts, and precompute/execution payloads.

Risk classification: Reporting-only / artifact-only.

Validation required: One fixture where constraints are applied and one where configured constraints are present but unused.

### Finding 4: Shadow Alpha Chase comparison is not yet a first-class FR-105 artifact

Severity: Medium

Operational shadow artifacts exist, and FR-105 Phase 2/3 research artifacts exist, but no Phase 4 Alpha Chase comparison artifact ties current sleeve-merge, global Alpha Chase, and optional core-satellite variants together.

Proposed fix: Add a default-off Phase 4 shadow comparison artifact after Phase 0/1 completeness is no longer sparse.

Risk classification: Shadow-only.

Validation required: No broker imports, no execution modules invoked, no forward returns unless explicitly PIT-safe and post-decision.

## Implementation Plan

1. Add Phase 0/1 artifact completeness report builder.
2. Make Phase 0 contract resolution stricter and more visible, not more permissive.
3. Populate Phase 0 from canonical candidate lifecycle, construction provenance, target/signals, and position sources when present.
4. Regenerate Phase 1 baseline only after Phase 0 has populated candidates and holdings.
5. Add Phase 4 design and tests, but keep it default-off and shadow-only.
6. Surface completeness status in reports/emails only after artifact schema is stable.

## Open Questions

1. Where is the canonical 2026-06-26 run/precompute artifact bundle?
2. Which source is canonical for global candidate score: `conviction_score`, `score`, `expected_alpha`, or a promoted FR-105 score field?
3. Should Phase 0 use posttrade positions, pretrade positions, target weights, or all three for the current-policy baseline comparison?
4. What universe definition should Alpha Chase use: current sleeve union, approved research universe, or FR-068/RDP PIT universe?
5. Should Phase 4 be generated daily once artifacts are present, or only manually until governance approval?
