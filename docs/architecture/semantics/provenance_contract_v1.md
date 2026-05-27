---
last_reviewed: 2026-05-21
owner: architecture
category: architecture
criticality: critical
canonical: true
related_systems: [research, governance, attribution, mcp]
spec_id: SEM-003
spec_version: v1
supersedes: null
---

# Specification 3 — Provenance Contract v1

**Spec ID:** SEM-003
**Version:** v1
**Date:** 2026-05-21
**Status:** Canonical
**Normative Language:** RFC 2119

---

## 1. Scope

Provenance is the institutional substrate. This specification defines:

- The lineage guarantees the corpus MUST provide.
- The treatment of orphan objects.
- Deterministic vs. non-deterministic derivation semantics.
- Provenance invalidation rules.
- Replay guarantees the contract MUST support.
- The immutability and supersession discipline.

Provenance is a **system primitive**, not a reporting enhancement. Every
research object exists inside the provenance DAG; there is no out-of-band
research.

---

## 2. The Provenance DAG

The provenance graph is a single directed acyclic graph (DAG) over the
entire Caerus research corpus.

- **Nodes** are research objects identified by `lineage.node_id` (Spec 1 §5).
- **Edges** are typed derivation relationships from parent to child.
- The graph is **append-only**: nodes and edges are never deleted. Objects
  are superseded, not replaced.

### 2.1 Edge Types

| Edge Type | Semantics |
|---|---|
| `DERIVED_FROM` | Child was computed from parent. The principal derivation edge. |
| `GOVERNED_BY` | Object is governed by a GovernanceFR. |
| `VALIDATED_BY` | Object was validated by a ValidationRun. |
| `AUDITED_BY` | Object was the subject of an AuditFinding. |
| `SUPERSEDES` | Newer object replaces older for same `(object_type, identity)` tuple. |
| `PROMOTED_FROM` | Strategy was promoted from a ResearchHypothesis. |
| `REPAIRS` | New object reconstructs a broken segment (Spec 2 §5.1). |

Every edge MUST be typed. Untyped edges are non-conformant.

### 2.2 Acyclicity

The graph MUST remain acyclic. A producer that would introduce a cycle
MUST reject the production. Cycle-detection is the responsibility of the
ingestion layer and MUST run on every index pass.

### 2.3 No Edge Erasure

Edges are immutable once committed. A correction to lineage requires:

- A new edge of type `SUPERSEDES` pointing from the corrected object to
  the prior incorrect object.
- An `AuditFinding` documenting the correction with rationale.

The original incorrect edge remains in the DAG, marked `is_superseded = true`.

---

## 3. Lineage Guarantees

The contract MUST provide the following guarantees for any object in the
corpus.

### 3.1 Traceability

For every object `O`, the union of `O`'s ancestors via `DERIVED_FROM`
edges MUST terminate in raw data sources or governance documents. There
are no permitted "hanging" derivations.

**Raw data source** is one of:
- A broker-authoritative artifact (broker snapshot, broker fills).
- A market-data ingest record (prices, corporate actions).
- A user-authored research hypothesis or governance FR.

**Governance document** is any object of type `GovernanceFR`,
`AuditFinding`, or governance Markdown ingested as such.

### 3.2 Completeness

For every object `O` with payload that depends numerically on prior
artifacts, **every** such artifact MUST appear in `provenance.input_object_ids`.
Hidden dependencies are forbidden. If a computation reads a config file
to obtain parameters, that config file MUST appear in `provenance.source_paths`
or be referenced via an object id.

### 3.3 Faithfulness

`provenance.transformation` MUST describe the actual transformation
performed, not the intended one. If the implementation drifts from the
description, the description MUST be updated under Spec 8 governance,
not silently.

### 3.4 Reproducibility (Deterministic Path)

For any object `O` with `provenance.deterministic = true`, the corpus MUST
guarantee that re-execution with:

- The same input objects (by `lineage.node_id`).
- The same `provenance.produced_by` at the same source-controlled version.
- The same `schema.schema_version`.

produces an object identical to `O` in payload, byte-for-byte, modulo
non-semantic encoding differences (whitespace, key ordering).

### 3.5 Identifiability (Non-Deterministic Path)

For any object `O` with `provenance.deterministic = false`, the corpus
MUST guarantee that:

- `provenance.source_state_hash` uniquely identifies the non-deterministic
  state (RNG seed, model checkpoint hash, sampling state, etc.).
- A re-execution with the same `source_state_hash` produces an identical
  object.

A non-deterministic derivation without `source_state_hash` is
non-conformant and MUST be refused at production (Spec 1 M010).

---

## 4. Orphan Handling

An **orphan** is an object whose `lineage.parent_refs` is empty AND
whose `object_type` is not in the set of raw data source types.

- Orphans MUST NOT be silently admitted to the corpus.
- The ingestion layer MUST surface orphans as findings of type
  `ORPHAN_DERIVATION` with severity `HIGH`.
