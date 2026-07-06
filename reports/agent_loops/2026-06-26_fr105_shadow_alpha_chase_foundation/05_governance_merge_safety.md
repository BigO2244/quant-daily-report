# Governance / Merge Safety

Audit date: 2026-06-26

Scope: Governance and merge-safety review for FR-105 Phase 0/1 artifact completion and shadow-only Alpha Chase foundation. No runtime behavior changed.

## Summary

The safe merge path is to keep the already approved reporting/provenance patch staged exactly as-is and keep this FR-105 package as report-only. Dirty execution files remain excluded from staging.

FR-105 Phase 0/1 artifact completion is safe if it remains additive, research-only, deterministic, and default-unavailable. Shadow Alpha Chase design is safe as a report/design artifact. Any implementation that changes optimizer outputs, target weights, sizing, broker submission, live-pilot selection, cron, or paper/live allocation is blocked without explicit Brett approval.

## Evidence Reviewed

- `git status --short`
- `git diff --cached --name-only`
- Current staged reporting/provenance file list.
- Dirty execution files in the worktree.
- FR-105 governance doc and research modules.
- Prior CIO governance/safety review.

## Files/Modules Inspected

| File/module | Risk relevance |
| --- | --- |
| `scripts/live_pilot_execute.py` | Dirty, execution/live-pilot-impacting, must not be staged in this workstream. |
| `scripts/run_precomputed_alpaca_execution.py` | Dirty, execution-impacting, must not be staged in this workstream. |
| `research/fr105_*` | Research-only artifacts and validators; untracked in current dirty tree. |
| `scripts/research/run_fr105_*` | Research artifact CLI wrappers; untracked in current dirty tree. |
| `core/construction_provenance.py` | Approved staged reporting/provenance patch. |
| `core/email_reporting_sections.py` | Approved staged reporting/provenance patch. |
| `paper/build_execution_email.py` | Approved staged reporting patch. |
| `scripts/send_trading_confirmation_email.py` | Approved staged reporting patch. |
| `Tests/test_construction_provenance.py` | Approved staged tests. |
| `Tests/test_execution_email.py` | Approved staged tests. |

## Current Index State

Staged files at review time:

- `Tests/test_construction_provenance.py`
- `Tests/test_execution_email.py`
- `core/construction_provenance.py`
- `core/email_reporting_sections.py`
- `paper/build_execution_email.py`
- `scripts/build_construction_provenance.py`
- `scripts/send_trading_confirmation_email.py`

Explicitly dirty but not staged:

- `scripts/live_pilot_execute.py`
- `scripts/run_precomputed_alpaca_execution.py`

## Approve / Block Matrix

| Work item | Classification | Verdict | Reason |
| --- | --- | --- | --- |
| This six-file FR-105 report package | Reporting-only | Approve | Adds reports only; no runtime files. |
| Phase 0/1 artifact completeness report builder | Artifact-only | Approve for next patch if isolated | Safe if it reads existing artifacts and writes only under `outputs/research/fr_105/`. |
| Populating Phase 0 from existing candidate lifecycle/construction provenance | Artifact-only | Approve with tests | Safe if no execution modules are invoked and missing fields remain unavailable. |
| Phase 4 shadow Alpha Chase comparison artifact | Shadow-only | Approve for design; implement after Phase 0/1 completeness | Must remain default-off and non-capital. |
| Precompute/execution/confirmation email FR-105 status blocks | Reporting-only | Approve after artifact schema stabilizes | Must be concise and source-labeled. |
| Dashboard FR-105 shadow panel | Reporting-only | Approve after artifact schema stabilizes | Must stay under research/shadow, not broker/live controls. |
| Core-satellite variant | Governance/design | Needs Brett approval before implementation | It changes doctrine interpretation. |
| Alpha Chase optimizer objective | Optimizer-impacting | Block | Requires separate approval, backtest, shadow evidence, and rollback. |
| Paper target/config changes for concentration | Paper-impacting | Block | Requires explicit approval and paper dry-run validation. |
| Live-pilot influence from Alpha Chase | Live-pilot-impacting | Block | Requires FR-104 approval and exact order review. |
| Any broker/order-submission behavior change | Execution-impacting | Block | Out of scope. |
| Staging dirty execution files | Execution-impacting | Block | Explicitly excluded by Brett. |

## Findings

### Finding 1: Reports are safe to add, but should remain unstaged until Brett decides

Severity: Low

This package is report-only, but the index already contains the approved reporting/provenance patch. Mixing staged merge content and new report files should be deliberate.

Proposed fix: Leave this report package unstaged unless Brett explicitly asks to stage it.

Risk classification: Reporting-only.

### Finding 2: Existing dirty execution files remain the main merge safety risk

Severity: Critical

`scripts/live_pilot_execute.py` and `scripts/run_precomputed_alpaca_execution.py` are dirty and execution-adjacent. They must not be staged for this merge.

Proposed fix: Continue selective staging only. Recheck `git diff --cached --name-only` before any commit.

Risk classification: Execution-impacting if staged.

### Finding 3: FR-105 research modules are untracked and should not be promoted by accident

Severity: Medium

The worktree contains untracked FR-105 modules/tests. They may be useful for the next implementation patch, but this review does not approve staging them.

Proposed fix: Treat FR-105 implementation as the next patch with its own review and validation.

Risk classification: Artifact-only / shadow-only when isolated.

## Required Tests

For this report-only package:

- `git status --short`
- `git diff --cached --name-only`

For approved reporting/provenance merge:

- `.venv/bin/python -m py_compile core/construction_provenance.py core/email_reporting_sections.py scripts/build_construction_provenance.py`
- `.venv/bin/pytest Tests/test_construction_provenance.py Tests/test_execution_email.py -q`

For next FR-105 artifact-only implementation:

- `.venv/bin/python -m py_compile research/fr105_replay_contract.py research/fr105_phase1_baseline.py research/fr105_phase2_topn_frontier.py research/fr105_phase3_holding_count.py`
- `.venv/bin/pytest Tests/test_fr105_replay_contract.py Tests/test_fr105_phase1_baseline.py Tests/test_fr105_phase2_topn_frontier.py Tests/test_fr105_phase3_holding_count.py -q`
- Add tests for artifact completeness and score-source prohibition.

## Rollback Plan

Report-only rollback:

- Delete or ignore `reports/agent_loops/2026-06-26_fr105_shadow_alpha_chase_foundation/`.

Artifact-only rollback:

- Stop running the FR-105 builder.
- Ignore/delete generated `outputs/research/fr_105/<date>/phase01_artifact_completeness.json`.
- Revert isolated research module/test changes.

Shadow-only rollback:

- Stop running Phase 4 shadow comparison.
- Ignore/delete `phase4_shadow_alpha_chase_comparison.json`.
- Keep paper/live/execution unaffected.

Execution safety rollback:

- Do not stage excluded dirty files.
- If staged accidentally, unstage only those files with `git restore --staged <path>`; do not reset the worktree.

## Open Questions

1. Should this report package be staged with the approved reporting patch or kept as unstaged review output?
2. Should the next FR-105 patch include only Phase 0/1 completeness, or also Phase 4 schema scaffolding?
3. Should untracked FR-105 Phase 2/3 files be reviewed before Phase 0/1 completion, or deferred?
4. Who approves core-satellite as a doctrine option?
5. Should local missing 2026-06-26 artifacts block any same-day FR-105 generation?
