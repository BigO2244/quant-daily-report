# Caerus Friday Refactor Backlog

## Status

This file is now a compatibility entry point. The Friday Refactor (FR) system
has been split into clearer governance documents under `docs/governance/` so
the active backlog stays operator-readable and deployed history remains
separate from future work.

## Canonical FR Documents

| Document | Purpose |
|---|---|
| `docs/governance/fr_active_backlog.md` | Current actionable FR work: `BACKLOG`, `READY`, `READY_VALIDATED`, `IN_PROGRESS`, `PROMOTION_READY`, and `DEPLOYED_OBSERVING`. |
| `docs/governance/fr_registry.md` | Historical FR record: deployed FRs, reviewed deferred FRs, wave history, rollback references, and deployment notes. |
| `docs/governance/fr_governance_model.md` | FR methodology: status semantics, observation windows, blast radius, validation, rollback, deployment, and maintenance rules. |
| `docs/governance/operational_lessons.md` | Operational lessons, anti-patterns, and deployment/recovery guidance learned from FR work. |

## Current Operating Model

Use `docs/governance/fr_active_backlog.md` for planning and execution. It should
remain concise and should not accumulate deployed history or long implementation
specs.

Use `docs/governance/fr_registry.md` for audit history. It preserves deployed
FRs, reviewed deferred items, final or current operational state, rollback
references, and observation status.

Use `docs/governance/fr_governance_model.md` when deciding whether an FR is
ready to implement, promote, observe, or close.

## Phase 4 Direction

Phase 4 remains focused on artifact governance and operational telemetry:

- artifact registry and ownership matrix
- operational health aggregation
- latest publication freshness manifests
- documentation and generated artifact separation
- retention and backup policy
- validation isolation
- semantic contract validation
- partial execution state normalization

Phase 4 is non-trading, non-execution, additive, and operational by default.
It must not introduce broker changes, strategy promotion, cron timing changes,
distributed orchestration, microservices, Kubernetes, or broad scheduler
rewrites.
