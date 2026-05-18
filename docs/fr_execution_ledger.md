# Caerus FR Execution Ledger

## Purpose

This ledger preserves operational history for Friday Refactor (FR) work. It is
an audit trail, not a scratchpad: rows record what was promoted, how it was
validated, what rollback path was retained, and what operators should observe
after deployment.

Do not rewrite history to hide earlier risk. Add corrective rows or notes when
later audits refine the operating model.

## Status Semantics

- `DEPLOYED`: promoted through git, fast-forwarded on the VM, validated, and no
  special observation window remains.
- `DEPLOYED_OBSERVING`: deployed and healthy, but operators should continue to
  watch the named surfaces because the change touches orchestration, cache, or
  recovery behavior.
- `REVIEWED_DEFERRED`: reviewed but intentionally not promoted.
- `BACKLOG`: not yet prepared for implementation.

## Deployed Wave Summary

| Wave | Date | FRs | Operational Theme | Validation Summary | Observation Focus |
|---|---|---|---|---|---|
| Wave 1 | 2026-05-15 | FR-004, FR-006, FR-009, FR-011, FR-013 | Reporting resilience and CI governance | Targeted pytest slices, workflow YAML validation, `scripts/operational_validation.py` PASS. | Dependabot noise, pinned action availability, report artifact continuity. |
| Wave 2 | 2026-05-15 | FR-001, FR-012 | Shadow orchestration observability and cache namespace isolation | Shadow wrapper tests, execution pipeline tests, shell syntax, operational validator, local runtime smoke. | Shadow step status artifacts and expected first-run cache misses. |
| Wave 3 | 2026-05-15 | FR-005 | Recovery integrity and degraded-state fail-closed behavior | Bundle validator tests, execution pipeline tests, shell syntax, operational validator, degraded-state simulations. | Self-heal recovery artifacts, bundle validation failures, repeated recovery attempts. |

## Planned Phase Summary

| Phase | FRs | Operational Theme | Promotion Posture | Observation Focus |
|---|---|---|---|---|
| Phase 4 | FR-015 through FR-023 | Artifact governance and operational telemetry | Foundation docs in local planning/WIP; non-trading and non-execution until individual FRs are promoted. | Artifact ownership, health synthesis, latest freshness, retention, validation isolation, documentation taxonomy, partial-state semantics. |

Phase 4 is intentionally not a deployment record. It is the next governance
backlog phase and should be promoted one FR at a time with isolated rollback
boundaries. The recommended execution order is FR-015, FR-017, FR-018, FR-023,
FR-019, FR-020, FR-016, FR-021, then FR-022.

Current Phase 4 foundation documents:

- `docs/artifact_governance.md`
- `docs/operational_health_aggregator.md`
- `docs/documentation_taxonomy.md`

## Execution History

