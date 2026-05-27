---
last_reviewed: 2026-05-21
owner: architecture
category: architecture
criticality: critical
canonical: true
related_systems: [research, governance, mcp]
spec_id: SEM-008
spec_version: v1
supersedes: null
---

# Specification 8 — Schema Evolution Governance v1

**Spec ID:** SEM-008
**Version:** v1
**Date:** 2026-05-21
**Status:** Canonical
**Normative Language:** RFC 2119

---

## 1. Scope

Schemas change. The research corpus has a five-to-ten-year horizon, and
no schema survives that horizon unchanged. The question is whether
schema evolution preserves or destroys institutional memory.

This specification defines the **lifecycle controls** that ensure schema
evolution preserves:

- Backward compatibility for consumers.
- Lineage continuity for the provenance DAG.
- Replay stability for historical reconstruction (Spec 6).
- Confidence integrity through migrations.

It is binding on every schema (ontology types, payload schemas, surface
methodologies, governance semantics — every dimension defined in Spec 5).

---

## 2. Schema Registry

The corpus maintains a **schema registry** as part of the Semantic
Contract Layer.

### 2.1 Registry Contents

For every schema, the registry MUST record:

- `schema_id` — canonical identifier (e.g., `caerus.attribution_run`).
- `schema_version` — semantic version per Spec 5.
- `published_at` — UTC instant when the version became valid for production.
- `status` — one of `DRAFT`, `PUBLISHED`, `DEPRECATED`, `RETIRED`.
- `effective_window` — date range during which production under this
  schema is permitted.
- `migration_refs` — list of migration FR object ids that operate on
  this schema.
- `payload_definition` — the structural definition (JSON Schema, dataclass,
  or equivalent). The canonical form is implementation-defined; the
  registry records the canonical reference.

### 2.2 Registry Storage

Until a dedicated `docs/architecture/semantics/schemas/` directory is
established, the schema registry is the union of:

- Schema declarations in producer module release tags.
- Ontology declarations in `caerus_research_mcp_architecture.md` §2.
- Governance semantic versions declared in Spec 4.

The future centralised registry SHOULD be a directory with one file per
schema version, machine-readable. The MCP ingestion layer MUST be able
to enumerate published schemas.

### 2.3 Publication Discipline

A schema version is **published** only when:

1. The structural definition is committed to source control.
2. A `published_at` timestamp is recorded.
3. The version appears in the registry with status `PUBLISHED`.

Producers MUST NOT emit objects under unpublished schemas. The
ingestion layer MUST reject objects whose `schema_version` is unknown
or in `DRAFT` status.

---

## 3. Schema Lifecycle States

A schema version progresses through the following states:

```
DRAFT  →  PUBLISHED  →  DEPRECATED  →  RETIRED
                  │
                  └──→  ERRATA_PATCH  (PATCH bumps only; remains PUBLISHED)
```

| State | Production Permitted | Consumption Permitted |
|---|---|---|
| `DRAFT` | No (development only) | No |
| `PUBLISHED` | Yes | Yes |
| `DEPRECATED` | Discouraged; permitted within deprecation window. | Yes; consumers warned. |
| `RETIRED` | No | Yes (historical objects only; new objects forbidden). |

### 3.1 Transitions

| Transition | Requires |
|---|---|
| `DRAFT → PUBLISHED` | Source commit, `published_at` stamp, registry entry. |
| `PUBLISHED → DEPRECATED` | Successor version PUBLISHED; migration FR in `READY_VALIDATED` or later; deprecation date documented. |
| `DEPRECATED → RETIRED` | Migration complete; deprecation window expired; no active producers. |
| `PUBLISHED → ERRATA_PATCH` | PATCH bump only; non-semantic correction. |

A `DEPRECATED → PUBLISHED` reversion is permitted only by AuditFinding +
GovernanceFR documenting why the deprecation was incorrect. Reversion
without that record is forbidden.

---

## 4. Migration Controls

A migration is a transition from schema `vA` to `vB`. Migrations are
governance events under Spec 4 and version events under Spec 5.

### 4.1 Migration FR Requirement

Every migration MUST be governed by an FR (`FR-XXX`) in the registry.
The FR MUST document:

- Source schema (`schema_id`, `vA`).
- Target schema (`schema_id`, `vB`).
- Bump type (PATCH, MINOR, MAJOR).
- Migration kind (forward-only, migrated, or non-disruptive — see §5).
- Deprecation window for `vA`.
- Reverse-migration capability (required for MAJOR bumps).
- Replay audit plan (required for MAJOR bumps).
- Rollback path.

