---
last_reviewed: 2026-05-21
owner: architecture
category: architecture
criticality: critical
canonical: true
related_systems: [research, governance, attribution, regime, shadow, alpha_stack, mcp]
spec_id: SEM-FREEZE-v1
spec_version: v1
supersedes: null
freezes: [SEM-001, SEM-002, SEM-003, SEM-004, SEM-005, SEM-006, SEM-007, SEM-008]
---

# Semantic Freeze v1 — Caerus Semantic Contract Layer Baseline

**Spec ID:** SEM-FREEZE-v1
**Version:** v1
**Date:** 2026-05-21
**Status:** Canonical — **FROZEN**
**Normative Language:** RFC 2119
**Layer Version Frozen:** 1.0

---

## 1. Purpose

This document declares the **canonical freeze** of the Caerus Semantic
Contract Layer v1.0 as of 2026-05-21. It exists to make the contents of
SEM-001 through SEM-008 immutable under defined-amendment rules, so that
the Caerus Research MCP implementation phase (and every artifact produced
under it) is built against a stable, replay-safe substrate.

The freeze is **not** an architectural document. It introduces no new
ontology, no new governance state, no new confidence value, no new
surface. It formalises *which* parts of the existing semantic contract
are frozen, *what* may evolve, and *under which procedure*.

If a question is not answered here, it is governed by the underlying
specification SEM-00N. If the underlying spec is silent, the conservative
default applies: **do not change it under v1.**

---

## 2. Canonical Frozen Baseline

The Caerus Semantic Contract Layer v1.0 is the union of the following
canonical specifications, taken as published on 2026-05-21:

| Spec ID | Title | Version | File |
|---|---|---|---|
| SEM-001 | Research Object Metadata Standard | v1 | `metadata_standard_v1.md` |
| SEM-002 | Truth Surface Standard | v1 | `truth_surface_standard_v1.md` |
| SEM-003 | Provenance Contract | v1 | `provenance_contract_v1.md` |
| SEM-004 | Governance Semantics | v1 | `governance_semantics_v1.md` |
| SEM-005 | Semantic Versioning Framework | v1 | `semantic_versioning_framework_v1.md` |
| SEM-006 | Point-in-Time Reconstruction Semantics | v1 | `point_in_time_reconstruction_v1.md` |
| SEM-007 | Confidence Semantics Standard | v1 | `confidence_semantics_standard_v1.md` |
| SEM-008 | Schema Evolution Governance | v1 | `schema_evolution_governance_v1.md` |

These eight specs, together with this freeze document, the Implementation
Conformance Guide (SEM-CONFORMANCE-v1), the Registry Invariants
(SEM-REGISTRY-v1), the MCP Implementation Boundaries
(SEM-MCP-BOUNDS-v1), and the Replay and Reconstruction Guarantees
(SEM-REPLAY-v1), constitute the **Semantic Contract Layer v1** in its
implementation-ready form.

No artifact produced after the freeze date MAY claim conformance to a
prior, draft, or unsigned version of any of the above. The frozen
baseline is the only conformant target for v1.

---

## 3. Freeze Scope

The freeze covers, for every spec listed in §2:

1. The **closed enumerations** (e.g., `ConfidenceLevel` values,
   `GovernanceState` values, `nav_surface_type` values,
   `chain_status` values, edge types, downgrade codes, validation codes).
2. The **MUST and MUST NOT clauses** as published.
3. The **field names, types, and required/optional designations** in the
   canonical metadata envelope (SEM-001 §2).
4. The **compatibility matrices** (SEM-002 §3; SEM-005 §8.1).
5. The **propagation algebra** for confidence (SEM-007 §3, §4) and
   governance (SEM-004 §3).
6. The **temporal fencing rules** (SEM-006 §3).
7. The **lineage edge type set** (SEM-003 §2.1; SEM-004 §8.2).
8. The **schema lifecycle states and transitions** (SEM-008 §3).
9. The **definition of canonical truth vs. reconstructed truth**
   (SEM-006 §4.3).
10. The **institutional question** the layer exists to answer
    (SEM README §"The Institutional Question").

A change to any item in this list is a **major-version event** governed
by §5 of this document.

---

## 4. Amendment Process

The frozen baseline MAY be amended only by the following procedure. No
shortcut path exists.

### 4.1 Amendment Types

