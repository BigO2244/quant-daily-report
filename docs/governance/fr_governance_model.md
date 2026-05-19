# FR Governance Model

## Purpose

This document defines how Caerus Friday Refactor (FR) work is proposed,
validated, promoted, observed, and closed. It is methodology only; it does not
change runtime behavior.

## Source Documents

- Active roadmap: `docs/governance/fr_active_backlog.md`
- Historical registry: `docs/governance/fr_registry.md`
- Operational lessons: `docs/governance/operational_lessons.md`
- Compatibility entry points:
  - `docs/friday_refactor_backlog.md`
  - `docs/fr_execution_ledger.md`

## Status Model

Preferred flow:

```text
BACKLOG -> READY -> READY_VALIDATED -> IN_PROGRESS -> PROMOTION_READY -> DEPLOYED_OBSERVING -> DEPLOYED
```

Status meanings:

| Status | Meaning |
|---|---|
| `BACKLOG` | Useful work, not ready for implementation. |
| `READY` | Scope, dependencies, rollback path, and validation plan are clear. |
| `READY_VALIDATED` | Pre-work audit confirms source ownership, runtime exposure, and dependencies are safe enough to begin. |
| `IN_PROGRESS` | Implementation or foundation work is underway. |
| `PROMOTION_READY` | Locally completed and validated, but not deployed or observed. |
| `DEPLOYED_OBSERVING` | Deployed with a specific observation window still open. |
| `DEPLOYED` | Deployed, validated, observed, and no special observation window remains. |
| `REVIEWED_DEFERRED` | Reviewed but intentionally not active. Track in registry, not active backlog. |

## Observation Window Semantics

`DEPLOYED_OBSERVING` is for changes that are deployed but still require runtime
evidence before being considered settled.

An FR exits `DEPLOYED_OBSERVING` only when its documented observation criteria
are met and the result is recorded in the registry. Do not invent operational
results; if evidence is absent, leave the FR observing.

Common observation criteria examples:

- N successful trading sessions after deployment.
- No rollback events during the observation window.
- No degraded-state incidents attributable to the FR.
- Relevant validators or targeted tests remain clean.
- Expected runtime artifacts are present and fresh.
- No stale/latest publication incidents attributable to the FR.
- Operator-facing dashboards or reports show the intended state without masking
  degraded conditions.

Observation metadata should be lightweight:

| Field | Meaning |
|---|---|
| `Observation Status` | `not_started`, `observing`, `satisfied`, `blocked`, or `not_required`. |
| `Observation Criteria` | The evidence required to close the window. |
| `Observation Evidence` | Links or notes proving criteria were met. |
| `Current State` | Current operational interpretation. |

## Blast Radius Framework

| Blast Radius | Meaning | Examples |
|---|---|---|
| `LOW` | Docs, read-only tooling, isolated reporting, or additive diagnostics with no scheduler/runtime effect. | Governance docs, read-only registry, static taxonomy. |
| `MEDIUM` | Research/reporting code, generated artifacts, dashboard rendering, CI hardening, or non-blocking shadow behavior. | Health aggregator, latest freshness manifests, test isolation. |
| `HIGH` | Cron, deployment, execution, reconciliation, broker state, order submission, recovery gates, or canonical runtime state. | Self-heal execution gates, broker submission semantics, cron deployment. |

Escalate blast radius when a change can affect execution timing, broker orders,
canonical state, rollback paths, or operator interpretation of trading-critical
health.

## Required FR Metadata

Keep metadata human-readable. Use this minimum set for active and historical FRs:

| Field | Purpose |
|---|---|
| `Phase` | Wave or phase, such as Wave 1, Wave 3, or Phase 4. |
| `Status` | Current FR status. |
| `Blast Radius` | LOW, MEDIUM, or HIGH. |
| `Dependencies` | Required predecessor FRs or operating assumptions. |
| `Observation Status` | Observation state or `not_required`. |
| `Introduced` | Date or promotion boundary when known. |
| `Current State` | Current operating interpretation. |
| `Rollback Reference` | How to safely reverse or ignore the change. |

## Validation Philosophy

- Select validation before implementation.
- Prefer read-only validation for governance and docs.
- Prefer simulation before promotion for recovery, scheduler, and
  execution-adjacent work.
- Do not run trading workflows, regenerate broker artifacts, install cron, or
  mutate runtime state as a validation shortcut.
- Keep validation proportional to blast radius.
- Report commands run and whether they passed.

## Rollback Discipline

- Prefer `git revert`, push, and VM fast-forward for deployed source changes.
- Preserve runtime evidence; do not delete logs or artifacts as a rollback
  shortcut.
- Stop on unexplained VM drift.
- SCP is exception-only and must be reconciled through git later.
- Any cleanup automation must have dry-run, manifest, backup, and rollback
  behavior.

## Deployment Rules

- `origin/main` is canonical deployable source.
- The scheduler VM is a deploy target, not source of truth.
- Local commits do not deploy until pushed and fast-forwarded on the VM.
- Cron and service changes require explicit deployment review.
- Phase 4 governance and telemetry work is non-trading and non-execution by
  default.

## Maintenance Checklist

- [ ] Market/execution window risk reviewed.
- [ ] Current local and VM source ownership clear.
- [ ] Blast radius classified.
- [ ] Dependencies documented.
- [ ] Validation selected before mutation.
- [ ] Rollback path identified.
- [ ] Runtime artifact impact documented.
- [ ] Observation criteria identified for `DEPLOYED_OBSERVING`.
- [ ] Documentation impact reviewed.