### 4.2 Migration Object

The migration itself MUST be recorded as a `SchemaMigration` object
(envelope-bearing) with:

- `data.source_schema` — `(schema_id, vA)`.
- `data.target_schema` — `(schema_id, vB)`.
- `data.migration_kind` — one of the values in §5.
- `data.affected_object_count` — number of historical objects affected
  (zero for forward-only migrations).
- `data.reverse_migration_ref` — reference to the reverse-migration tool
  or specification.
- `data.replay_audit_ref` — reference to the `ReplayRun` set that
  validates replay stability post-migration.

### 4.3 Migration Ordering

When multiple schemas are co-migrated (e.g., a coordinated ontology
bump touching several object types), each schema's migration MUST be
recorded individually. Cross-schema dependencies MUST be declared in
each migration's FR via `Dependencies`.

The MCP MUST be able to reconstruct, for any `T`, the schema set that
was published at `T`.

---

## 5. Migration Kinds

Migrations fall into one of three kinds. The kind determines the
applicable rules.

### 5.1 Forward-Only Migration

A forward-only migration introduces a new schema version that applies
**only to objects produced after the cutover**. Historical objects
remain on the prior schema version.

Requirements:

- Cutover date specified.
- Old schema transitions `PUBLISHED → DEPRECATED` at cutover;
  `DEPRECATED → RETIRED` after the deprecation window.
- The MCP retrieval layer dispatches on `schema.schema_version` so
  consumers can read both old and new objects.
- No reverse-migration tool required (historical objects unaffected).

Forward-only is the **default** migration kind. It is the lowest-risk
option and SHOULD be preferred when feasible.

### 5.2 Migrated

A migrated migration **re-stamps historical objects** with the new
schema. Historical payloads are transformed from `vA` representation
to `vB` representation.

Requirements:

- A migration function `f: vA → vB` is implemented and version-tagged.
- A reverse migration function `f_inv: vB → vA` is implemented such
  that `f_inv(f(o)) == o` byte-for-byte (modulo Spec 3 §3.4 encoding).
  Lossy migrations are **forbidden** — every migration must be
  reversible to its source representation, otherwise historical replay
  (Spec 6) is broken.
- A replay audit verifies that historical reconstructions before and
  after the migration produce identical results when re-emitted under
  their respective schemas.
- The migration FR is in `DEPLOYED_OBSERVING` for at least the migration
  window; transition to `DEPLOYED` requires the replay audit to pass
  cleanly.

### 5.3 Non-Disruptive

A non-disruptive migration is a PATCH bump that fixes encoding issues,
typos, or non-semantic representation choices. It does NOT change
payload semantics.

Requirements:

- No deprecation of the prior version (the prior version is errata-corrected
  in place).
- The bump is recorded in the schema registry but does not appear as a
  schema lifecycle transition.
- Reverse-mapping is identity.

PATCH bumps MUST NOT mask MINOR or MAJOR changes. If discovered, they
MUST be retracted and re-issued under correct bump type.

---

## 6. Backward Compatibility

### 6.1 Reader Discipline (Required)

A conformant reader for schema `vA.B.C` MUST:

- Process objects produced under `vA.B'.C'` for any `B' >= B`, `C' >= 0`
  (forward-compatible within the same MAJOR).
- Ignore unknown additive fields rather than reject.
- Reject objects of an unknown MAJOR or unknown `schema_id` and surface
  them as findings.

### 6.2 Producer Discipline (Required)

A conformant producer for schema `vA.B.C`:

- MUST NOT add new required fields without bumping MAJOR.
- MUST NOT change field types without bumping MAJOR.
- MUST NOT silently emit a new representation under the same version.
- MAY add optional fields under MINOR bumps.

### 6.3 Cross-Version Reads

For an object produced under `vA.B0.C0` read by a consumer at `vA.B1.C1`
with `B1 > B0`:

- The consumer MUST process the object as if its absent newer fields
  are at their defined defaults. Defaults are part of the schema and MUST
  be documented in the registry.

For an object produced under `vA.B0.C0` read by a consumer at `vA'.B1.C1`
with `A' > A`:

- The consumer MUST either:
  - Apply the migration `vA → vA'` if a migrated migration exists, OR
  - Reject the read and surface a `SCHEMA_INCOMPATIBLE_MAJOR` finding.

Silent partial reads across MAJOR boundaries are forbidden.

---

## 7. Lineage Continuity

