# Operational Lessons

## Purpose

This document preserves operational lessons from FR deployment, recovery, and
governance work. It is not an implementation spec. Use it to shape future FR
scope, rollout sequencing, validation, rollback, and observation criteria.

## Deployment Lessons

- Wave-based deployment keeps blast radius visible and gives each group of
  changes a clear rollback boundary.
- Runtime smoke checks can expose orchestration issues that static shell syntax
  checks do not catch.
- `git revert`, push, and VM fast-forward is the preferred deployed-source
  rollback path.
- SCP is emergency-only. Any SCP source change must be verified and reconciled
  back through git.
- Local implementation status is not deployment status. FRs are not deployed
  until they pass through the canonical git and VM flow.

## Recovery Lessons

- Degraded-state simulation is necessary for recovery work; happy-path tests are
  not enough.
- Recovery paths should validate their own outputs before allowing execution to
  continue.
- Partial recovery output must fail closed when execution-critical artifacts are
  incomplete.
- Runtime evidence should be preserved during rollback. Deleting artifacts or
  logs to make a state look clean is not a rollback strategy.

## Artifact Lessons

- Additive observability artifacts are safer than overwriting or deleting
  canonical runtime artifacts.
- `latest` files are convenience surfaces, not trustworthy state unless they
  carry freshness and provenance metadata.
- Generated markdown, diagnostics, reports, and runtime artifacts should not sit
  beside canonical operator docs without clear generated-file labeling.
- Artifact ownership, freshness, retention, and consumer semantics should be
  documented before cleanup automation is introduced.

## Governance Lessons

- `DEPLOYED_OBSERVING` needs explicit exit criteria. Otherwise it becomes a
  vague holding state rather than an operational control.
- Documentation drift is operational risk when docs define deployment,
  rollback, cron, scheduler, dashboard, or artifact contracts.
- Deferred FRs should retain rationale and re-entry criteria so they do not
  silently return as unscoped implementation work.
- Low-blast-radius governance work should remain additive first; code paths can
  adopt the model after operator trust semantics are clear.

## Anti-Patterns To Avoid

- Treating VM working tree drift as canonical source.
- Marking an FR `DEPLOYED` without observation evidence when runtime behavior
  still needs proof.
- Using `latest` artifacts as authoritative state without trade date,
  publication time, producer, and source path metadata.
- Combining docs cleanup, runtime producer changes, and scheduler changes in one
  rollback boundary.
- Introducing distributed orchestration to solve unclear state ownership.

## Phase 4 Application

Phase 4 should continue the move from execution automation toward operational
state governance. The highest-leverage work is still explicit artifact ownership,
freshness semantics, operator health synthesis, retention policy, validation
isolation, and documentation hygiene.