| Date | FR | Status | Commit / Promotion Boundary | Validation | Rollback Reference | Docs Updated | Notes |
|---|---|---|---|---|---|---|---|
| 2026-05-04 | FR-003 managed ticker exceptions | REVIEWED_DEFERRED | Local WIP only | Planned: `pytest Tests/test_price_cache_only_hydrator.py Tests/test_ticker_exceptions.py -q`; dry-run hydration. | Empty or remove ticker exceptions. | `docs/price_hydration.md` in local WIP. | Not part of Waves 1-3. Do not treat as deployed until it goes through git promotion. |
| 2026-05-08 | FR-008 deployment reconciliation/governance recovery | DEPLOYED | `c8c0e10` plus governance follow-up | VM backup, stash, fetch, `git merge --ff-only origin/main`, governance commit push, VM pull, `git status`, `git log -1 --oneline`, shell syntax. | Git revert preferred; preserved VM patches/stashes during recovery. | `AGENTS.md`, `docs/deployment_workflow.md`, `docs/documentation_governance.md`, `docs/OPERATIONS.md`, `docs/runbook.md`, ledger/backlog. | Restored deterministic git-based VM deployment. SCP is now exception-only. |
| 2026-05-15 | FR-004 feedback-loop rolling index | DEPLOYED | Wave 1 reporting/learning commit | `Tests/test_feedback_loop_artifacts.py`; combined Wave 1 pytest slice; operational validation. | Revert Wave 1 reporting commit or stop reading/writing the additive index; dated JSON artifacts remain canonical. | `docs/feedback_loop_artifacts.md`, ledger/backlog. | Adds compact performance rows without changing execution, broker, reconciliation, or scheduler behavior. |
| 2026-05-15 | FR-006 required vs optional artifact health | DEPLOYED | Wave 1 reporting/learning commit | `Tests/test_portfolio_learning_report.py`; combined Wave 1 pytest slice; operational validation. | Revert artifact-health classification changes. | `docs/feedback_loop_artifacts.md`, ledger/backlog. | Missing optional learning artifacts remain visible without making the core scoreboard unavailable. |
| 2026-05-15 | FR-009 GitHub Actions SHA pinning | DEPLOYED | Wave 1 CI/governance commit | Workflow YAML parse; `scripts/operational_validation.py` PASS. | Revert pinned SHA refs only if a SHA is invalid or unavailable. | `docs/operational_validation.md`, ledger/backlog. | All workflow `uses:` refs are pinned to immutable SHAs. |
| 2026-05-15 | FR-011 workflow permission minimization | DEPLOYED | Wave 1 CI/governance commit | Workflow permission validation; operational validator PASS. | Restore prior permission blocks only if workflow writes fail. | `docs/operational_validation.md`, ledger/backlog. | Workflow-scope `contents: write` removed; job-level elevation remains only where needed. |
| 2026-05-15 | FR-013 dependency monitoring governance | DEPLOYED | Wave 1 CI/governance commit | Dependabot syntax/config review; operational validator PASS. | Disable or remove `.github/dependabot.yml` if advisory noise becomes unacceptable. | `docs/operational_validation.md`, ledger/backlog. | Adds advisory-first Dependabot monitoring for pip and GitHub Actions without auto-merge. |
| 2026-05-15 | FR-001 shadow wrapper decomposition | DEPLOYED_OBSERVING | Wave 2 shadow orchestration commit | `Tests/test_shadow_daily_wrapper.py`, `Tests/test_execution_pipeline_integration.py`, `bash -n scripts/run_shadow_candidates_daily.sh`, local shadow smoke. | Revert wrapper decomposition commit to restore prior inline wrapper. | Ledger/backlog, `AGENTS.md`, operations docs. | Shadow remains non-blocking and now writes generate/latest/reconciliation step status artifacts. |
| 2026-05-15 | FR-012 CI cache namespace isolation | DEPLOYED_OBSERVING | Wave 2 cache namespace commit | Workflow YAML validation; cache-key review; operational validator PASS. | Revert cache key namespace commit if cache misses create unacceptable workflow instability. | Ledger/backlog, deployment docs. | Cache keys/restore prefixes include `github.repository_id`; first post-deploy run may miss and regenerate caches. |
| 2026-05-15 | FR-005 self-heal recovery integrity hardening | DEPLOYED_OBSERVING | Wave 3 recovery integrity commit | `Tests/test_execution_pipeline_integration.py`, `Tests/test_precompute_bundle_validation.py`, `scripts/operational_validation.py`, shell syntax, degraded-state simulations. | Revert FR-005 commit to restore the prior recovery gate and remove additive validation/status artifacts. | Ledger/backlog, `AGENTS.md`, deployment and runbook docs. | Execution now requires full bundle validation; partial self-heal output fails closed and writes recovery observability. |
| 2026-05-15 | FR-007 parquet scaling review | REVIEWED_DEFERRED | Documentation review only | No runtime validation required. | Ignore/remove `docs/parquet_scaling_review.md`; single parquet remains canonical. | `docs/parquet_scaling_review.md`. | Storage migration deferred until runtime pressure justifies it. |
| 2026-05-15 | FR-010 deterministic dependency governance | REVIEWED_DEFERRED | Not promoted in Waves 1-3 | Advisory dependency review only. | Leave current dependency installation behavior unchanged. | `docs/dependency_governance.md` may exist as WIP. | Hash-based lockfile governance remains future work; do not mix with deployed waves. |

## Lessons Learned

- Wave deployment kept blast radius visible and made rollback boundaries clear.
- Runtime smoke tests caught orchestration issues that static shell tests would
  not have surfaced.
- Degraded-state simulation found a fail-open risk in self-heal recovery before
  promotion; that risk is now covered by full bundle validation.
- Additive observability artifacts are preferable to overwriting or deleting
  canonical runtime artifacts during recovery.
- `git revert` plus VM fast-forward is the canonical rollback model; destructive
  cleanup is not a rollback strategy.