| Type | Surface |
|---|---|
| **Errata** | Typos, formatting, non-semantic clarifications. Recorded inline in the affected spec's §"Errata". Does not bump layer version. |
| **PATCH** | A non-semantic correction that preserves all MUST clauses and all enumerations. Layer version `1.0.0 → 1.0.1`. |
| **MINOR** | A new specification is added, OR additive new clauses that do not break any existing conformant artifact. Layer version `1.0.x → 1.1.0`. |
| **MAJOR** | Any change that alters a MUST clause, enumeration, compatibility matrix, propagation rule, fencing rule, or any item in §3. Layer version `1.x.y → 2.0.0`. Requires the full migration discipline in §6 of this document. |

### 4.2 Amendment Authorities

- **Errata** — recorded by the spec owner with notice to architecture.
- **PATCH** — proposed by any contributor, approved by architecture.
- **MINOR** — requires an FR in `fr_registry.md` and architecture review.
- **MAJOR** — requires (a) an FR, (b) a published migration plan under
  SEM-008, (c) a replay audit under SEM-006 §5, (d) an explicit
  successor spec version (`vN+1`) with `supersedes: vN` frontmatter, and
  (e) acknowledgement that this freeze document itself is superseded by
  the corresponding `SEMANTIC_FREEZE_v2.md`.

### 4.3 Out-of-Band Amendments Forbidden

Any change to a spec's MUST/MUST NOT clauses, to a closed enumeration, or
to a compatibility matrix that is not recorded as a tracked amendment
under §4.1 is **non-conformant**. The MCP audit subsystem MUST surface
such drift as a `SEMANTIC_FREEZE_VIOLATION` finding.

### 4.4 No Silent Re-Stamping

A spec's `spec_version` MUST NOT be reused. A `v2` of any spec replaces
its `v1` via the supersession mechanism; both versions remain in the
repository for historical reference. Editing `v1` after publication
with semantic content is forbidden — only errata are permitted.

---

## 5. Major / Minor / Patch Rules at the Freeze Level

The following table is normative. It governs every proposed amendment to
the frozen baseline.

| Proposed Change | Bump |
|---|---|
| Add a value to any closed enumeration (`ConfidenceLevel`, `GovernanceState`, `nav_surface_type`, `chain_status`, edge type, downgrade code, validation code). | MAJOR. |
| Remove or rename a value from any closed enumeration. | MAJOR. |
| Change a MUST to a SHOULD, or vice versa. | MAJOR. |
| Change a MUST NOT to a MAY, or vice versa. | MAJOR. |
| Change the type, required-ness, or default of an envelope field (SEM-001 §3). | MAJOR. |
| Change a compatibility-matrix cell (SEM-002 §3). | MAJOR. |
| Change a downgrade trigger's effect (SEM-007 §5). | MAJOR. |
| Change a fencing or temporal-honesty rule (SEM-006). | MAJOR. |
| Add a new spec under SEM-00N+. | MINOR. |
| Add an OPTIONAL field to the envelope. | MINOR. |
| Add an additional MUST clause that does not invalidate prior conformant artifacts. | MINOR. |
| Clarify wording without changing semantics. | PATCH or errata. |
| Fix a typo, formatting, or link. | Errata. |

When in doubt, the higher bump applies. Under-bumping is a freeze
violation; over-bumping is not.

---

## 6. Compatibility Guarantees Under the Freeze

For the lifetime of v1, the freeze guarantees:

1. **Reader robustness.** A reader implemented against v1.0.0 MUST
   continue to function against any v1.x.y release. Unknown additive
   fields MUST be ignored, not rejected.
2. **Writer discipline.** A producer claiming v1 conformance MUST stamp
   every artifact under a published version of every relevant dimension
   (SEM-005 §2). Producing under a version not in the registry is
   non-conformant.
3. **Historical reproducibility.** Every artifact produced under v1
   MUST remain replayable for the lifetime of v1 in accordance with
   SEM-006 §5 and SEM-008 §8.
4. **Cross-dimension stability.** The compatibility matrix between
   ontology, schema, surface methodology, governance semantic, and
   layer versions (SEM-005 §8.1) is frozen at v1. Combinations declared
   invalid at freeze remain invalid; new combinations require a MINOR
   layer bump.
5. **No retroactive invalidation.** Artifacts conformant to v1 at
   production MUST remain readable as conformant for v1's lifetime,
   regardless of subsequent MINOR/PATCH amendments.

---

## 7. Prohibited Breaking Changes Under v1

The following changes are **prohibited entirely under v1**. They cannot
be introduced by PATCH, MINOR, or amendment. They can be introduced only
by a v2 layer (a successor freeze, with full migration discipline).

