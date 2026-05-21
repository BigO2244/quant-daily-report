---
last_reviewed: 2026-05-21
owner: architecture
category: architecture
criticality: critical
canonical: true
related_systems: [research, governance, mcp, attribution, shadow, regime]
spec_id: SEM-CONFORMANCE-v1
spec_version: v1
supersedes: null
governs: [SEM-001, SEM-002, SEM-003, SEM-004, SEM-005, SEM-006, SEM-007, SEM-008]
---

# Implementation Conformance Guide v1 — Caerus Research MCP

**Spec ID:** SEM-CONFORMANCE-v1
**Version:** v1
**Date:** 2026-05-21
**Status:** Canonical — **Authoritative Implementation Contract**
**Normative Language:** RFC 2119
**Frozen Under:** `SEMANTIC_FREEZE_v1.md`

---

## 1. Purpose

This document is the **authoritative implementation contract** for the
Caerus Research MCP and every adjacent producer, consumer, validator,
ingestion routine, retrieval surface, and audit tool built against the
Semantic Contract Layer v1.

It exists so that Codex (and any human implementer) can build the system
without re-deriving semantics from the eight underlying specifications.
The guide collects, in RFC 2119 form, every mandatory implementation
invariant the freeze imposes.

If a clause here conflicts with a SEM-00N spec, the SEM-00N spec
governs and this guide is corrected via errata. If this guide is silent
on a topic, the conservative default applies (do not relax any clause of
SEM-001..008).

---

## 2. Conformance Levels

An implementation is **conformant** when it satisfies every MUST and
MUST NOT clause in this document. SHOULD/SHOULD NOT clauses guide
prudent implementation; deviation requires documented rationale.
MAY clauses describe latitude.

Three conformance scopes are defined:

| Scope | Audience | Conformance Surface |
|---|---|---|
| **Producer** | Any module/script/agent that emits research objects. | §3, §4, §5, §7, §9, §10. |
| **Consumer** | Any tool that reads from the corpus (MCP cognition, scripts, dashboards). | §3, §6, §7, §8, §10, §11. |
| **MCP** | The Caerus Research MCP itself. | All sections. |

An implementation MAY be conformant in one scope and silent in another
(e.g., a producer that does not retrieve), but it MUST be fully
conformant in every scope it claims.

---

## 3. Mandatory Implementation Invariants

The following invariants are non-negotiable. They are stated as
absolutes.

### 3.1 Envelope Invariants (SEM-001)

- I-001. Every research object MUST carry the canonical envelope
  (SEM-001 §2) at production. Envelope absence is non-conformant.
- I-002. Every required envelope field MUST be present and
  non-defaulted. Defaults for required fields are forbidden.
- I-003. `object_id` MUST be deterministic — same identity inputs
  produce same id (SEM-001 §5).
- I-004. `lineage.node_id` MUST be globally unique across the corpus.
- I-005. `confidence.level` MUST NOT be null; `governance.state` MUST
  NOT be null. `UNAVAILABLE` and `UNGOVERNED` are explicit values.
- I-006. NAV-bearing payloads MUST carry surface labels
  (`surface.nav_surface_type`, `surface.execution_realism`).
- I-007. Non-raw objects MUST have at least one entry in
  `lineage.parent_refs`.

### 3.2 Provenance Invariants (SEM-003)

- I-010. The provenance DAG MUST remain acyclic. Cycle introduction
  MUST be refused at production.
- I-011. Every edge MUST be typed (SEM-003 §2.1).
- I-012. Every non-raw object MUST trace back through `DERIVED_FROM`
  edges to a raw data source or governance document.
- I-013. `provenance.deterministic = false` MUST be accompanied by a
  non-null `provenance.source_state_hash`.
- I-014. Edges are immutable once committed. Corrections require new
  `SUPERSEDES` edges and `AuditFinding` records.
- I-015. `provenance.transformation` MUST describe the actual
  transformation performed.
- I-016. Hidden dependencies (inputs read but not declared) MUST NOT
  exist. Every numeric dependency MUST appear in
  `provenance.input_object_ids` or `provenance.source_paths`.

### 3.3 Surface Invariants (SEM-002)

- I-020. Surface assignment MUST be explicit. Silent inference is
  forbidden.
- I-021. Cross-surface aggregation MUST be refused except via the
  override mechanism (SEM-002 §3.2), which MUST downgrade confidence to
  `LOW` and stamp `annotations.surface_override`.
- I-022. The surface confidence ceiling MUST cap any object's stamped
  confidence.
