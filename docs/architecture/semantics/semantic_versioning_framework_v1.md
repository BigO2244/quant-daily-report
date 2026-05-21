---
last_reviewed: 2026-05-21
owner: architecture
category: architecture
criticality: critical
canonical: true
related_systems: [research, governance, mcp]
spec_id: SEM-005
spec_version: v1
supersedes: null
---

# Specification 5 — Semantic Versioning Framework v1

**Spec ID:** SEM-005
**Version:** v1
**Date:** 2026-05-21
**Status:** Canonical
**Normative Language:** RFC 2119

---

## 1. Scope

The Caerus research corpus is a long-lived system. Its meaning will drift
unless versioning is explicit and disciplined.

This specification defines:

- The versionable dimensions of the system.
- The semver discipline for each dimension.
- Compatibility guarantees between versions.
- Migration requirements when versions advance.
- The relationship between dimensions (which versions may co-exist).

The framework is binding on every dimension of the Caerus semantic
substrate. There is no unversioned semantic surface.

---

## 2. Versionable Dimensions

The Caerus semantic substrate has five independently versioned dimensions:

| Dimension | What It Governs | Versioned Identifier |
|---|---|---|
| Ontology Version | The set of valid `object_type`s and their structural definitions. | `schema.ontology_version` (Spec 1). |
| Schema Version | The payload schema for a specific `object_type`. | `schema.schema_version` (Spec 1). |
| Surface Methodology Revision | The NAV-computation methodology under a surface identifier. | `envelope.surface.methodology_revision` (Spec 2 §8). |
| Governance Semantic Version | The semantics applied by the governance layer (this layer's contracts). | `governance.semantic_version` (this spec). |
| Semantic Layer Version | The version of this Semantic Contract Layer as a whole. | `Layer Version` in `README.md`. |

Each dimension uses **semantic versioning** in the form `MAJOR.MINOR.PATCH`:

- **MAJOR** increments imply a breaking change in semantics or structure.
- **MINOR** increments add capability without breaking existing consumers.
- **PATCH** increments fix non-semantic issues (typos, errata, encoding).

---

## 3. Ontology Versioning

The ontology is the set of valid `object_type`s and the structural
definition of each (architecture §2).

### 3.1 What Counts as Ontology Change

| Change | Bump |
|---|---|
| Add a new `object_type`. | MINOR. |
| Add an OPTIONAL field to an existing type. | MINOR. |
| Add a REQUIRED field to an existing type. | MAJOR. |
| Remove an `object_type`. | MAJOR. |
| Rename a field. | MAJOR. |
| Change a field's type or enum values. | MAJOR. |
| Tighten a constraint (e.g., narrow a range). | MAJOR. |
| Loosen a constraint. | MINOR. |
| Add an enumeration value to an existing enum (if consumers must handle unknown values gracefully). | MINOR. |
| Add a new edge type to the relationship graph. | MINOR. |
| Change the semantics of an existing field without structural change. | MAJOR. |

### 3.2 Ontology Compatibility Guarantees

For ontology version `vX.Y.Z`:

- **Backward compatibility** within the same MAJOR: a consumer written
  for `vX.Y0.Z0` MUST be able to process objects produced by `vX.Y1.Z1`
  where `Y1 >= Y0`. Unknown new fields MUST be ignored, not rejected.
- **No forward compatibility**: a consumer written for `vX.Y0.Z0` is NOT
  guaranteed to process objects from an older version `vX.Y2.Z2` where
  `Y2 < Y0` if those objects rely on now-removed structures (which is
  permitted only across MAJOR boundaries).
- **MAJOR bumps require migration** per Spec 8.

### 3.3 Co-existence

Objects produced under different ontology versions MAY co-exist in the
corpus. Every object carries `schema.ontology_version`. The MCP retrieval
layer dispatches on ontology version. There is no global migration
event that re-stamps prior objects, except as governed by Spec 8.

---

## 4. Schema Versioning

Each `object_type` has a schema governing its payload. The schema is
versioned independently of the ontology.

### 4.1 What Counts as Schema Change

The bumping rules in §3.1 apply, scoped to a single `object_type`'s
payload.

### 4.2 Schema Identity

A schema is identified by `(schema.schema_id, schema.schema_version)`.
`schema_id` is the canonical name (e.g., `caerus.attribution_run`).

A schema MUST be **published** before it MAY be used in production. The
registry of published schemas is `docs/architecture/semantics/schemas/`
(future expansion); until that registry exists, the canonical reference
is the schema declaration emitted by the producing module's release tag.

### 4.3 Per-Type Versioning

A schema MAJOR bump for one `object_type` does NOT force a bump for
others. The ontology and per-type schemas advance on independent
schedules. Spec 8 (Schema Evolution Governance) defines the lifecycle.

---

## 5. Surface Methodology Versioning

Surfaces (Spec 2) are stable identifiers. The *methodology* under a
surface MAY evolve.

### 5.1 Methodology Revision Rules

- A methodology revision MUST be recorded in Spec 2's errata.
- A revision that changes historical NAV values is **not** a revision —
  it is a new surface (Spec 2 §8). The corpus MUST retire the old
  surface identifier and introduce a new one (e.g.,
  `OPERATIONAL_SHADOW_NAV_V2`).
- A revision that improves precision without changing historical values
  bumps `methodology_revision`. Historical objects retain their
  original methodology revision; new objects use the new revision.
- The MCP MUST be able to filter NAV series by methodology revision.

### 5.2 Methodology Revision Co-existence

NAV series produced under different methodology revisions on the same
surface MAY be concatenated only if explicitly authorised by an
`AuditFinding` documenting that the revision is non-disruptive for the
concatenated segment. Default is to forbid concatenation across revisions.

---

## 6. Governance Semantic Versioning

This dimension governs the semantics applied by the governance layer —
i.e., the rules in Spec 4.

### 6.1 What Counts as Governance Semantic Change

| Change | Bump |
|---|---|
| Add a new `GovernanceState` value. | MAJOR (consumers MUST know it). |
| Change inheritance break conditions. | MAJOR. |
| Change observation semantics. | MAJOR. |
| Add a new edge type to the governance DAG. | MINOR. |
| Refine a query API without changing semantics. | MINOR or PATCH. |

### 6.2 Governance Semantic Version on Objects

Every object MAY carry `governance.semantic_version` to declare which
version of governance semantics produced its `governance.state`. If
absent, the object inherits the layer version of the artifact at
production time.

When governance semantics change MAJOR, the MCP MUST re-evaluate
`governance.state` for all objects at the next index pass. Old objects
are not destroyed; their *stamped* governance state at original
production time remains accessible via point-in-time queries (Spec 6).

---

## 7. Semantic Layer Versioning

The Semantic Contract Layer as a whole carries `Layer Version` declared
in `README.md`.

### 7.1 Layer Version Bumping

- **MAJOR** — a backward-incompatible change to any specification's
  MUST clauses.
- **MINOR** — a new specification is added, or a specification adds new
  MUST clauses that do not break existing conformant artifacts.
- **PATCH** — errata, typos, clarifications.

A specification's own `spec_version` (e.g., `v1`, `v2`) increments
independently. A `v2` of an existing specification supersedes `v1`;
the supersession is recorded in the new spec's frontmatter
(`supersedes: v1`). Both versions remain in the repository for
historical reference.

### 7.2 Layer Version on Artifacts

Conformant producers SHOULD stamp the layer version under
`annotations.semantic_layer_version` to enable post-hoc auditing of
which contract version an artifact was produced under.

---

## 8. Compatibility Guarantees

This section is the operative summary across all dimensions.

| Guarantee | Rule |
|---|---|
| Reader robustness | A reader at semantic layer `LX.Y.Z` MUST gracefully process artifacts produced under `LX.Y'.Z'` for `Y' <= Y`. Unknown additive fields are ignored. |
| Writer discipline | A writer MUST stamp every version field truthfully. Producing an object with a future version it does not actually conform to is forbidden. |
| Migration on MAJOR | A MAJOR bump in any dimension MUST be accompanied by a published migration plan (Spec 8). Productions under the new MAJOR MUST cite the migration. |
| Cross-dimension constraints | Some combinations of versions are invalid. The MCP MUST publish a compatibility matrix and refuse to ingest objects with invalid combinations. |

### 8.1 Cross-Dimension Compatibility Matrix

Object production MUST satisfy:

- `schema.schema_version` MUST be a published version for the object's
  `object_type` under the declared `schema.ontology_version`.
- `governance.semantic_version` (when stamped) MUST be a published
  governance version compatible with the declared `schema.ontology_version`.
- `surface.methodology_revision` (when applicable) MUST be a revision
  published under the declared `surface.nav_surface_type`.

The compatibility matrix MUST be maintained in
`docs/architecture/semantics/compatibility_matrix.md` (future expansion).
Until that document exists, compatibility is defined by the publication
dates of each version: a version published on date `D` is compatible
with versions published before `D`, modulo MAJOR-bump rules above.

---

## 9. Migration Requirements

A MAJOR version bump in any dimension MUST be accompanied by:

1. A **migration FR** in the `fr_registry.md` documenting the change.
2. A **before/after comparison artifact** showing payload or behaviour
   differences for representative objects.
3. A **deprecation window** during which producers MAY emit either
   version. The deprecation window MUST be at least 14 calendar days for
   ontology changes and at least 30 calendar days for governance
   semantic changes.
4. A **reverse-migration tool** capable of reconstructing the previous
   version's representation from the new version, sufficient for
   historical replay (Spec 3 §7.3).
5. A **replay audit** (Spec 6) confirming that historical
   reconstructions before the cutover remain identical.

A migration without one of the above is non-conformant under this spec.

---

## 10. Examples

### 10.1 Adding a New Object Type (MINOR Ontology Bump)

A new `object_type = ScenarioStressRun` is added.

- `ontology_version`: `1.0.0` → `1.1.0`.
- New schema `caerus.scenario_stress_run` published at `v1.0.0`.
- Existing object types unchanged.
- Consumers at `1.0.0` continue to function; they ignore unknown
  `ScenarioStressRun` objects (or filter them out).
- No migration FR required (additive only).

### 10.2 Tightening a Constraint (MAJOR Schema Bump)

`AttributionRun.payload.return_pct` is constrained from `float` to
`float in [-1.0, 10.0]`.

- `caerus.attribution_run` schema: `1.2.0` → `2.0.0`.
- Migration FR opened. Historical artifacts MAY violate the new constraint.
- Reverse-migration tool: identity (no payload change).
- Replay audit: every historical replay of an `AttributionRun` MUST
  succeed under the old schema `1.2.0`.
- Producers MAY emit `1.2.0` or `2.0.0` for 30 days; after that,
  `1.2.0` production is closed.

### 10.3 Surface Methodology Change (Non-Disruptive)

The synthetic-cost computation in `RESEARCH_BACKTEST_NAV` is refined to
fix a rounding bug. Historical NAV values unchanged.

- `surface.methodology_revision`: `1` → `2` going forward.
- Spec 2 errata updated.
- No new surface identifier.
- Pre-revision objects keep `methodology_revision = 1`; post-revision
  objects carry `methodology_revision = 2`.

### 10.4 Surface Methodology Change (Disruptive)

The synthetic-cost computation is overhauled in a way that changes
historical NAV values.

- New surface identifier required: `RESEARCH_BACKTEST_NAV_V2`.
- Old identifier `RESEARCH_BACKTEST_NAV` retained as canonical for
  historical objects.
- The compatibility matrix between the two is `INCOMPATIBLE` by default,
  pending explicit reconciliation rules.

---

## 11. Versioning Anti-Patterns

The following are forbidden:

1. **Silent payload change without bump.** A producer that emits a
   different payload structure under the same `schema_version` is
   non-conformant. The MCP MUST detect this via payload schema validation
   and raise a `SCHEMA_SILENT_DRIFT` finding.

2. **Reuse of a published version number.** Once a version is published,
   its semantics are frozen. A different semantic content under the same
   version string is forbidden. PATCH bumps are cheap; reuse is not
   acceptable.

3. **Cross-dimension version-bumping by alias.** Bumping the ontology
   version to mask a schema change is forbidden. Each dimension is
   bumped on its own merits.

4. **Backward-incompatible MINOR or PATCH.** Versioning discipline is
   the floor of consumer trust. A MINOR bump that breaks consumers is a
   process failure and MUST be retracted under errata + new MAJOR.

---

## 12. Enforcement Surface

| Component | Enforcement |
|---|---|
| Producers | Stamp every version field; refuse to publish under an unpublished version. |
| Ingestion layer | Validate version fields against the publication registry; refuse unknown versions or invalid combinations. |
| Retrieval layer | Dispatch on `schema.ontology_version` for type resolution; dispatch on `schema.schema_version` for payload parsing. |
| Migration tooling | Maintain reverse-migration capability for every MAJOR bump. |
| Audit subsystem | Emit `SCHEMA_SILENT_DRIFT` and `VERSION_REUSE` findings when violations are detected. |

---

## 13. Errata

*(none at v1)*

---

*SEM-005 v1 — 2026-05-21. See SEM-INDEX (`README.md`).*
