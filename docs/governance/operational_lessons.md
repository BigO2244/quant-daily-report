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

## Execution Reliability Lessons

- On 2026-05-26, the paper run halted `PARTIAL` after four sell orders were
  accepted and before five planned buys were submitted. The immediate trigger
  was `post_submit_artifact_failure:posttrade_state_capture_failed`.
- The failure pattern was sell-phase timeout plus a transient Alpaca
  `PARTIALLY_FILLED` order state without a resolvable `filled_qty`. The system
  halted conservatively instead of submitting buys against uncertain sell
  completion, which prevented state corruption and overbuying risk.
- The missing artifact was posttrade reconciliation evidence, not order
  submission evidence. Pretrade, postsell, and posttrade broker snapshots were
  preserved; `recon_posttrade_2026-05-26.json` was missing because posttrade
  state capture raised before publishing reconciliation.
- Hotfix `40fce71` keeps the conservative buy block when sell state is unsafe,
  but preserves posttrade evidence. If a partial sell can be resolved from
  broker position deltas, reconciliation proceeds. If it cannot be resolved,
  posttrade reconciliation is written as `NOT_COMPARABLE` with unresolved order
  metadata instead of failing artifact capture.
- Future execution-adjacent fixes should preserve this distinction: never mask
  unresolved broker state, but avoid losing available posttrade evidence.
- On 2026-05-27, buy-leg suppression required explicit governance because a
  suppressed buy phase can look superficially similar to a clean no-buy day if
  operator surfaces only show submitted orders. Planned buys, submitted buys,
  budget-skipped buys, guard-suppressed buys, and repair-eligible buys need
  separate state labels.
- HOTFIX-2026-05-27 observation criteria: preserve incident artifacts and logs;
  verify that operator/email evidence distinguishes planned-versus-submitted
  buy counts; verify that suppressed buys carry an explicit guard or budget
  reason; verify that repair guidance, if present, is broker-authoritative; and
  verify on the next buy-capable paper run that buys are either submitted or
  blocked with an explicit reason.
- The durable follow-up is FR-031 execution integrity contract. That contract
  should be designed before additional execution-contract changes so future
  fixes do not mix order planning, broker submission, artifact publication,
  suppression, and recovery eligibility into one ambiguous status.
- FR-031 implements that follow-up as an additive audit artifact and compact
  operator-summary status. The validator should remain non-routing in its first
  deployed version: it observes and classifies integrity failures, but does not
  create a new post-submit halt path unless a later FR explicitly promotes that
  behavior with its own validation and rollback boundary.

## Artifact Lessons

- Additive observability artifacts are safer than overwriting or deleting
  canonical runtime artifacts.
- `latest` files are convenience surfaces, not trustworthy state unless they
  carry freshness and provenance metadata.
- Generated markdown, diagnostics, reports, and runtime artifacts should not sit
  beside canonical operator docs without clear generated-file labeling.
- Artifact ownership, freshness, retention, and consumer semantics should be
  documented before cleanup automation is introduced.
- Source-readiness diagnostics should distinguish expected waiting states from
  actual failure states. Before the post-close hydration window, stale same-day
  shadow artifacts can be expected; after the window, missing hydration evidence
  becomes an operator action item.

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