- I-023. Display layers MUST render surface labels adjacent to every
  NAV-bearing number.
- I-024. Concatenation MUST NOT elevate the surface of any segment.

### 3.4 Confidence Invariants (SEM-007)

- I-030. Confidence MUST be stamped as `min(surface_ceiling,
  governance_ceiling, propagation_floor, trigger_floor)`.
- I-031. Confidence MUST NOT exceed `min` of material parents'
  confidence.
- I-032. Confidence MUST NOT be raised except via a covering
  `ConfidenceAssessment` whose `temporal_validity` includes the
  reference instant.
- I-033. Every applicable downgrade trigger MUST be listed in
  `confidence.downgrade_reasons`. Hiding any applicable code is
  non-conformant.
- I-034. `confidence.rationale` MUST be non-empty.

### 3.5 Governance Invariants (SEM-004)

- I-040. Governance inheritance MUST follow the state-selection
  precedence (SEM-004 §2.1).
- I-041. Governance MUST NOT inherit across non-deterministic
  derivations.
- I-042. Governance MUST NOT inherit across surface boundaries.
- I-043. Governance MUST NOT inherit across ontology version changes.
- I-044. An `UNGOVERNED` material parent MUST defeat inheritance unless
  the parent is explicitly declared `context` in
  `provenance.materiality_map`.
- I-045. FR-status changes MUST emit `GovernanceTransition` objects.
- I-046. Rollback MUST emit a `Rollback` object; affected coverage MUST
  be recomputed.

### 3.6 Temporal Invariants (SEM-006)

- I-050. `T_anchor` MUST NOT be in the future.
- I-051. Point-in-time queries MUST NOT consume objects with
  `as_of > T_anchor` or `produced_at > T_anchor`.
- I-052. Governance and confidence at `T_anchor` MUST be reconstructed
  from then-current state; current state MUST NOT be projected onto past
  objects.
- I-053. The `CANONICAL` vs `RECONSTRUCTED` vs `HYBRID` mode tag MUST be
  set on every reply that returns a research object.
- I-054. Hybrid reconstruction MUST be opt-in and clearly labelled; the
  default MUST be strict point-in-time.

### 3.7 Versioning Invariants (SEM-005, SEM-008)

- I-060. Every artifact MUST stamp truthfully every applicable version
  field (`schema_version`, `ontology_version`,
  `methodology_revision`).
- I-061. Production under an unpublished schema MUST be refused.
- I-062. Lossy schema migration MUST NOT exist. Every migrated
  migration MUST have a working reverse migration.
- I-063. Cross-MAJOR coercion at read time MUST NOT exist. Cross-MAJOR
  reads require explicit migration application.
- I-064. PATCH bumps MUST NOT mask MINOR or MAJOR changes.

### 3.8 Freeze Invariants (SEM-FREEZE-v1)

- I-070. The closed enumerations of v1 (confidence, governance, surface,
  chain_status, edge types) MUST NOT be silently extended at the
  implementation level.
- I-071. Any change requiring a MAJOR amendment to the freeze MUST be
  refused in implementation until the amendment is published.
- I-072. The audit subsystem MUST be capable of emitting a Freeze
  Conformance Report (SEM-FREEZE-v1 §16).

---

## 4. Required Validation Behaviour

The MCP ingestion and retrieval layers MUST implement validators that
enforce the following.

### 4.1 Envelope Validation

A conformant validator MUST reject objects on every code in SEM-001 §8
(`M001`..`M013`). Rejections are surfaced as findings of type
`ENVELOPE_INVALID` with the failing code.

### 4.2 Provenance Validation

A conformant validator MUST:

- Refuse acyclicity violations at ingest.
- Refuse missing-parent references at ingest.
- Surface orphans as `ORPHAN_DERIVATION` findings (HIGH severity).
- Compute and verify `lineage.transformation_chain_hash` against the
  formula in SEM-003 §9.

### 4.3 Surface Validation

A conformant validator MUST:

- Reject NAV-bearing payloads without surface labels (SEM-002 §9).
- Refuse INCOMPATIBLE cross-surface combinations absent an override.
- Stamp `annotations.surface_mismatch` on CAUTIOUS_OK comparisons.

### 4.4 Confidence Validation

A conformant validator MUST:

- Verify `confidence.level <= surface_ceiling`.
- Verify `confidence.level <= governance_ceiling`.
- Verify `confidence.level <= min(material parents' confidence)`.
- Verify that every applicable downgrade trigger appears in
  `confidence.downgrade_reasons`.
- Emit `CONFIDENCE_SILENT_INFLATION` finding if a stamped value exceeds
  the computed floor without a covering `ConfidenceAssessment`.

