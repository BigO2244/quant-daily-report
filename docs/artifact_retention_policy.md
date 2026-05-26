# Artifact Retention And Backup Policy

## Purpose

FR-019 defines operator-readable retention and backup semantics for Caerus
runtime, telemetry, research, and validation artifacts.

This policy is governance only. It does not add cleanup automation, delete
artifacts, change producers, alter cron, mutate broker state, or reinterpret any
historical output.

## Principles

- Preserve execution and reconciliation evidence before reducing storage.
- Treat deletion as a governed operation, not an incidental maintenance task.
- Prefer dated artifacts over mutable latest publications for audit evidence.
- Back up incident evidence before cleanup or rollback.
- Keep test and smoke outputs separate from production runtime evidence.
- Do not use cleanup to hide stale, partial, or failed operational state.
- Require dry-run reporting before any future cleanup automation.

## Retention Classes

| Class | Examples | Minimum Policy | Backup Requirement | Cleanup Readiness |
|---|---|---|---|---|
| `critical_runtime_evidence` | Execution run roots, pretrade/posttrade reconciliation, broker snapshots, planned execution payloads. | Retain through the full audit and tax review window. | Back up before any cleanup or incident rollback. | Manual review only. |
| `workflow_evidence` | `outputs/workflow/<date>/*.json`, cron logs, self-heal validation artifacts. | Retain long enough to investigate scheduler and recovery incidents. | Back up when tied to an incident or failed run. | Dry-run report required before deletion. |
| `hydration_evidence` | `outputs/price_hydration/<date>/status.json`, hydration logs. | Retain dated status history for source-readiness investigations. | Back up if price cache freshness or shadow readiness is disputed. | Do not delete while related shadow artifacts depend on it. |
| `shadow_research_evidence` | `outputs/shadow_candidates/<date>/`, research clarity bundles, FR-030 packets. | Retain dated evidence used for attribution, promotion review, or timing-governance review. | Back up before backfill, repair, or governed migration work. | Safe only when superseded and no active review depends on it. |
| `generated_display` | Dashboard payloads, generated markdown/html reports, latest convenience exports. | Retain as operator convenience only when dated source evidence remains available. | Usually not required unless used in an incident review. | Can be regenerated if source artifacts remain. |
| `research_historical` | Backtests, alpha lab outputs, research notebooks, weekly review markdown. | Archive by research milestone and governance relevance. | Back up milestone evidence before major refactors. | Manual research-owner review. |
| `ephemeral_validation` | Test scratch directories, bounded `/tmp` outputs, smoke-test residue. | Keep out of canonical runtime paths whenever possible. | No backup required unless converted into incident evidence. | Preferred cleanup class after FR-020 isolation. |

## Backup Boundaries

Before cleanup or rollback involving operational evidence, preserve:

- current git HEAD and branch;
- `git status --short`;
- impacted artifact paths;
- execution run id or trade date;
- relevant logs;
- broker and reconciliation evidence when present;
- source-readiness and hydration diagnostics when shadow artifacts are involved.

Backups should be path-scoped and named with trade date or incident identifier.
SCP-only or manual VM backups must be reconciled through the normal git and
operator note process when they include source files. Runtime artifact backups
do not make the VM canonical source.

## Latest Publication Rules

`latest` publications are convenience surfaces. They may be overwritten by
normal publishing and should not be retained as audit evidence unless their
dated source artifact is also preserved.

Operators should not delete stale latest files to make health appear clean.
Staleness should be visible through freshness metadata, source-readiness
diagnostics, or operator notes.

## Incident Evidence Hold

Place an informal evidence hold before cleanup when any of the following are
true:

- execution ended `PARTIAL`, `HALTED`, `FAILED`, or `INDETERMINATE`;
- broker reconciliation is missing, stale, or not comparable;
- source readiness is incomplete after the hydration window;
- price hydration is `PARTIAL`, stale, missing, or structurally broken;
- shadow NAV continuity, backfill, or repaired-chain evidence is under review;
- a dashboard or packet displayed materially misleading stale information;
- a future FR depends on historical before/after comparison evidence.

Evidence holds are operator discipline, not an automated lock. They should be
resolved by explicit review, not by silent cleanup.

## Future Cleanup Automation Requirements

Any future cleanup tool must be additive and governed separately. It must:

- default to dry-run;
- print affected paths and retention class;
- refuse to delete critical runtime evidence without explicit operator approval;
- preserve evidence holds;
- never delete files to suppress degraded-state visibility;
- write a cleanup manifest before mutation;
- support rollback from a backup created before deletion;
- exclude broker credentials, source code, cron definitions, and workflow
  configuration from artifact cleanup scope.

No cleanup automation is implemented by FR-019.

## Operator Guidance

Use this policy to decide what evidence must remain available before running
manual cleanup or planning future automation.

For unresolved incidents, preserve first and clean later. For routine generated
reports, keep dated source artifacts and treat display copies as rebuildable.
For tests and smoke validation, prefer bounded `/tmp` directories until FR-020
formalizes validation isolation.
