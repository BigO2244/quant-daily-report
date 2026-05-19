# Caerus Documentation Governance

## Purpose

Documentation drift is operational risk. Caerus uses documentation as part of
the control system for production-like paper trading, scheduled research,
dashboard publishing, and VM deployment. Docs must describe actual behavior, not
aspirational workflows.

## Source-of-Truth Hierarchy

For operational truth:

1. Broker state and persisted broker artifacts for positions, NAV, fills, and
   reconciliation.
2. `origin/main` for deployable source code and scheduler definitions.
3. VM installed state for currently running cron, services, nginx, and preserved
   recovery stashes/patches.
4. Agent-facing docs for workflow expectations and safe operating rules.
5. Runtime outputs and logs as evidence, not source.

For documentation:

1. `AGENTS.md` is the compact agent-facing handoff.
2. `docs/deployment_workflow.md` controls deployment governance.
3. `docs/OPERATIONS.md` and `docs/runbook.md` control operator procedures.
4. Architecture-specific docs control subsystem contracts.
5. `docs/governance/` tracks FR active work, deployed/deferred history,
   governance methodology, and operational lessons.

## Documentation Update Triggers

Update or review docs when a change affects:

- deployment model, VM workflow, SCP usage, or rollback process
- cron schedule, scheduler ownership, or GitHub Actions role
- execution, reconciliation, broker state, or order-submission gates
- dashboard data source, route, nginx/service ownership, or publication path
- artifact schema, required output, or generated-file contract
- strategy promotion state or live/paper/shadow responsibility
- feature flags, operating modes, or emergency procedures
- FR status, validation evidence, or deployment readiness

## Required Updates by Change Type

| Change Type | Required Documentation Review |
|---|---|
| Deployment or VM workflow | `AGENTS.md`, `docs/deployment_workflow.md`, `docs/OPERATIONS.md`, `docs/runbook.md` |
| Cron or scheduler change | `AGENTS.md`, `scripts/crontab.txt`, `docs/OPERATIONS.md`, `docs/runbook.md` |
| Execution/reconciliation change | `AGENTS.md`, `docs/OPERATIONS.md`, `docs/execution_integrity_runbook.md` if applicable |
| Dashboard publishing change | `AGENTS.md`, dashboard specs, deployment workflow if VM state changes |
| Artifact schema change | owning subsystem doc, tests, operator docs if used operationally |
| Strategy promotion or execution scope | `AGENTS.md`, architecture docs, runbook |
| Friday refactor | `docs/governance/fr_active_backlog.md`, `docs/governance/fr_registry.md`, `docs/governance/fr_governance_model.md`, `docs/governance/operational_lessons.md` |

If documentation cannot be updated in the same change, record an explicit
blocker or follow-up. Do not silently leave stale operational instructions.

## Drift Detection Checklist

When auditing docs, check for:

- SCP-era instructions that imply SCP is the normal deploy path.
- GitHub Actions described as scheduled production execution when VM cron owns it.
- Missing distinction between source, runtime artifacts, logs, and deployed state.
- Rollback steps that rely on destructive cleanup.
- Cron install instructions without a warning that cron changes are explicit
  deployment work.
- Dashboard docs that imply heuristic or blended broker metrics.
- FR items marked done without validation, docs, or deployment evidence.
- Agent instructions that conflict with operator docs.

## Agent and Governance Synchronization

Agent instructions must remain aligned with operational docs:

- `AGENTS.md` should summarize the current rules.
- Detailed deployment procedure belongs in `docs/deployment_workflow.md`.
- Operator procedures belong in `docs/OPERATIONS.md` and `docs/runbook.md`.
- Active FR work belongs in `docs/governance/fr_active_backlog.md`.
- Deployed and reviewed deferred FR history belongs in
  `docs/governance/fr_registry.md`.
- FR methodology belongs in `docs/governance/fr_governance_model.md`.
- FR deployment and recovery lessons belong in
  `docs/governance/operational_lessons.md`.

If these conflict, stop and reconcile before changing production-adjacent code.

## Operational Review Cadence

- Review deployment and operator docs after every deployment model change.
- Review FR backlog and ledger during Friday maintenance planning.
- Review `AGENTS.md` after any strategy promotion, scheduler change, or safety
  rule change.
- Review dashboard docs when dashboard source-of-truth or publication path
  changes.
- Review documentation drift after incidents, reconciliation events, or emergency
  SCP hotfixes.