### 4.5 Governance Validation

A conformant validator MUST:

- Refuse objects declaring `governance.state = GOVERNED_*` with empty
  `governing_frs`.
- Refuse objects whose declared state contradicts the state-selection
  precedence applied to their `governing_frs` set.
- Refuse `governance.state` claims inconsistent with the FR registry at
  ingest time.

### 4.6 Temporal Validation

A conformant validator MUST:

- Refuse `produced_at > as_of`.
- Refuse non-UTC timestamps.
- Refuse `T_anchor` values in the future or malformed.
- Refuse point-in-time queries that would require post-anchor inputs.

### 4.7 Schema-Version Validation

A conformant validator MUST:

- Refuse objects with `schema_version` not in the registry as
  `PUBLISHED` (or `DEPRECATED` within the window).
- Refuse objects whose payload structure does not match the declared
  `schema_version` — i.e., MUST detect `SCHEMA_SILENT_DRIFT`.
- Refuse new production under `RETIRED` schemas.

---

## 5. Prohibited Implementation Shortcuts

The following shortcuts are forbidden under all circumstances.

- P-001. **Default-filling required fields.** If a required envelope
  field is absent at production, the producer MUST refuse to emit,
  not silently default.
- P-002. **Synthesising surface labels.** Surfaces MUST be produced,
  not inferred (SEM-002 §4.2).
- P-003. **Stripping envelope fields at re-emission.** Tools MUST NOT
  omit envelope fields when forwarding objects (SEM-001 §9).
- P-004. **Hiding downgrade codes.** Producers MUST list every
  applicable code in `confidence.downgrade_reasons`.
- P-005. **Silent supersession.** A `SUPERSEDES` edge MUST cite a
  governing reference (`AuditFinding`, `GovernanceFR`, or scheduled
  re-run).
- P-006. **Silent invalidation.** Invalidated objects MUST remain in
  the corpus with `is_invalidated = true`; deletion is forbidden.
- P-007. **In-place schema reuse.** Republishing different content
  under an already-published version string is forbidden.
- P-008. **Lossy migration.** A migration that cannot be reversed
  byte-for-byte to its source representation MUST NOT exist.
- P-009. **Inferring at present** that "this is what was true at T."
  The retrieval layer MUST distinguish canonical vs. reconstructed
  vs. hybrid (SEM-006 §4.3).
- P-010. **Persisting derived reconstructions as new corpus objects.**
  Reconstructions are views; they are not records (SEM-006 §4.2).
- P-011. **Bypassing the FR-registry parser** with hand-coded
  governance assertions.
- P-012. **Hot-patching closed enumerations** in code paths.
  Enumeration additions require a MAJOR amendment (SEM-FREEZE-v1 §7).
- P-013. **Treating `not_started` as a long-lived state.** SEM-004 §5.1
  forbids it after one trading day.
- P-014. **Allowing display layers to render NAV without surface
  context.**
- P-015. **Substituting current-state results for unreplayable
  segments.**

---

## 6. Deterministic Rebuild Requirements

The MCP registry MUST be **rebuildable from source** at any time. This
is a constitutional requirement.

### 6.1 Rebuild Inputs

A rebuild MUST consume only:

1. The source artifacts in the storage substrate (architecture §1).
2. The committed source-controlled producers (with reachable tags or
   release SHAs for every historical schema version).
3. The committed `fr_registry.md` and related governance source.
4. The committed Semantic Contract Layer.

A rebuild MUST NOT consume:

- The prior registry's index (a rebuild is from-scratch by definition).
- Any external mutable state.

### 6.2 Rebuild Determinism

Given identical inputs (§6.1), two successive rebuilds MUST produce
byte-identical registries (modulo SEM-003 §3.4 encoding differences in
index serialisation).

If two rebuilds diverge, a `REGISTRY_REBUILD_DIVERGENCE` finding MUST
be raised with the differing object set.

### 6.3 Incremental Indexing

Incremental indexing (architecture §12) is permitted as an optimisation
but MUST satisfy the consistency property: at any moment, an incremental
state MUST be reachable by a from-scratch rebuild from the same source.

The MCP MUST periodically verify this property by performing a shadow
rebuild and diffing against the incremental index.

---

## 7. Required Replay Behaviour

The MCP MUST implement replay (SEM-006 §5) with the following
behaviours.

### 7.1 ReplayRun Production

- R-001. Every replay execution MUST produce a `ReplayRun`
  envelope-bearing object (SEM-003 §7.4).