1. Adding, removing, or renaming any value of `ConfidenceLevel`.
2. Adding, removing, or renaming any value of `GovernanceState`.
3. Adding a fourth canonical surface to the closed taxonomy (SEM-002 §2).
4. Removing the surface compatibility matrix's INCOMPATIBLE cells.
5. Permitting confidence upgrades without a `ConfidenceAssessment`
   record.
6. Permitting governance inheritance across non-deterministic
   derivations.
7. Permitting point-in-time queries to consume artifacts with
   `produced_at > T_anchor`.
8. Permitting silent supersession (supersession without governance
   reference).
9. Permitting envelope re-emission to strip envelope fields.
10. Permitting orphan derivations to be silently admitted to the corpus.
11. Permitting lossy schema migration.
12. Permitting NAV-bearing numeric payloads without surface labels.
13. Replacing the immutability of the provenance DAG with mutable
    semantics.
14. Replacing temporal fencing with any softer rule.
15. Permitting hybrid reconstruction (SEM-006 §7.3) to be the default
    truth mode.

Every prohibition above corresponds to a foundational guarantee of v1.
Relaxing any of them changes what Caerus *means* by an institutional
research corpus, and is a v2 event.

---

## 8. Permitted Evolution Under v1

The following classes of change are permitted under v1 and MAY be
landed as MINOR or PATCH amendments without successor freeze:

1. Adding new `object_type` entries to the ontology (SEM-005 §3.1).
2. Adding OPTIONAL fields to existing envelopes.
3. Publishing new schemas (SEM-008 §2).
4. Publishing new `methodology_revision` numbers for existing surfaces
   (SEM-002 §8) **provided** historical NAV values are unchanged.
5. Adding new lineage edge types (e.g., relational metadata) that do not
   reinterpret existing edges.
6. Adding new downgrade codes (SEM-007 §5) that strengthen the
   confidence regime (i.e., that can only lower confidence further).
7. Publishing additional governance health metrics, audit findings types,
   or report formats that do not alter underlying state semantics.
8. Loosening a constraint **after** explicit MINOR review (e.g.,
   broadening a range, removing an unused tightening).
9. Adding new `ReplayRun`, `SchemaMigration`, `ConfidenceAssessment`,
   `GovernanceTransition`, `Deployment`, or `Rollback` object instances
   to the corpus (these are first-class records, not schema changes).

Permitted evolution MUST be governed by an FR in the registry and MUST
be reflected in the appropriate spec's errata or in a new spec entry.

---

## 9. Replay Guarantees of the Freeze

The freeze itself is replay-safe.

For any object produced under v1.x.y, the corpus MUST be able to:

1. Reconstruct the **exact** envelope semantics in force at production
   time (using the spec versions then current).
2. Reconstruct the **exact** governance, confidence, and surface state
   in force at the artifact's `as_of` (via SEM-006).
3. Distinguish between **canonical truth** (the object as recorded) and
   **reconstructed truth** (a derived view) in every response.

Subsequent MINOR/PATCH amendments to v1 MUST NOT invalidate this
guarantee. If an amendment would invalidate replay for any prior
artifact, it is a MAJOR change by definition (see §5) and is therefore
not eligible as a v1 amendment.

---

## 10. Ontology Freeze Boundary

The ontology — the set of `object_type` values and their structural
definitions — is **frozen at v1.0** as enumerated in the architecture
document §2 and as governed by SEM-001 / SEM-005.

Under v1:

- **Permitted:** Adding new `object_type`s (MINOR ontology bump per
  SEM-005 §3.1). The newly added types MUST themselves be conformant to
  SEM-001 envelopes from inception.
- **Permitted:** Adding OPTIONAL fields to existing object types.
- **Prohibited:** Removing or renaming any existing `object_type`.
- **Prohibited:** Removing or renaming any required field of any
  existing object type.
- **Prohibited:** Changing the structural interpretation of any field
  (e.g., re-purposing `chain_status = REPAIRED` to mean something
  different).
- **Prohibited:** Reorganising the object graph relationships
  (architecture §3) such that previously-distinct edge types collapse
  or distinct edges merge.

A new institutional research concept that cannot be modeled within the
v1 ontology by additive extension is, by definition, a v2 candidate.
Under v1 it MUST be either decomposed into existing types or deferred.

---

## 11. Governance Freeze Boundary

The governance semantic model — `GovernanceState` enumeration,
inheritance rules, FR-lifecycle mapping, observation semantics, rollback
semantics, deployment semantics — is **frozen at v1.0** as governed by
SEM-004.

Under v1:

- **Permitted:** Adding new edge types to the governance DAG that do not
  re-interpret existing edges (MINOR per SEM-005 §6.1).
- **Permitted:** Adding `GovernanceFR` entries; modifying FR-lifecycle
  states of individual FRs per the process documents.
- **Permitted:** Refining `observation_criteria` with documented
  rationale (SEM-004 §5.2).
- **Prohibited:** Adding, removing, or renaming any `GovernanceState`
  value.
- **Prohibited:** Permitting governance inheritance across
  non-deterministic derivations or across surface boundaries
  (SEM-004 §3.3).
- **Prohibited:** Permitting `DEPLOYED` status without backing
  `Deployment` evidence — administratively deployed FRs are a transient
  debt class, not a permanent state.
- **Prohibited:** Silent rollback (rollback without a `Rollback` object
  and authorising `AuditFinding`).

The state-selection precedence (SEM-004 §2.1) is frozen. The
"most-cautious-state-wins" rule is non-negotiable under v1.

---

## 12. Confidence Freeze Boundary

The confidence semantic model — the closed lattice, propagation
algebra, downgrade triggers, reassessment rules, invalidation rules — is
**frozen at v1.0** as governed by SEM-007.

Under v1:

- **Permitted:** Adding new downgrade codes that can only *lower*
  confidence further (e.g., a new `CHAIN_OBSERVED_REGRESSION` code).
- **Permitted:** Publishing additional `ConfidenceAssessment` records.
- **Permitted:** Refining the reporting chain (SEM-007 §8) with
  additional metadata that does not change the lattice or the algebra.
- **Prohibited:** Adding any new value to the confidence lattice
  (`BROKER_AUTHORITATIVE`, `HIGH`, `PARTIAL_CONFIDENCE`, `LOW`,
  `UNAVAILABLE`).
- **Prohibited:** Adding a downgrade code whose effect is to *raise*
  confidence.
- **Prohibited:** Permitting silent upgrades — confidence MUST rise only
  via `ConfidenceAssessment`.
- **Prohibited:** Permitting confidence to exceed surface ceiling,
  governance ceiling, propagation floor, or trigger floor.
- **Prohibited:** Reinterpreting the lattice as a probability
  distribution. The lattice is a discrete trust ordering and remains so
  under v1 (SEM-007 §2.2).

---

## 13. Truth-Surface Freeze Boundary

The closed surface taxonomy (`LIVE_BROKER_PAPER_NAV`,
`OPERATIONAL_SHADOW_NAV`, `RESEARCH_BACKTEST_NAV`) and the surface
compatibility matrix are **frozen at v1.0** as governed by SEM-002.

Under v1:

- **Permitted:** Bumping `methodology_revision` on an existing surface
  for non-disruptive improvements (SEM-005 §5).
- **Permitted:** Adding `ReconciliationReport` instances and other
  side-by-side viewing tools that respect §3 of SEM-002.
- **Prohibited:** Adding a fourth canonical surface.
- **Prohibited:** Loosening any INCOMPATIBLE cell of the compatibility
  matrix.
- **Prohibited:** Permitting cross-surface concatenation or aggregation
  outside the override path (SEM-002 §3.2).
- **Prohibited:** Permitting display layers to render NAV-bearing
  numbers without surface labels.

A new surface that would change historical NAV values is a v2 event by
construction (SEM-002 §8). Under v1 it MUST be introduced as a new
surface identifier (e.g., `RESEARCH_BACKTEST_NAV_V2`) — not as a
re-interpretation of an existing one.

---

## 14. Replay-Safety Freeze Boundary

The temporal-honesty contract (SEM-006) is **frozen at v1.0**.

Under v1:

- **Permitted:** Adding new `ReplayRun` instances; refining the replay
  subsystem's implementation; adding observability of replay quality.
- **Permitted:** Adding new `replay_quality` values that strengthen the
  reporting (e.g., a granular `BEST_EFFORT_DEPENDENCY_DRIFT`) — provided
  they do not relax any existing rule.
- **Prohibited:** Permitting any artifact with `as_of > T_anchor` or
  `produced_at > T_anchor` to participate in an as-of-`T_anchor` view.
- **Prohibited:** Permitting hybrid reconstruction (SEM-006 §7.3) to be
  the default truth mode.
- **Prohibited:** Permitting silent supersession across `T_anchor`
  boundaries.
- **Prohibited:** Permitting unreplayable states to be substituted with
  current-state results.
- **Prohibited:** Permitting reconstruction pollution — using
  post-anchor artifacts to "improve" a point-in-time view.