Schema evolution MUST preserve lineage continuity.

### 7.1 Identity Preservation

A migrated object retains its `object_id` and `lineage.node_id`. The
migration is recorded via:

- A `SUPERSEDES` edge from the migrated form to the pre-migration form
  is **NOT** used. Migration is not supersession.
- Instead, the migration is recorded as an `annotations.schema_migration`
  block on the migrated object pointing to the `SchemaMigration` object.
- The prior representation remains accessible via the reverse migration.

### 7.2 Chain Hash Stability

The `lineage.transformation_chain_hash` (Spec 3 §9) is computed from
canonical schema-independent inputs (parent chain hashes, ontology
version, producer, transformation, determinism). It is invariant under
non-disruptive migrations. Migrated migrations recompute the chain hash;
the change is recorded in the migration's `data.chain_hash_delta`.

### 7.3 Provenance Preservation

Migration MUST NOT alter `provenance.input_object_ids`. The set of
inputs is fixed at production; migrations re-encode the payload, not
the lineage.

---

## 8. Replay Stability Through Migration

This is the strictest requirement of this spec. Schema migration MUST
NOT break historical replay (Spec 6 §5).

### 8.1 The Replay Stability Test

Before a migrated migration may transition from `DEPLOYED_OBSERVING` to
`DEPLOYED`, the following test MUST pass:

For a representative set of pre-migration objects `{O_i}`:

1. Each `O_i` is replayed under the pre-migration schema → produces `R_i^A`.
2. Each `O_i` is migrated to the new schema → `O_i^B`.
3. Each `O_i^B` is reverse-migrated back to the old schema → `R_i^A'`.
4. Each `R_i^A` MUST equal `R_i^A'` byte-for-byte (modulo Spec 3 §3.4).

Failures MUST be reported as `MIGRATION_REPLAY_DIVERGENCE` findings.

### 8.2 Producer Version Tagging

The producer module MUST be tagged at each schema version. A historical
replay invokes the producer at the tag corresponding to the artifact's
`schema_version`. Migrating an artifact MUST NOT require running the
producer at a different tag.

### 8.3 Schema-Dependent Inputs

Some artifacts depend not only on their payload but on the schema of
their inputs. The MCP MUST be able to determine, for any artifact, the
schema version of every input that was in force at the artifact's
`produced_at`. This is tracked via `provenance.input_object_ids` plus
the inputs' own `schema.schema_version` fields.

### 8.4 Cross-Migration Replays

A replay that spans a migration cutover MUST use schema-version-aware
dispatch: inputs before the cutover are read under their original
schema; inputs after are read under the new schema. The replay MUST
NOT silently apply one schema to objects of the other.

---

## 9. Deprecation Windows

| Bump Type | Minimum Deprecation Window |
|---|---|
| PATCH | None (in-place errata). |
| MINOR | None (backward compatible by definition; old version permitted indefinitely). |
| MAJOR (forward-only) | 14 calendar days. |
| MAJOR (migrated) | 30 calendar days (covers `DEPLOYED_OBSERVING` of the migration FR). |
| Ontology MAJOR | 60 calendar days. |
| Governance Semantic MAJOR | 60 calendar days. |

During the deprecation window:

- Producers SHOULD migrate to the new version but MAY continue producing
  under the old.
- Consumers MUST handle both versions.
- The schema registry MUST list both as eligible.

After the deprecation window:

- The old version transitions to `RETIRED`.
- New production under the old version is rejected by ingestion.
- Historical objects remain readable.

---

## 10. Schema Deprecation

A schema MAY be deprecated even without a successor (e.g., the underlying
research practice was discontinued).

In that case:

- The schema transitions `PUBLISHED → DEPRECATED → RETIRED`.
- No migration is required (nothing to migrate to).
- Historical objects remain in the corpus, readable under the retired
  schema.
- The MCP retrieval layer continues to dispatch on the retired schema
  version. Retirement is about production, not consumption.

A retired schema MUST NOT be brought back to `PUBLISHED` without errata
+ AuditFinding documenting the reversal.

---

## 11. Cross-Schema Coordination

When schemas evolve in coordinated bundles (e.g., a Track-B FR that
touches NAVSurface, AttributionRun, and PortfolioSnapshot simultaneously),
the migrations MUST share a single coordinating FR.

The coordinating FR MUST:

- Declare each touched schema and its target version.
- Specify the migration ordering.
- Provide a single rollback path that reverses all touched schemas
  atomically.
