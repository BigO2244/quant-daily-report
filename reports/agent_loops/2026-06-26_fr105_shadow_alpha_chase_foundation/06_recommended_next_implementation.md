# Recommended Next Implementation

Audit date: 2026-06-26

Scope: Implementation plan only. Do not implement Alpha Chase yet. Do not change optimizer, sizing, broker, execution, live-pilot, cron, paper, production, or order-submission behavior.

## Executive Summary

Implementation is ready for a narrow Phase 0/1 artifact-completion patch. It is not ready for Alpha Chase construction, optimizer changes, paper/live influence, or live-pilot influence.

The next patch should create a deterministic artifact completeness layer around the existing FR-105 Phase 0/1 artifacts, then populate Phase 0/1 from canonical artifacts when those artifacts are present. If source artifacts are absent, the output must say `unavailable` and identify the missing source.

Phase 4 shadow Alpha Chase comparison should be designed now but implemented only after Phase 0/1 is no longer sparse for at least one canonical run.

## Evidence Reviewed

- FR-105 governance doc and current research modules.
- Existing sparse local `outputs/research/fr_105/2026-06-25/*`.
- Existing shadow artifacts under `outputs/shadow_candidates/`.
- Prior CIO reporting/concentration/governance package.
- Current git staged/dirty state.

## Files/Modules Inspected

| File/module | Role |
| --- | --- |
| `research/fr105_replay_contract.py` | Phase 0 source contract. |
| `research/fr105_phase1_baseline.py` | Phase 1 current-policy baseline. |
| `research/fr105_phase2_topn_frontier.py` | Existing top-N frontier research. |
| `research/fr105_phase3_holding_count.py` | Existing holding-count research. |
| `scripts/research/build_fr105_replay_contract.py` | Phase 0 CLI. |
| `scripts/research/run_fr105_phase1_baseline.py` | Phase 1 CLI. |
| `Tests/test_fr105_*` | Existing research-only test foundation. |
| `core/construction_provenance.py` | New approved construction provenance source for future Phase 0 enrichment. |
| `core/candidate_trade_lifecycle.py` | Existing candidate lifecycle source, currently untracked in this worktree. |

## Recommended Next Patch

### Patch 1: FR-105 Phase 0/1 Artifact Completeness

Risk classification: Artifact-only / reporting-only.

Proposed files:

- `research/fr105_phase01_completeness.py`
- `scripts/research/check_fr105_phase01_artifact_completeness.py`
- `Tests/test_fr105_phase01_completeness.py`
- Optional doc update to `docs/governance/fr_active/fr_105_global_portfolio_optimizer_and_decision_provenance.md`

Artifact:

`outputs/research/fr_105/<RUN_ID_OR_DATE>/phase01_artifact_completeness.json`

Behavior:

- Read existing Phase 0 and Phase 1 artifacts.
- Check required source artifacts and key fields.
- Classify each as `FOUND`, `MISSING`, `SPARSE`, `STALE`, or `UNAVAILABLE`.
- Emit blocking gaps and warnings.
- Do not call broker, paper broker, live-pilot, execution, optimizer, or sizing modules.
- Do not infer score fields from target or allocation weights.
- Write deterministic JSON.

Validation:

- Full-source fixture.
- Sparse-source fixture matching current local 2026-06-25 state.
- Missing-artifact fixture.
- Stale-date fixture.
- No-production-module-import fixture.

### Patch 2: Phase 0 Source Resolution Hardening

Risk classification: Artifact-only.

Proposed files:

- `research/fr105_replay_contract.py`
- `Tests/test_fr105_replay_contract.py`

Behavior:

- Improve artifact source inventory and status reporting.
- Add optional construction provenance input resolution.
- Add optional candidate lifecycle source resolution from explicit path.
- Add exact source-status fields instead of relying on null paths alone.
- Preserve existing schema compatibility or version bump intentionally.

Do not:

- Import execution modules.
- Generate trades.
- Recompute targets.
- Change run artifacts.

### Patch 3: Shadow Alpha Chase Phase 4 Schema Scaffold

Risk classification: Shadow-only.

Prerequisite: Phase 0/1 completeness can produce a non-sparse artifact from a canonical run.

Proposed files:

- `research/fr105_phase4_shadow_alpha_chase.py`
- `scripts/research/run_fr105_phase4_shadow_alpha_chase.py`
- `Tests/test_fr105_phase4_shadow_alpha_chase.py`

Artifact:

`outputs/research/fr_105/<RUN_ID_OR_DATE>/phase4_shadow_alpha_chase_comparison.json`

Behavior:

- Compare current sleeve-merge, global Alpha Chase, optional core-satellite.
- Use only artifact-backed scores.
- Fail closed to `NO_SHADOW_COMPARISON_SPARSE_INPUT`.
- Default off; no runtime integration.

### Patch 4: Reporting Integration

Risk classification: Reporting-only.

Prerequisite: Phase 0/1 completeness artifact exists and has tests.

Proposed files:

- `core/email_reporting_sections.py`
- `paper/build_execution_email.py`
- `scripts/send_trading_confirmation_email.py`
- `scripts/format_precompute_email.py`
- `scripts/research/build_dashboard_v1.py`
- Related tests.

Behavior:

- Render concise FR-105 status and missing-source summary.
- Do not render full ticker tables in email.
- Keep dashboard placement under research/shadow/governance.

## Files Proposed By This Review Package

Created in this pass:

- `reports/agent_loops/2026-06-26_fr105_shadow_alpha_chase_foundation/01_fr105_artifact_gap_audit.md`
- `reports/agent_loops/2026-06-26_fr105_shadow_alpha_chase_foundation/02_shadow_alpha_chase_design.md`
- `reports/agent_loops/2026-06-26_fr105_shadow_alpha_chase_foundation/03_portfolio_construction_doctrine_draft.md`
- `reports/agent_loops/2026-06-26_fr105_shadow_alpha_chase_foundation/04_reporting_integration_plan.md`
- `reports/agent_loops/2026-06-26_fr105_shadow_alpha_chase_foundation/05_governance_merge_safety.md`
- `reports/agent_loops/2026-06-26_fr105_shadow_alpha_chase_foundation/06_recommended_next_implementation.md`

Proposed for future implementation:

- `research/fr105_phase01_completeness.py`
- `scripts/research/check_fr105_phase01_artifact_completeness.py`
- `Tests/test_fr105_phase01_completeness.py`
- `research/fr105_phase4_shadow_alpha_chase.py`
- `scripts/research/run_fr105_phase4_shadow_alpha_chase.py`
- `Tests/test_fr105_phase4_shadow_alpha_chase.py`
- `docs/governance/portfolio_construction_doctrine.md` after Brett approval.

## Implementation Readiness

Ready now:

- Phase 0/1 artifact completeness report.
- Sparse artifact diagnostics.
- Score-source guard tests.
- Doctrine draft review.

Not ready:

- Alpha Chase optimizer behavior.
- Paper/live target influence.
- Live-pilot influence.
- Production dashboard headline claims.
- Forward-return performance claims.

Blocked until canonical artifacts are available:

- Same-day 2026-06-26 FR-105 conclusions.
- Current versus Alpha Chase comparison for today's successful trade.

## Validation Required

For this report-only pass:

```bash
git status --short
git diff --cached --name-only
.venv/bin/python -m py_compile core/construction_provenance.py core/email_reporting_sections.py scripts/build_construction_provenance.py
.venv/bin/pytest Tests/test_construction_provenance.py Tests/test_execution_email.py -q
```

For the next Phase 0/1 implementation patch:

```bash
.venv/bin/python -m py_compile research/fr105_replay_contract.py research/fr105_phase1_baseline.py research/fr105_phase01_completeness.py scripts/research/check_fr105_phase01_artifact_completeness.py
.venv/bin/pytest Tests/test_fr105_replay_contract.py Tests/test_fr105_phase1_baseline.py Tests/test_fr105_phase01_completeness.py -q
```

## Items Requiring Brett Approval

- Creating canonical `docs/governance/portfolio_construction_doctrine.md`.
- Choosing Alpha Chase first shadow variant: top-N, Phase 3 selected, equal-weight, score-weighted, or core-satellite.
- Authoritative score field for Alpha Chase.
- Minimum diversification guardrails.
- Any paper/live/optimizer/sizing/broker/live-pilot influence.
- Any use of stale or fallback artifacts for same-day CIO claims.

## Open Questions

1. Should Phase 0/1 completeness be the only next patch, or should Phase 4 schema scaffolding be included?
2. Should the canonical 2026-06-26 artifact bundle be copied into this checkout before any FR-105 artifact generation?
3. Should the report package be staged with the current approved reporting merge or left unstaged?
4. What exact guardrail values should be used for the first shadow Alpha Chase run?
5. Should core-satellite be included in the first implementation or remain a doctrine-only option?