The "what did Caerus believe at T?" question (see SEM README and SEM-006
§1) is constitutional under v1. Any amendment that weakens the
implementation's ability to answer it correctly is prohibited.

---

## 15. Schema-Evolution Freeze Boundary

The schema-evolution discipline (SEM-008) is **frozen at v1.0**.

Under v1:

- **Permitted:** Publishing new schema versions per SEM-008 §2.
- **Permitted:** Forward-only and migrated migrations conforming to
  SEM-008 §5.
- **Permitted:** Adding new `SchemaMigration` instances.
- **Prohibited:** Lossy migration (SEM-008 §12.1).
- **Prohibited:** In-place schema reuse (SEM-008 §12.2).
- **Prohibited:** Hidden schema dispatch (SEM-008 §12.3).
- **Prohibited:** Skip-publication production (SEM-008 §12.4).
- **Prohibited:** Migration without replay audit (SEM-008 §12.5).
- **Prohibited:** Cross-MAJOR silent migration (SEM-008 §12.6).

Every migration under v1 MUST satisfy the §8.1 replay-stability test of
SEM-008. The test is a freeze invariant: implementation MAY add stricter
tests; it MAY NOT relax.

---

## 16. Freeze Verification

The freeze itself is auditable. The MCP audit subsystem MUST be able to
emit, on demand, a **Freeze Conformance Report** that confirms:

1. Each spec listed in §2 exists in the repository at the exact byte
   contents published on 2026-05-21 (modulo recorded errata).
2. No closed enumeration in any spec has been silently modified.
3. No MUST/MUST NOT clause has been silently modified.
4. The compatibility matrix in SEM-002 is intact.
5. The confidence lattice in SEM-007 is intact.
6. The governance state enumeration in SEM-004 is intact.
7. Every artifact produced after 2026-05-21 carries an
   `annotations.semantic_layer_version` consistent with this freeze
   (SEM-005 §7.2 SHOULD becomes effectively MUST under freeze
   verification).

Failures to verify MUST be raised as `SEMANTIC_FREEZE_VIOLATION` audit
findings with severity `CRITICAL`.

---

## 17. Relationship to Other Freeze Documents

This document is the root of the freeze. Four companion documents
elaborate specific surfaces of the freeze:

| Document | Purpose |
|---|---|
| `IMPLEMENTATION_CONFORMANCE_GUIDE_v1.md` | Codex / implementation contract. MUST/MUST NOT for builders. |
| `REGISTRY_INVARIANTS_v1.md` | Hard invariants the MCP registry MUST satisfy. |
| `MCP_IMPLEMENTATION_BOUNDARIES_v1.md` | Constitutional IS / IS NOT / MAY / MUST NEVER for the MCP. |
| `REPLAY_AND_RECONSTRUCTION_GUARANTEES_v1.md` | Temporal-honesty guarantees the MCP MUST provide. |

If a companion document conflicts with a SEM-00N spec, the SEM-00N spec
governs and the companion document MUST be corrected as errata. If a
companion document conflicts with this freeze, this freeze governs.

---

## 18. Implementation-Phase Posture

Implementation work commencing after 2026-05-21 (including all Codex
implementation tasks) MUST treat the frozen baseline as a closed input.

Implementation MAY:

- Add tests that verify conformance.
- Add tooling that reports conformance.
- Add observability surfaces (metrics, logs) that expose conformance
  state.
- Add migration utilities and ingestion adaptors.

Implementation MUST NOT:

- Redefine an enumeration value in code that contradicts the freeze.
- Introduce a new envelope field outside an additive MINOR amendment.
- Substitute a non-conformant default for a missing required field.
- Permit any of the prohibited transitions or omissions enumerated in
  §7, §10–§15.

A pull request whose semantic effect would require a MAJOR amendment to
this freeze MUST be rejected at review and re-opened under the
amendment process (§4).

---

## 19. Sunset and Successor

This freeze is sunset only by publication of `SEMANTIC_FREEZE_v2.md`.
Until then, every spec listed in §2 remains canonical, every
prohibition remains in force, and every MINOR/PATCH amendment MUST be
recorded against this freeze.

A v2 freeze MUST inherit the institutional question, the temporal-
honesty rule, and the immutability of the provenance DAG. These are
constitutional commitments that survive any version transition.

---

## 20. Errata

*(none at v1)*

---

*SEM-FREEZE-v1 — 2026-05-21. Caerus Semantic Contract Layer.*
*Owner: Architecture / Research Infrastructure.*
*Classification: Institutional — Constitutional Implementation Boundary.*
