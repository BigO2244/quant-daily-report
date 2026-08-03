# AEG-002: Operational Aegis Registry Architecture

Status: Proposed in stacked draft PR
Scope: Local, deterministic, non-executing control plane

## Alpha Lab MVP evidence boundary

The Alpha Lab operational view reads `CURRENT_STATE.md`, `STRATEGY_BACKLOG.md`,
`EXPERIMENT_LEDGER.md`, and `DECISION_LOG.md` from draft PR #160 at its immutable
head commit. The importer preserves the source-reported date independently from
the capture timestamp. Backlog lifecycle labels and constraints are copied
verbatim; only explicit `BLOCKED` states produce blocker edges, and only ledger
rows already in `REVIEW` with a `PARK` verdict produce owner-decision entries.
This mapping is research planning provenance and grants no promotion, trading,
allocation, execution, scheduling, deployment, or capital authority.

## Decision

Extend PR #167 through forward schema migration 2. Preserve the v1 mission,
task, artifact, decision, capability, and event tables and add an operational
registry beside them: typed hierarchy entities, relationships, import sources,
source snapshots, external references, reconciliation records, priority scores,
executive overrides, decision-queue entries, brief snapshots, and source-health
records.

Native mission/task IDs remain unchanged. Imported records use source-qualified
stable IDs and retain their original identifiers in external-reference and
source-record fields. GitHub records remain planning metadata; repository
registries remain authoritative only for the scope they explicitly claim.

## Taxonomy and graph

The generic hierarchy is domain → program → initiative → mission → task, with
artifacts and decisions connected by typed graph relationships. Caerus is a
program under the Aegis domain. Configured initiative nodes may exist with
`STATUS_UNRESOLVED`; that means the taxonomy supports the name but no active
state was inferred. Hierarchy children have one parent. Hierarchy and acyclic
relationship types reject cycles transactionally. Deletion is not exposed, so
records cannot be silently orphaned.

Relationships use deterministic identities over source, target, and type.
Imported and inferred relationships require provenance; inferred edges also
require an evidence-bearing rule and certainty classification. Duplicate edges
are idempotent.

## Imports and reconciliation

GitHub access is behind an adapter. Tests use fixtures; an optional read-only
`gh` adapter uses the operator's authenticated session. Repository import reads
the strategy registry, FR registry, and configured evidence paths. Both produce
content-addressed snapshots and idempotent source-qualified records. Closed work
is not imported or classified by the open-record adapter; a later historical
adapter must record its closure rule explicitly.

Reconciliation emits review records. Exact/probable duplicates, conflicts, and
orphans are not automatically merged or resolved. Recommendations are rules,
not autonomous decisions.

## Priority and executive interface

Priority is a weighted sum of explicit urgency, importance, risk, and readiness
components. Inputs, weights, formula, stable tie-breaker, and executive override
rationale are persisted. It never assigns expected financial return.

Executive briefs are generated only from a persisted database snapshot at an
explicit `as_of` value, emitted as JSON and Markdown, hashed, and stored. Mission
Control embeds the same persisted model in a standalone HTML file with no CDN or
write actions.

## SQLite and concurrency

- Expected use is one local writer with concurrent readers.
- Foreign keys and a five-second busy timeout are enabled on every connection.
- Multi-record imports use `BEGIN IMMEDIATE` and rollback on exceptions.
- Schema changes are numbered forward migrations; no destructive replacement.
- The database and generated artifacts are created with owner-only permissions
  where the platform supports POSIX modes.
- Identity collisions fail instead of silently overwriting artifacts.

## Security and deployment assumptions

- REST defaults to read-only and may bind only to localhost.
- REST has no authentication. This is acceptable only for local loopback use;
  remote use would require authenticated TLS termination, authorization,
  CSRF protection for any writes, request limits, audit logging, and a deployment
  review. No remote deployment is included.
- The generated dashboard is static and has no controls, broker actions, or
  external CDN dependencies.
- GitHub credentials are required only for optional live `gh` import; fixtures
  and repository-only import require none. Tokens and environment values are
  never persisted in snapshots or reports.
- SQLite paths and generated reports may contain private planning metadata and
  should not be served publicly.

## Explicit exclusions

No autonomous Codex dispatcher, external model API, Claude dependency, broker
submission, execution/allocation/sizing change, scheduler/cron change, VM
change, deployment, paper/pilot/live behavior change, or capital-path change.