- Treat partial application as `BROKEN`: if migration of some but not
  all schemas in the bundle occurs, the bundle is non-conformant and
  MUST be rolled back to the pre-bundle state.

---

## 12. Anti-Patterns

The following are forbidden:

1. **Lossy migration.** A migrated migration without a working reverse
   function destroys replay capability. Refuse.

2. **In-place schema reuse.** Publishing `vA.B.C` with content different
   from a prior publication of `vA.B.C` is forbidden under Spec 5 §11.2.
   Always bump.

3. **Hidden schema dispatch.** A consumer that reads `vA.B0` objects but
   silently applies `vA.B1` parsing semantics is non-conformant. Schema
   dispatch is explicit, not inferred.

4. **Skip-publication production.** Emitting objects under a schema
   version not yet in the registry as `PUBLISHED` is forbidden.

5. **Migration without replay audit.** A migrated migration that has not
   completed §8.1 cannot transition to `DEPLOYED`. Skipping the audit is
   non-conformant.

6. **Cross-MAJOR silent migration.** A reader that auto-upgrades objects
   across MAJOR boundaries without an explicit migration is forbidden.
   MAJOR boundaries require declared migrations.

---

## 13. Enforcement Surface

| Component | Enforcement |
|---|---|
| Schema registry | Maintain authoritative state and lifecycle of every schema version. |
| Ingestion layer | Validate `schema.schema_version` against the registry; reject unknown or `DRAFT` versions; reject `RETIRED` versions for new productions. |
| Retrieval layer | Dispatch on `schema.schema_version`; refuse cross-MAJOR auto-coercion. |
| Migration tooling | Implement and version-tag migration `f` and reverse `f_inv`; pass §8.1 replay audit before `DEPLOYED`. |
| Audit subsystem | Emit `SCHEMA_SILENT_DRIFT`, `VERSION_REUSE`, `MIGRATION_REPLAY_DIVERGENCE`, `MIGRATION_PARTIAL_BUNDLE` findings. |
| Governance subsystem | Maintain migration FRs; gate migrations on conformance to this spec. |

---

## 14. Examples

### 14.1 Forward-Only Migration

`caerus.attribution_run` adds an OPTIONAL `factor_exposure_summary` field.

- Bump: `1.2.0 → 1.3.0` (MINOR).
- Migration kind: forward-only by default; no historical re-stamping.
- Old version remains `PUBLISHED` indefinitely (MINOR is backward
  compatible).
- Consumers at `1.2.0` continue to function (ignore new field).
- Consumers at `1.3.0` see the field on new objects; absent on old objects
  treated as default `null`.

### 14.2 Migrated MAJOR

`caerus.nav_surface` changes the `chain_status` enum to add a new value
`SOFT_BREAK` (between `OK` and `BROKEN_CHAIN`). This is breaking — old
readers do not know `SOFT_BREAK`.

- Bump: `1.1.0 → 2.0.0` (MAJOR).
- Migration FR opened: declares old `chain_status` mapping into new
  enum (most values identity; identify which old `OK` objects are
  re-classified as `SOFT_BREAK` based on retrospective audit).
- Reverse migration: `SOFT_BREAK → BROKEN_CHAIN` (lossy upward, but
  reversible downward by the rule "any `SOFT_BREAK` was historically
  `OK` or `BROKEN_CHAIN`, with original recorded in
  `annotations.pre_migration_chain_status`").
- Replay audit runs over 90 days of historical NAV surfaces; passes if
  all reverse-migrations recover original chain status byte-for-byte.
- Deprecation window: 30 days `DEPLOYED_OBSERVING`.
- After window + passing audit: FR transitions to `DEPLOYED`, old version
  `RETIRED`.

### 14.3 Refused Production

A producer emits an `AttributionRun` with `schema_version = 2.5.0`, which
is not in the schema registry.

The ingestion layer MUST refuse with `SCHEMA_UNKNOWN` finding. The
producer MUST NOT retry under the same version; either the registry
needs an entry (publish 2.5.0) or the producer is misconfigured.

### 14.4 Forbidden Lossy Migration

A proposed migration `vA → vB` drops the `intra_day_pnl_detail` array
because it's "no longer used." This is lossy — a reverse migration
cannot reconstruct the dropped data.

The migration is non-conformant. It MUST either retain the field
(perhaps marked deprecated) or be rejected.

---

## 15. Errata

*(none at v1)*

---

*SEM-008 v1 — 2026-05-21. See SEM-INDEX (`README.md`).*
