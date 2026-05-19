# FR Registry

## Purpose

This registry is the canonical historical record for Caerus Friday Refactor
(FR) work. It preserves deployed history, reviewed deferred items, rollback
references, validation summaries, and final or current operational state.

Active upcoming work belongs in `docs/governance/fr_active_backlog.md`.
Methodology belongs in `docs/governance/fr_governance_model.md`.

## Wave Summary

| Phase | Date | FRs | Status | Operational Theme | Observation Focus |
|---|---|---|---|---|---|
| Wave 1 | 2026-05-15 | FR-004, FR-006, FR-009, FR-011, FR-013 | Mixed: deployed | Reporting resilience and CI governance. | Dependabot noise, pinned action availability, report artifact continuity. |
| Wave 2 | 2026-05-15 | FR-001, FR-012 | `DEPLOYED_OBSERVING` | Shadow orchestration observability and cache namespace isolation. | Shadow step status artifacts and expected first-run cache misses. |
| Wave 3 | 2026-05-15 | FR-005 | `DEPLOYED_OBSERVING` | Recovery integrity and degraded-state fail-closed behavior. | Self-heal recovery artifacts, bundle validation failures, repeated recovery attempts. |
| Phase 4 | 2026-05-17 onward | FR-015, FR-017, FR-018, FR-023 foundations | `IN_PROGRESS` locally | Artifact governance and operational telemetry foundations. | Governance docs only; no runtime producers changed. |

## Deployed FRs

| FR | Phase | Status | Blast Radius | Introduced | Current State | Rollback Reference |
|---|---|---|---|---|---|---|
| FR-004 feedback-loop rolling index | Wave 1 | `DEPLOYED` | LOW | 2026-05-15 | Compact learning/performance rows are additive; dated JSON remains canonical. | Revert reporting commit or stop reading/writing additive index. |
| FR-006 required vs optional artifact health | Wave 1 | `DEPLOYED` | LOW | 2026-05-15 | Optional learning artifacts remain visible without blocking core scoreboard. | Revert artifact-health classification changes. |
| FR-008 git/VM deployment governance | Governance recovery | `DEPLOYED` | HIGH | 2026-05-08 | `origin/main` is canonical; VM fast-forwards from git; SCP is exception-only. | Prefer `git revert`, push, VM fast-forward; preserve drift evidence. |
| FR-009 GitHub Actions SHA pinning | Wave 1 | `DEPLOYED` | MEDIUM | 2026-05-15 | Workflow `uses:` references are pinned to immutable SHAs. | Revert pinned SHA refs only if a SHA is invalid or unavailable. |
| FR-011 workflow permission minimization | Wave 1 | `DEPLOYED` | MEDIUM | 2026-05-15 | Workflow-scope `contents: write` removed; job-level elevation remains where needed. | Restore prior permission blocks only for confirmed permission failures. |
| FR-013 dependency monitoring governance | Wave 1 | `DEPLOYED` | LOW | 2026-05-15 | Dependabot monitors pip and GitHub Actions without auto-merge. | Disable or remove Dependabot config if advisory noise is unacceptable. |

## Deployed Observing FRs

| FR | Phase | Status | Blast Radius | Observation Status | Observation Criteria | Current State | Rollback Reference |
|---|---|---|---|---|---|---|---|
| FR-001 shadow wrapper decomposition | Wave 2 | `DEPLOYED_OBSERVING` | MEDIUM | observing | Shadow generate/latest/reconciliation step artifacts appear as expected; no shadow failure blocks trading. | Shadow remains non-blocking and now writes step status artifacts. | Revert wrapper decomposition commit to restore prior inline wrapper. |
| FR-005 self-heal recovery integrity | Wave 3 | `DEPLOYED_OBSERVING` | HIGH | observing | Self-heal artifacts reflect continuation or fail-closed halt; repeated recovery attempts remain visible; bundle validation gates execution. | Execution requires full bundle validation; partial self-heal fails closed. | Revert FR-005 commit; preserve existing recovery artifacts as evidence. |
| FR-012 CI cache namespace isolation | Wave 2 | `DEPLOYED_OBSERVING` | MEDIUM | observing | Repository-scoped cache misses/regeneration behave as expected; no workflow instability from namespace migration. | Cache keys include `github.repository_id`; first post-deploy misses may be expected. | Revert cache key namespace commit if cache misses cause unacceptable instability. |