- R-002. `ReplayRun.data.replay_result` MUST be one of
  `IDENTICAL | DIVERGENT | UNREPLAYABLE`.
- R-003. `DIVERGENT` replays MUST cite divergence specifics in
  `divergence_summary`.
- R-004. `UNREPLAYABLE` replays MUST cite the unreachable component(s).

### 7.2 Replay Quality

- R-005. Replays of deterministic-chain objects MUST be marked
  `replay_quality = GUARANTEED`.
- R-006. Replays of non-deterministic objects with intact
  `source_state_hash` MUST be marked `GUARANTEED`.
- R-007. Replays of external-resource or grandfathered objects MUST be
  marked `BEST_EFFORT` with reasons.
- R-008. Replays MUST NOT be silently substituted by current-state
  results.

### 7.3 Replay Trigger Discipline

- R-009. Replays MAY be triggered manually by an operator, by an audit,
  or by a scheduled freshness verifier.
- R-010. The MCP MUST NOT trigger speculative replays that produce
  records inconsistent with on-demand requests.

---

## 8. Required Provenance Guarantees

The MCP MUST provide every guarantee in SEM-003 §3:

- G-001. **Traceability** — every ancestor chain terminates in raw data
  or governance documents.
- G-002. **Completeness** — no hidden dependencies.
- G-003. **Faithfulness** — `provenance.transformation` matches
  implementation.
- G-004. **Reproducibility** — deterministic objects re-execute to
  byte-identical results.
- G-005. **Identifiability** — non-deterministic objects re-execute
  identically under recorded `source_state_hash`.

The implementation MUST produce, on demand, an **ancestor walk** for
any object: a serialised representation of every upstream object up to
roots, with edge types intact.

---

## 9. Required Confidence Propagation Behaviour

The MCP MUST compute confidence per SEM-007 §3 and §4.

### 9.1 Stamping (Producer Surface)

- C-001. Producers MUST compute the propagation floor from declared
  material parents.
- C-002. Producers MUST apply surface ceiling.
- C-003. Producers MUST apply governance ceiling.
- C-004. Producers MUST apply all triggering downgrades.
- C-005. The final stamped value MUST be the meet (`min`) of the
  above.

### 9.2 Re-Evaluation (Retrieval Surface)

- C-010. The retrieval layer MUST re-verify the stamped value at
  hydration. A stamped value exceeding the recomputed floor MUST be
  refused.
- C-011. The retrieval layer MUST return the confidence chain
  (limiting component, parent ref, downgrade codes) — not the
  bare level.

### 9.3 Reassessment

- C-020. Reassessments MUST be recorded as `ConfidenceAssessment`
  objects.
- C-021. Upgrades without a covering active assessment MUST be refused.
- C-022. Assessments MUST be governed (operator + audit, or FR).

---

## 10. Required Temporal Fencing Behaviour

The MCP MUST enforce temporal fencing on every point-in-time query per
SEM-006 §3.

- T-001. The retrieval layer MUST translate trade-date anchors into
  UTC instants using the canonical calendar (US/Eastern + NYSE
  holiday calendar), and MUST stamp `annotations.anchor_resolution`.
- T-002. The retrieval layer MUST filter inputs by
  `min(as_of, indexed_at) <= T_anchor`.
- T-003. The retrieval layer MUST resolve supersession to the
  pre-anchor state.
- T-004. The retrieval layer MUST recompute governance-at-anchor and
  confidence-at-anchor.
- T-005. The retrieval layer MUST tag every response with
  `annotations.truth_mode`.
- T-006. The retrieval layer MUST refuse hybrid mode unless explicitly
  requested.
- T-007. The retrieval layer MUST raise
  `RECONSTRUCTION_POLLUTION` findings post-hoc if any fence
  violation is detected.

---

## 11. Required Governance Inheritance Behaviour

The MCP MUST implement governance inheritance per SEM-004 §3.

- GV-001. At ingestion, the MCP MUST compute `governance.state` and
  `governing_frs` from the parent set under the inheritance rules.
- GV-002. The MCP MUST apply the state-selection precedence
  (`GOVERNED_OBSERVING > GOVERNED_DEFERRED > GOVERNED_DEPLOYED >
  GOVERNED_DRAFT > UNGOVERNED`).
- GV-003. The MCP MUST break inheritance on non-deterministic
  derivations, cross-surface transformations, and ontology-version
  crossings.
- GV-004. The MCP MUST recompute governance-at-anchor for point-in-time
  queries using `GovernanceTransition` history.
- GV-005. The MCP MUST refuse to recognise forbidden FR-status
  transitions (SEM-004 §4.2).

