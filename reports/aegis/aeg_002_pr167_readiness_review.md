# AEG-002 PR #167 Operational Readiness Review

As of: 2026-08-02T00:00:00Z
Reviewed head: `ae29d8a1356defa11c2592115fecd5747759ed75`
Reviewed PR: `BigO2244/quant-daily-report#167` (draft, open, mergeable)

## Current capability inventory

- Stable SHA-256-derived mission and task identifiers over canonical JSON.
- Additive SQLite migration ledger plus mission, task, task-edge, artifact,
  decision, capability, and append-only lifecycle-event tables.
- Explicit mission and task transition maps.
- Deterministic fixed task decomposition and task DAG construction.
- Packet-only safe default runner and an approval-checked constructor for the
  authoritative local `aiops run-all --spec <path> --mode <MODE>` boundary.
- SHA-256 artifact manifests and metadata-only file registration.
- Local CLI for create, inspect, approve, brief, and localhost REST serving.
- Minimal JSON REST portfolio/mission reads, mission creation, and a static,
  local, read-only Mission Control HTML view.
- ADR, governed AIOPS implementation spec, focused tests, and a forbidden-import
  boundary test.

## Missing capabilities

- Source snapshots, GitHub/repository import adapters, external references,
  import provenance, dry-run manifests, and unresolved-state handling.
- Domain/program/initiative hierarchy with validation and traversal.
- General typed relationship graph, lineage queries, certainty, and edge
  provenance.
- Reconciliation queue, duplicate/conflict detection, legacy migration report,
  and explicit approval of recommended actions.
- Explained deterministic priorities, component scores, and executive overrides.
- A complete executive decision queue and final decision events.
- Snapshot-reproducible Markdown/JSON briefs, immutable brief history, stale
  source reporting, and change detection.
- Operational Mission Control views and mission-first CLI linking/import flows.
- A real, evidence-backed Caerus consolidation mission.

## Code-quality risks

- SQLite foreign keys are declared in places but not enabled per connection;
  several tables have no foreign keys or uniqueness constraints.
- `executescript` migration behavior, default SQLite locking, and file
  permissions are undocumented; no busy timeout or explicit rollback contract
  exists.
- `INSERT OR REPLACE` can silently overwrite artifact rows.
- Approval enforcement in `AIOPSRunnerAdapter` accepts a caller-provided boolean
  instead of verifying persisted mission/task approval state.
- Mission creation timestamps are wall-clock values, so brief/change artifacts
  need a separate explicit as-of/snapshot contract for reproducibility.
- The task template records an adapter key that is not persisted.
- The REST surface includes an unauthenticated write endpoint. Localhost binding
  reduces exposure but does not supply authentication or CSRF protection.
- API and dashboard rendering are compact prototypes with incomplete validation,
  error handling, accessibility, and empty/stale-source states.

## Migration risks

- PR #167 migration version 1 must remain intact; AEG-002 must add a forward
  migration and tolerate a database already initialized at version 1.
- New foreign-key enforcement can expose legacy-invalid rows, so migration tests
  must cover both fresh and v1-upgrade databases without destructive repair.
- Existing stable native IDs must not change. Imported external IDs require a
  separate namespace and source uniqueness rules.
- Relationship and hierarchy cycle checks must be transactional so failed
  imports leave no partial registry state.

## Recommended branch strategy

Create `agent/aeg-002-operationalize-aegis` from PR #167 head and open a stacked
draft PR targeting `agent/aegis-control-plane-166`. This keeps PR #167 small and
reviewable, makes the dependency explicit, avoids duplicating its foundation,
and prevents an unmerged base from obscuring AEG-002's diff. Retarget the stacked
PR to `main` only after PR #167 merges; do not merge either PR in this mission.

## Validation strategy

1. Migration-from-v1 and fresh-schema tests with foreign keys enabled.
2. Focused lifecycle, stable-ID, import-idempotency, graph/hierarchy cycle,
   reconciliation, priority, decision, brief, REST, CLI, and approval tests.
3. Existing Aegis and AIOPS contract suites.
4. Static forbidden-import and changed-path boundary scans.
5. Relevant read-only Caerus contracts and the known paper-parity test on both
   PR #167 and AEG-002 heads, without updating its golden artifact.
6. Compilation, configured formatting/lint checks, `git diff --check`, and a
   full-suite attempt with exact failures recorded.

## Production-boundary statement

AEG-002 is operational registry and reporting work only. It will not change or
invoke broker submission, trading execution, allocation, sizing, scheduler,
cron, VM, deployment, paper, pilot, live, or capital paths. GitHub and repository
metadata are planning provenance only and are never treated as broker- or
trading-authoritative evidence. No autonomous Codex dispatcher or external model
API dependency is in scope.