Do not mark an observing FR `DEPLOYED` until evidence satisfies its observation
criteria. If evidence is unavailable, leave the FR observing.

## Reviewed Deferred FRs

| FR | Phase | Status | Blast Radius | Current State | Deferred Rationale | Re-entry Criteria |
|---|---|---|---|---|---|---|
| FR-003 managed ticker exceptions | Pre-Wave review | `REVIEWED_DEFERRED` | MEDIUM | Local WIP only; not deployed through Waves 1-3. | Needs isolated promotion package and hydration validation. | Dry-run hydration, targeted tests, artifact-only rollout plan. |
| FR-007 parquet scaling review | Wave 1 review | `REVIEWED_DEFERRED` | LOW | Advisory review only; single parquet remains canonical. | Runtime pressure does not justify storage migration yet. | Repeated memory/runtime pressure or coverage-sidecar insufficiency. |
| FR-010 deterministic dependency governance | Wave 1 review | `REVIEWED_DEFERRED` | MEDIUM | Advisory dependency docs/inputs exist; hash enforcement not promoted. | Premature enforcement risks VM/GitHub install failures. | Clean install validation, APScheduler decision, rollback path, advisory audit. |
| FR-022 dependency hash enforcement | Phase 4 | `REVIEWED_DEFERRED` | MEDIUM | Future extension of FR-010. | Hash enforcement should wait until dependency baselines and emergency update procedure are proven. | Same as FR-010 plus workflow install policy decision. |

## Historical Execution Notes

| Date | FR | Validation Summary | Docs / Evidence | Notes |
|---|---|---|---|---|
| 2026-05-08 | FR-008 | VM backup, stash, fetch, fast-forward, status/log review, shell syntax. | `AGENTS.md`, deployment workflow, operations docs, runbook. | Restored deterministic git-based VM deployment. |
| 2026-05-15 | FR-004, FR-006 | `Tests/test_feedback_loop_artifacts.py`, `Tests/test_portfolio_learning_report.py`, operational validation. | Feedback loop docs and FR registry. | Reporting-only and additive. |
| 2026-05-15 | FR-009, FR-011, FR-013 | Workflow YAML parse, permission validation, Dependabot config review, operational validation. | Operational validation docs and FR registry. | CI governance hardened without workflow auto-merge. |
| 2026-05-15 | FR-001 | Shadow wrapper tests, execution integration tests, shell syntax, local shadow smoke. | Operations docs and FR registry. | Decomposed non-blocking shadow observability. |
| 2026-05-15 | FR-012 | Workflow YAML/cache key review, operational validation. | Deployment docs and FR registry. | Repository-scoped cache keys added. |
| 2026-05-15 | FR-005 | Execution integration tests, bundle validation tests, shell syntax, degraded-state simulations. | Operations docs, runbook, and FR registry. | Self-heal now fails closed unless full bundle validation passes. |
| 2026-05-17 | FR-015, FR-017, FR-018, FR-023 foundations | Docs-only validation. | Artifact governance, health aggregator, documentation taxonomy. | Foundation docs only; no runtime producers changed. |

## Registry Rules

- Preserve rollback references even after an FR is fully deployed.
- Preserve deferred rationale; do not reclassify deferred work as active without
  a new readiness review.
- Do not invent observation results. Record only evidence that exists.
- Keep implementation detail concise; link to owning docs for long specs.