- An orphan MAY be retained in the registry only with an
  `AuditFinding` documenting why parent recovery is impossible.
- A retained orphan MUST carry `confidence.level = LOW` regardless of
  payload quality.

### 4.1 Apparent Orphans (Synthesised Envelopes)

Grandfathered artifacts (Spec 1 §11) that lack explicit lineage are
*apparent* orphans. The ingestion layer SHOULD attempt parent inference
via path-to-ontology mapping (architecture §12) and SHOULD record the
inferred edges with `transformation = "mcp.ingestion.inferred"` and
`deterministic = false`. Inferred edges carry confidence ceiling `LOW`.

---

## 5. Derivation Semantics

### 5.1 Deterministic Derivation

A derivation is deterministic when:

1. Its outputs are a pure function of its declared inputs.
2. It does not consume wall-clock time, system entropy, network state,
   or any unrecorded external resource.
3. Two executions with identical declared inputs produce identical
   outputs (Spec 3.4).

Deterministic derivations propagate governance inheritance (Spec 4 §3).
They are the privileged derivation class.

### 5.2 Non-Deterministic Derivation

A derivation is non-deterministic when any of the above fail. Examples:

- Model training with stochastic optimisation.
- Random sub-sampling for cross-validation.
- Bootstrapping or Monte Carlo simulation.
- LLM-based reasoning over research artifacts.

Non-deterministic derivations:

- MUST set `provenance.deterministic = false`.
- MUST record `provenance.source_state_hash`.
- MUST NOT inherit governance from parents — they break the inheritance
  chain (Spec 4 §3).
- MUST carry confidence ceiling `PARTIAL_CONFIDENCE` unless an explicit
  reassessment under Spec 7 upgrades them.

### 5.3 Mixed Derivation

A derivation that combines deterministic and non-deterministic steps is
non-deterministic as a whole. There is no partial determinism.

### 5.4 External-Resource Derivation

A derivation that consumes an external resource not under Caerus version
control (e.g., live market data feed, external API) MUST:

- Record the consumption in `provenance.source_paths` with a URI scheme
  identifying the external system.
- Capture an immutable snapshot of the consumed state (e.g., the API
  response body) and store it under a snapshot identifier referenced via
  `provenance.source_state_hash`.
- Be classified as non-deterministic if the external resource is
  time-varying (e.g., live quote).

---

## 6. Provenance Invalidation

An object's provenance MAY be invalidated by one of the following events:

| Trigger | Invalidation Effect |
|---|---|
| A parent object is found to be incorrect (AuditFinding severity `HIGH` or `CRITICAL`). | Child's confidence floored at `LOW`; `annotations.provenance_invalidated = true`. |
| A parent's surface label is corrected. | Child's surface MUST be re-derived; if surface change is non-compatible, child is superseded. |
| A schema migration of a parent (Spec 8) breaks payload compatibility. | Child MUST be re-derived under the new schema; old child is superseded. |
| A governance FR rollback removes the governance authority of an input. | Child's `governance.state` recomputed; coverage MAY become `UNGOVERNED`. |

Invalidation propagates **downstream only**. An invalidated object does
not invalidate its parents. The invalidation event MUST be recorded as an
`AuditFinding` referenced from every invalidated object's
`annotations.invalidation_finding_ref`.

### 6.1 Invalidation Is Not Deletion

An invalidated object MUST remain in the corpus. It MUST NOT be deleted
or hidden. Consumers MAY filter invalidated objects from their views, but
the underlying record remains queryable via lineage.

This is the **temporal honesty** requirement: history is what happened,
not what we wish had happened.

---

## 7. Replay Guarantees

A **replay** is the reconstruction of an object (or a set of objects) as
of a specified prior moment in time. Replay is the operational test of
provenance integrity.

The contract MUST guarantee:

### 7.1 Faithful Replay

For any object `O` with `produced_at = T_O`, a replay at any `T >= T_O`
MUST produce the same object identity (`object_id`) and the same payload
(modulo Spec 3.4 encoding differences), provided:

- All inputs at their `as_of <= T_O` snapshots are reachable.
- The producing module at the source-controlled version used at `T_O` is
  reachable (via tagged release, container image, or equivalent).
- For non-deterministic derivations, the recorded `source_state_hash` is
  reproducible.

### 7.2 Point-in-Time Replay

A replay "as of `T_anchor`" MUST consume only objects with
`as_of <= T_anchor`. Future objects (objects with `as_of > T_anchor`)
MUST NOT enter the replay even if they are in the same lineage chain.

The detailed semantics of point-in-time replay are specified in Spec 6.
This contract guarantees the *provenance preconditions* for such replay:

- All inputs to an object are identifiable by `as_of` ≤ the object's `as_of`.
- All inputs are individually replayable.
- The producing transformation is reproducible.

### 7.3 Replay Stability Under Schema Migration

A schema migration of a parent object's payload (Spec 8) MUST NOT break
historical replay. Migration semantics MUST be either:

- **Forward-only**: the new schema applies only to future productions;
  historical objects remain on the old schema.
- **Migrated**: historical objects are migrated, AND a reverse-migration
  is preserved sufficient to reconstruct the original payload byte-for-byte.

Migration that destroys reverse-reconstruction capability is forbidden
under Spec 8.

### 7.4 Replay Audit Trail

Every replay execution MUST itself produce an envelope-bearing object of
type `ReplayRun` with:

- `provenance.input_object_ids` listing all consumed objects.
- `data.replay_anchor` — the `T_anchor` of the replay.
- `data.replay_target` — the `object_id` being replayed.
- `data.replay_result` — `IDENTICAL | DIVERGENT | UNREPLAYABLE`.
- If `DIVERGENT`, `data.divergence_summary` describing what differed.

`ReplayRun` objects are first-class. They are how the contract proves
its own integrity.

---

## 8. Supersession Discipline

When a new object replaces an older one for the same
`(object_type, identity)` tuple:

1. The new object MUST carry a `SUPERSEDES` lineage edge to the old.
2. The old object's `is_superseded = true` flag MUST be set in the
   registry index. The old object is NOT deleted from storage.
3. Tools that retrieve "current" objects MUST default to the latest
   non-superseded object.
4. Tools that retrieve "as of T" objects MUST return the object that was
   non-superseded at T (Spec 6).
5. Supersession MUST be governed: a `SUPERSEDES` edge MUST cite a
   governing reference (`AuditFinding`, `GovernanceFR`, or scheduled
   re-run) in the edge's `transformation` field.

Ad-hoc supersession is forbidden. Every supersession is a governed event.

---

## 9. Provenance Hash

Every object MUST carry `lineage.transformation_chain_hash`, computed as:

```
hash = H(
  parent_chain_hashes_sorted,
  schema.schema_version,
  schema.ontology_version,
  provenance.produced_by,
  provenance.transformation,
  provenance.deterministic,
  provenance.source_state_hash | "",
)
```

where `H` is SHA-256 (or successor) and `parent_chain_hashes_sorted` is
the lexicographically sorted list of parent objects' chain hashes.

Properties:

- Two objects with identical chain hashes are **provenance-equivalent**:
  same derivation path, same upstream identity. They MAY still differ in
  payload if non-deterministic; provenance equivalence is necessary but
  not sufficient for payload equivalence.
- A change anywhere in the upstream chain MUST propagate to all
  downstream chain hashes.
- The chain hash is the canonical "did anything in the lineage change?"
  primitive. Replay integrity checks use it.

---

## 10. Storage of Provenance

The MCP registry (architecture §10) stores provenance in two tables:

- `objects` — object identity, envelope summary.
- `lineage` — typed edges.

Storage representation is implementation; the **semantic guarantee** is:

- The full envelope MUST be reconstructable from registry contents.
- The DAG MUST be queryable for upstream and downstream traversal in
  bounded time.
- Replay-relevant fields (`schema_version`, `produced_by`,
  `source_state_hash`, `transformation_chain_hash`) MUST be indexed.

---

## 11. Enforcement Surface

| Component | Enforcement |
|---|---|
| Producers (pipelines, scripts) | Emit conformant envelopes at production; refuse to produce orphans. |
| Ingestion layer | Build the DAG; reject acyclicity violations and missing-parent references; surface orphans as findings. |
| Retrieval layer | Validate provenance on hydration; refuse hydration of objects with broken parent references. |
| Replay subsystem | Produce `ReplayRun` records; compare chain hashes; emit divergence findings. |
| Audit subsystem | Generate provenance-completeness reports; raise findings on gaps. |

---

## 12. Examples

### 12.1 Conformant Lineage

```
broker_snapshot__2026-04-30  (raw)
  └── (DERIVED_FROM) ──→ nav_surface__caerus_polaris__2026-04-30__broker_paper
       └── (DERIVED_FROM) ──→ attribution_run__caerus_polaris__2026-04-30
            └── (DERIVED_FROM) ──→ daily_research_brief__caerus_polaris__2026-04-30
       └── (GOVERNED_BY) ──→ FR-024
```

Every link is typed. Every object has at least one parent. Root is a raw
broker artifact.

### 12.2 Non-Conformant Examples

- A `attribution_run` whose `provenance.input_object_ids` is empty and
  whose `lineage.parent_refs = []` → orphan, refuse (§4).
- A `validation_run` with `deterministic = true` but whose two executions
  produce different metrics → contract violation, raise
  `PROVENANCE_FAITHFULNESS` finding (§3.4).
- Silent deletion of a superseded attribution run → forbidden (§8 §6.1).
- A new `schema.schema_version` of a parent that breaks reverse migration
  → forbidden under Spec 8 §7.3; refuse migration.

---

## 13. Errata

*(none at v1)*

---

*SEM-003 v1 — 2026-05-21. See SEM-INDEX (`README.md`).*