---

## 12. RFC 2119 Index

This section consolidates the principal normative clauses by category.

### 12.1 MUST

The implementation MUST satisfy every clause above tagged `I-*`, `R-*`,
`G-*`, `C-*`, `T-*`, `GV-*`. In particular:

- MUST validate every envelope on ingest and hydration.
- MUST enforce the provenance DAG invariants.
- MUST stamp confidence with full chain visibility.
- MUST apply temporal fencing on every point-in-time query.
- MUST distinguish CANONICAL/RECONSTRUCTED/HYBRID truth modes.
- MUST produce `ReplayRun` records for every replay.
- MUST maintain a rebuildable registry.
- MUST refuse cross-MAJOR auto-coercion.

### 12.2 MUST NOT

- MUST NOT permit any prohibited shortcut listed in §5.
- MUST NOT default-fill required envelope fields.
- MUST NOT permit confidence upgrades absent a `ConfidenceAssessment`.
- MUST NOT permit cross-surface aggregation absent an override.
- MUST NOT permit hidden writes (§11 of `MCP_IMPLEMENTATION_BOUNDARIES_v1`).
- MUST NOT permit autonomous orchestration or workflow triggering.
- MUST NOT permit broker credential access.
- MUST NOT permit future-information contamination.
- MUST NOT silently extend closed enumerations.
- MUST NOT persist derived reconstructions as new corpus records.

### 12.3 SHOULD

- SHOULD include `annotations.semantic_layer_version` on every produced
  object (becomes effectively MUST under Freeze Conformance Report).
- SHOULD prefer forward-only migrations over migrated migrations
  (SEM-008 §5.1).
- SHOULD batch ingestion for performance; SHOULD verify incremental
  state via periodic shadow rebuilds.
- SHOULD provide observability for confidence-chain decisions, surface
  override events, and rollback-affected coverage sets.
- SHOULD report `BEST_EFFORT` replay reasons granularly to aid debug.

### 12.4 SHOULD NOT

- SHOULD NOT introduce additional confidence-level synonyms in display
  layers (e.g., presenting "Medium" for `PARTIAL_CONFIDENCE`).
- SHOULD NOT compute aggregate "blended" metrics across CAUTIOUS_OK
  pairs without an explicit research-statistic definition.
- SHOULD NOT serve cached governance state across an FR-status
  transition without re-evaluation.

### 12.5 MAY

- MAY add new `object_type` entries (with appropriate ontology MINOR
  bump).
- MAY add new OPTIONAL envelope fields.
- MAY add new `ReplayRun`, `ConfidenceAssessment`,
  `GovernanceTransition`, `Deployment`, `Rollback`, `SchemaMigration`
  instances.
- MAY introduce additional observability tooling and reporting surfaces.
- MAY add stricter validators than this guide requires.

---

## 13. Conformance Tests (Acceptance Criteria)

The MCP implementation MUST ship with — and pass — a conformance test
suite covering at minimum:

| Test Class | Verifies |
|---|---|
| Envelope round-trip | Every object type produces and re-parses a conformant envelope. |
| Validation rejection | Every `M001`..`M013` code triggers refusal on at least one synthetic input. |
| Provenance DAG | Cycle introduction is refused; orphan derivation surfaces a finding. |
| Cross-surface refusal | INCOMPATIBLE combination is refused; CAUTIOUS_OK annotates; override path requires both fields. |
| Confidence floor | Floor violation refused; downgrade triggers stack correctly. |
| Governance inheritance | Inheritance breaks on non-determinism / surface change / ontology change. |
| State-selection precedence | The most-cautious-applicable-state wins on multi-FR coverage. |
| Temporal fencing | Future inputs are fenced; anchor-pre-repair returns pre-repair object; hybrid is opt-in. |
| Replay determinism | A deterministic chain replays byte-identically; non-deterministic with intact hash replays identically. |
| Schema migration | Forward-only and migrated migrations pass the §8.1 stability test (SEM-008). |
| Registry rebuild determinism | Two from-scratch rebuilds produce byte-identical indices. |
| Freeze conformance report | The audit subsystem can emit a conformance report (SEM-FREEZE-v1 §16). |

A release that does not pass every test in this suite MUST NOT be
declared conformant to v1.

---

## 14. Errata

*(none at v1)*

---

*SEM-CONFORMANCE-v1 — 2026-05-21. Caerus Semantic Contract Layer.*
*Owner: Architecture / Research Infrastructure.*
*Classification: Institutional — Authoritative Implementation Contract.*
