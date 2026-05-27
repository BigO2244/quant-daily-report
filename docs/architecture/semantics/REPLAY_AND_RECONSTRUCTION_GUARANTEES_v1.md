---
last_reviewed: 2026-05-21
owner: architecture
category: architecture
criticality: critical
canonical: true
related_systems: [mcp, research, governance, attribution, shadow, regime]
spec_id: SEM-REPLAY-v1
spec_version: v1
supersedes: null
governs: [SEM-001, SEM-003, SEM-004, SEM-006, SEM-007, SEM-008]
---

# Replay and Reconstruction Guarantees v1 — Caerus Research MCP

**Spec ID:** SEM-REPLAY-v1
**Version:** v1
**Date:** 2026-05-21
**Status:** Canonical
**Normative Language:** RFC 2119
**Frozen Under:** `SEMANTIC_FREEZE_v1.md`

---

## 1. Purpose — The Institutional Question

This document formalises the temporal-honesty guarantees the Caerus
Research MCP MUST provide. It is the constitutional answer to the
single institutional question that justifies the entire Semantic
Contract Layer:

> **"What does it mean for Caerus to reconstruct what it believed at
> time T?"**

This document gives the operative answer in normative form. Every
guarantee below is a commitment the MCP MUST satisfy. Every
prohibition is a class of failure the MCP MUST structurally prevent.

The underlying mechanics are specified in SEM-006 (Point-in-Time
Reconstruction Semantics), SEM-003 §7 (Replay Guarantees in the
Provenance Contract), and SEM-008 §8 (Replay Stability Through
Migration). This document **freezes** them as a single, implementation-
ready guarantee surface and resolves the operative interpretive
questions Codex (and any human implementer) will face.

---

## 2. Definitions

Throughout this document:

- **`T_anchor`** — a UTC ISO-8601 instant against which a reconstruction
  or replay is computed. `T_anchor` MUST NOT be in the future relative
  to query execution.
- **Canonical truth** — an object as recorded at production time,
  retrieved from the corpus unchanged.
- **Reconstructed truth** — a derived view of "what would have been
  produced at `T_anchor`", computed from then-admissible inputs.
- **Hybrid reconstruction** — an explicitly opt-in view that uses
  `T_anchor`-era inputs but current-version producer logic.
- **Replay** — the operational re-execution of a producer (or chain)
  yielding a verification artifact (`ReplayRun`).
- **Then-current** — the value of any time-varying attribute (FR
  status, schema version, confidence assessment) at `T_anchor`.
- **At-present** — the value at the moment of query execution.

The two readings of governance, confidence, and schema (then-current
vs. at-present) MUST be distinguishable in every relevant response.

---

## 3. Point-in-Time Reconstruction Guarantees

The MCP MUST satisfy the following guarantees on every point-in-time
query.

### 3.1 Admissibility Rule (Hard Fence)

For every input `I` admitted into an as-of-`T_anchor` view:

```
min(I.temporal.as_of, I.indexed_at) <= T_anchor
   AND
I.provenance.produced_at <= T_anchor
   AND
I was not superseded at T_anchor
   AND
I was not invalidated at T_anchor
```

Any input failing any of these tests MUST be fenced out. This is the
**discoverability** rule (SEM-006 §8): an artifact's admissibility is
determined by both its existence and its **visibility** at `T_anchor`.

### 3.2 Recursive Fencing

Fencing applies **recursively** along the provenance DAG. The
admissibility test MUST be re-applied to every transitive ancestor of
every input.

If recursive fencing is infeasible because the producing module's
then-current version is unreachable, the query MUST be marked
`UNREPLAYABLE` for the affected segment with the unreachable
component(s) named.

### 3.3 Governance Reconstruction (Then-Current)

Governance state at `T_anchor` MUST be reconstructed from:

1. The FR registry restricted to entries `introduced <= T_anchor`.
2. The `GovernanceTransition` history restricted to events
   `transition_at <= T_anchor`.
3. The SEM-004 §3 inheritance algebra applied over (1) and (2).

The MCP MUST report both `governance_at_production` (as stamped on the
envelope) and `governance_at_anchor` (recomputed). In the common case
they are identical; when they differ, the difference MUST be surfaced.

### 3.4 Confidence Reconstruction (Then-Current)

Confidence at `T_anchor` MUST be reconstructed from the SEM-007 §3
algebra applied to:

1. The then-current surface ceiling.
2. The then-current governance ceiling (per §3.3).
3. The then-admissible parent confidence set.
4. The then-applicable downgrade triggers.
5. The `ConfidenceAssessment` records active at `T_anchor` (i.e., with
   `temporal_validity.valid_from <= T_anchor` and either
   `valid_to` null or `valid_to >= T_anchor`).

Current-state confidence MUST NOT be projected onto past objects.

### 3.5 Schema Reconstruction (Then-Current)

For point-in-time queries spanning a schema migration, the MCP MUST
dispatch each input under the schema version in force at that input's
`produced_at`. The MCP MUST NOT apply a post-migration schema to a
pre-migration object, or vice versa.

If a needed historical schema is `RETIRED` but its objects exist, the
MCP MUST read those objects under the retired schema's reader path
(SEM-008 §10). Retirement constrains production, not consumption.

### 3.6 Surface Reconstruction (Then-Current)

The surface taxonomy is closed (SEM-002 §2) and stable under v1.
Methodology revisions (SEM-002 §8) are versioned. For point-in-time
queries spanning a methodology revision, the MCP MUST report the
methodology revision in force at each input's `produced_at`. The MCP
MUST NOT silently apply a newer methodology to older artifacts.

---

## 4. Replay Guarantees

Replay is the operational verification of point-in-time correctness.
The MCP MUST classify every replay into one of three guarantee tiers.

### 4.1 Guaranteed Replay

A replay is **GUARANTEED** when all of the following hold:

- The target object is deterministic
  (`provenance.deterministic = true`), and its entire upstream chain is
  deterministic.
- Every input is individually replayable.
- The producing module at the source-controlled version recorded in
  `provenance.produced_by` is reachable (via tag, image, or
  equivalent).

For a `GUARANTEED` replay, the MCP MUST produce a result byte-identical
to the original object (modulo SEM-003 §3.4 encoding differences).

### 4.2 Identity-Hash Replay (Non-Deterministic with Recorded State)

A replay of a non-deterministic object is **GUARANTEED** when:

- `provenance.source_state_hash` is recorded and intact.
- The non-deterministic state (RNG seed, model checkpoint hash, etc.)
  is reachable.
- The producing module at the version recorded is reachable.

Under these conditions, re-execution MUST produce a result identical
to the original. The MCP MUST classify this case alongside §4.1 as
`GUARANTEED` for reporting purposes.

### 4.3 Best-Effort Replay

A replay is **BEST_EFFORT** when:

- External-resource dependencies (SEM-003 §5.4) have immutable
  snapshots but the producer module has been semantically refactored.
- The object is grandfathered (SEM-001 §11), with synthesised
  provenance.
- Recoverable but lossy reconstruction is achievable (e.g., a fallback
  producer version).

A `BEST_EFFORT` replay MUST cite the reasons for degraded guarantee in
`ReplayRun.data.degradation_reasons`. The MCP MUST NOT present
`BEST_EFFORT` results as `GUARANTEED`.

### 4.4 Unreplayable

A replay is **UNREPLAYABLE** when:

- A required input is missing from the corpus and not recoverable.
- A producer module's then-current version is unreachable.
- A non-deterministic derivation's `source_state_hash` is absent.

The MCP MUST report the failure with the specific unreachable
component(s) identified. The MCP MUST NOT substitute current-state
results for unreplayable segments. The MCP MUST NOT silently degrade
an unreplayable request to a best-effort.

### 4.5 Replay Audit Record

Every replay execution MUST produce a `ReplayRun` envelope-bearing
object (SEM-003 §7.4) containing:

- `data.replay_anchor` — `T_anchor`.
- `data.replay_target` — `object_id` being replayed.
- `data.replay_result` — `IDENTICAL | DIVERGENT | UNREPLAYABLE`.
- `data.replay_quality` — `GUARANTEED | BEST_EFFORT | UNREPLAYABLE`.
- `data.divergence_summary` — non-empty when result is `DIVERGENT`.
- `data.degradation_reasons` — non-empty when quality is
  `BEST_EFFORT`.
- `data.unreachable_components` — non-empty when result is
  `UNREPLAYABLE`.
- `provenance.input_object_ids` — every consumed input.

`ReplayRun` records are first-class. They are queryable by target id,
by anchor, by quality class, and by date. They are the auditable
substrate that proves the MCP's replay claims.

---

## 5. Historical State Guarantees

Beyond per-object replay, the MCP MUST be able to reconstruct
**aggregate historical state**.

### 5.1 Historical Coverage

The MCP MUST be able to compute, for any `T_anchor`:

- The set of strategies in the corpus at `T_anchor` and their
  `promotion_state`.
- The governance coverage ratio (SEM-004 §9) at `T_anchor`.
- The set of active `GovernanceFR`s and their statuses at `T_anchor`.
- The set of active `ConfidenceAssessment` records at `T_anchor`.
- The schema set published at `T_anchor`.

Each computation MUST consume only inputs admissible per §3.1.

### 5.2 Historical Trends

The MCP MUST be able to produce trends (governance coverage, orphan
counts, surface mismatch frequency, etc.) over a date range by
repeatedly applying §5.1 at sampled anchors. Trends MUST distinguish
between "computed at the time" and "reconstructed retrospectively"
when both are available.

### 5.3 Historical Promotion Decisions

For a historical `PromotionAssessment`, the MCP MUST be able to
reproduce the decision context at the assessment's `as_of`:

- The surface evidence then admissible.
- The governance state of all governing FRs then in force.
- The confidence values then computed.
- The blocking findings (`AuditFinding`) then open.

A query of the form "would the assessment have been
`governance_readiness = true` at `T_anchor`?" MUST be answerable from
historical state without consulting post-anchor information.

---

## 6. Canonical vs. Reconstructed Replay

The MCP MUST distinguish three truth modes in every response that
returns research data.

### 6.1 The Three Modes

| Mode | Meaning |
|---|---|
| **CANONICAL** | The object as recorded at production. Retrieved from the corpus unchanged. The default mode. |
| **RECONSTRUCTED** | A derived view: "what would have been produced at `T_anchor` from then-admissible inputs." Computed from inputs; not persisted as a corpus record. |
| **HYBRID** | An explicit opt-in: "what would today's producer compute from `T_anchor`-era inputs." Combines `T_anchor` data with at-present logic. Always degraded confidence. |

### 6.2 Mode Tagging

Every response MUST set `annotations.truth_mode` to one of the three
values. Mixing modes silently within a single response is forbidden.

For composite responses (e.g., a report combining canonical objects
with a derived synthesis), each component MUST be individually mode-
tagged, and the response envelope MUST aggregate the components'
modes (e.g., `annotations.truth_mode = "MIXED"` with per-component
detail).

### 6.3 Persistence Rules

- `CANONICAL` objects are persisted records. They are the corpus.
- `RECONSTRUCTED` views MUST NOT be persisted as new corpus objects
  (SEM-006 §4.2). They are views, not records. Caching them is
  permitted under SEM-REGISTRY-v1 §3.3 with invalidation discipline.
- `HYBRID` views MUST NOT be persisted as new corpus objects. They are
  inspection artifacts, never archived.

### 6.4 Default Mode

The default mode for an as-of-`T_anchor` query is `RECONSTRUCTED` if
the query targets a derived view, and `CANONICAL` if it targets a
stored object retrieved at its anchor-pre-supersession state.

`HYBRID` MUST be explicitly requested by the caller. The MCP MUST
refuse implicit hybrid responses — the caller's intent is opt-in.

---

## 7. Repaired-History Handling

A repair (SEM-002 §5.1, SEM-003 §6) corrects a prior segment of the
corpus. Repaired history is subject to strict honesty rules.

### 7.1 Persistence of Pre-Repair Records

A repair MUST NOT erase the pre-repair record. The pre-repair object
persists in the corpus with `is_superseded = true` and (where
applicable) `is_invalidated = true`.

### 7.2 Point-in-Time Reading of Repairs

For a query at `T_anchor`:

- **If `T_anchor` < repair's `effective_at`**: the MCP MUST return
  the pre-repair object with `annotations.known_incorrect_at_present =
  true` and `annotations.repair_reference` pointing to the repairing
  object.
- **If `T_anchor` >= repair's `effective_at`**: the MCP MUST return
  the repaired object with `annotations.chain_repair_inherited = true`
  per SEM-002 §5.1.
- **No anchor specified (current canonical)**: the MCP MUST return
  the repaired object.

### 7.3 The Honesty Rule

The MCP MUST surface, at every point-in-time response, whether the
returned object is:

- **Believed-correct at anchor and at present.** No annotation.
- **Believed-correct at anchor but known-incorrect at present.** Return
  the pre-repair object; annotate `known_incorrect_at_present`.
- **Repaired.** Return the post-repair object; annotate
  `chain_repair_inherited`.

The consumer MUST be able to determine which mode applies without
inspecting source code.

### 7.4 The Reconstruction-Pollution Prohibition

When reconstructing object `O` as-of `T_anchor`, the MCP MUST NOT use
any artifact produced after `T_anchor`, including:

- Repaired versions of `O` or its inputs whose `effective_at > T_anchor`.
- Governance transitions with `transition_at > T_anchor`.
- Confidence reassessments with `assessed_date > T_anchor`.
- Schema migrations completed after `T_anchor`.

A reconstruction at `T_anchor` MUST use **pre-repair** inputs — even
though they are known-incorrect today. This is the strict point-in-time
honesty rule.

### 7.5 Repair Records

Every repair MUST be recorded as a `ReplayRun` or equivalent
envelope-bearing object referencing the repaired segment. The
governing `AuditFinding` MUST be cited in
`annotations.chain_repair.governing_ref`.

The MCP MUST be able to enumerate, for any object id, the set of
repairs affecting it or its lineage. Repair history is a first-class
audit surface.

---

## 8. Future-Information Exclusion

Future-information exclusion is the strongest guarantee in this
document. It is structurally enforced.

### 8.1 The Exclusion Rule

In an as-of-`T_anchor` view, the MCP MUST NEVER consume any artifact
that:

- Has `produced_at > T_anchor`, OR
- Has `as_of > T_anchor`, OR
- Was unreachable at `T_anchor` (i.e., `indexed_at > T_anchor`), OR
- Was created by a producer process not yet in source control at
  `T_anchor`, OR
- Was created under a schema version not yet published at `T_anchor`.

These five conditions are individually necessary and jointly
sufficient for future-information exclusion.

### 8.2 Structural Enforcement

The retrieval layer MUST implement fencing as a query-rewriting step
that prepends an admissibility filter to every traversal. Fencing
MUST NOT be a post-query filter — it MUST be impossible for any
post-anchor artifact to enter intermediate computation.

### 8.3 Out-of-Band Operator Knowledge

A query MAY be tagged `operator_was_aware = true` (SEM-006 §8.1) to
indicate that an operator at `T_anchor` had out-of-band knowledge not
yet reflected in the corpus. Such tags are non-conformant by default
and MUST be explicitly documented in an `AuditFinding`.

The MCP MUST NOT use this tag as an automatic override. Each instance
MUST be governed by an audit finding that explicitly authorises the
inclusion of out-of-band knowledge in the reconstruction.

### 8.4 Detection of Violations

The audit subsystem MUST be capable of post-hoc detection of fencing
violations by replaying the original query against its declared
`T_anchor` and comparing against the served response. Divergence MUST
be raised as `RECONSTRUCTION_POLLUTION` finding (severity `CRITICAL`).

---

## 9. Replay Invalidation Conditions

A previously-`GUARANTEED` replay MAY become invalidated by:

| Trigger | Effect |
|---|---|
| Loss of producer module version (tag deleted, image purged). | Future replays of affected objects degrade to `BEST_EFFORT` or `UNREPLAYABLE`. |
| Loss of `source_state_hash`-referenced state (RNG seed file deleted, checkpoint corrupted). | Affected non-deterministic objects become `UNREPLAYABLE`. |
| Removal of immutable snapshot for an external-resource dependency. | Affected derivations become `UNREPLAYABLE`. |
| Discovery of an upstream `AuditFinding` of severity `CRITICAL`. | Downstream confidence floors at `LOW` per SEM-007 §5 `INPUT_INVALIDATED`; replay results MUST cite the invalidation. |
| Schema migration that breaks reverse-migration capability. | **Forbidden** under SEM-008 §5.2 — implementations MUST refuse such migrations rather than accept replay loss. |

The MCP MUST track and report the **replay-coverage trajectory** over
time: a degrading trajectory indicates institutional decay and MUST be
visible to operators.

---

## 10. Schema Migration Replay Guarantees

The strictest interaction between replay and schema evolution is
governed here.

### 10.1 No Migration That Breaks Replay

A schema migration that destroys reverse-reconstruction capability is
**forbidden** (SEM-008 §5.2, §12.1). Implementations MUST refuse such
migrations.

### 10.2 The Replay Stability Test

Before any migrated schema migration may transition from
`DEPLOYED_OBSERVING` to `DEPLOYED`, SEM-008 §8.1 specifies the
**replay stability test**: for representative pre-migration objects,
forward-and-reverse migration MUST reproduce the original byte-for-byte
(modulo SEM-003 §3.4).

This test is binding under v1. Skipping it is a freeze violation.

### 10.3 Cross-Migration Replays

A replay that spans a migration cutover MUST use schema-version-aware
dispatch: inputs before the cutover are read under their original
schema; inputs after are read under the new schema (SEM-008 §8.4).

The MCP MUST NOT silently apply one schema to objects of the other.

### 10.4 Producer Version Tagging Under Migration

A historical replay invokes the producer at the tag corresponding to
the artifact's `schema_version` (SEM-008 §8.2). Migrating an artifact
MUST NOT require running the producer at a different tag. Migration
re-encodes payload; it does not re-execute production.

### 10.5 Replay Stability Across Layer Versions

If a future v2 freeze is published, replays of v1-era objects MUST
remain `GUARANTEED` (or `BEST_EFFORT` with documented degradation)
indefinitely. A v2 freeze that destroys v1 replay capability is
non-conformant by construction — the institutional question is
constitutional.

---

## 11. Governance-State Replay Guarantees

Governance state is time-varying. Replays MUST treat governance with
the same temporal discipline as data.

### 11.1 Then-Current Governance

For any historical replay or reconstruction, the governance state
applied MUST be the state in force at the relevant `as_of` (for
objects) or `T_anchor` (for queries), computed from:

- The FR registry restricted to entries `introduced <= reference_time`.
- The `GovernanceTransition` history restricted to events
  `transition_at <= reference_time`.

### 11.2 Rollback Replay

A historical replay at `T_anchor` **before** a rollback's
`effective_at` MUST return the pre-rollback governance state. The
rollback is a temporally located event; it MUST NOT retroactively
rewrite prior governance (SEM-004 §6.3).

A historical replay **after** the rollback's `effective_at` MUST return
the post-rollback state, with `governance.state` recomputed under the
rollback's effects.

### 11.3 Observation State at Anchor

For an FR in `DEPLOYED_OBSERVING` at `T_anchor`, the MCP MUST be able
to compute the FR's `observation_status` as it would have been
evaluated at `T_anchor`, using only `metric_object_ids` admissible
under §3.1.

A claim of `satisfied` at `T_anchor` MUST be backed by evidence
admissible at `T_anchor`. Hindsight knowledge that the criteria were
met later MUST NOT be projected onto the anchor.

### 11.4 Audit-Finding History at Anchor

For a `PromotionAssessment` at `T_anchor`, the `blocking_findings` set
MUST include exactly the open `AuditFinding` objects at that anchor.
Findings closed before `T_anchor` MUST NOT appear; findings opened
after `T_anchor` MUST NOT appear.

---

## 12. The Institutional Definition

This section answers, normatively, the question stated in §1.

> **"What does it mean for Caerus to reconstruct what it believed at
> time T?"**

It means **exactly** the following:

1. The MCP MUST return, for query as-of `T`, the set of canonical
   research objects whose admissibility test (§3.1) succeeds at `T`,
   with each object's:
   - **Envelope** as recorded at production, including its
     `governance_at_production` and `confidence_at_production`
     stamps.
   - **`governance_at_anchor`** recomputed under §3.3 from the FR
     registry and `GovernanceTransition` history restricted to events
     `<= T`.
   - **`confidence_at_anchor`** recomputed under §3.4 from the
     then-admissible dependency set, then-current ceilings, then-
     applicable downgrade triggers, and `ConfidenceAssessment`
     records active at `T`.
   - **Schema-dispatch context** — each input read under the schema
     in force at its `produced_at`.
   - **Repair status** — if `T` precedes a repair, the pre-repair
     object is returned with `known_incorrect_at_present = true`. If
     `T` follows a repair, the repaired object is returned with
     `chain_repair_inherited = true`.

2. The MCP MUST NOT consume any artifact whose existence post-dates
   `T` in any reading of "post-dates" (production, indexing, supersession
   `effective_at`, transition `transition_at`, assessment `assessed_date`,
   schema publication).

3. The MCP MUST tag the response with the appropriate `truth_mode`
   (`CANONICAL`, `RECONSTRUCTED`, or — only if explicitly requested —
   `HYBRID`).

4. The MCP MUST be able to **prove** the correctness of (1)–(3) by
   producing, on demand, a `ReplayRun` artifact that re-executes the
   query against the registry and confirms either byte-identical
   results (`IDENTICAL`) or a documented divergence (`DIVERGENT`) with
   cause.

This is the operative definition of "what Caerus believed at T". Any
other reading — any reading that admits post-`T` information, applies
present semantics to past objects, or treats reconstruction as
canonical — is non-conformant and MUST be refused.

---

## 13. Acceptance Criteria for Replay Correctness

A conformant MCP MUST pass the following acceptance tests.

### 13.1 Deterministic Chain

Given a deterministic object `O` produced at `T_O` with deterministic
parents traceable to raw data:

- A replay at any `T >= T_O` produces a byte-identical envelope
  (modulo SEM-003 §3.4 encoding).
- The `ReplayRun.data.replay_result = "IDENTICAL"`.
- The `ReplayRun.data.replay_quality = "GUARANTEED"`.

### 13.2 Non-Deterministic with Recorded State

Given a non-deterministic object `O` with intact `source_state_hash`:

- A replay at any `T >= T_O` produces a byte-identical envelope.
- The `ReplayRun.data.replay_result = "IDENTICAL"`.
- The `ReplayRun.data.replay_quality = "GUARANTEED"`.

### 13.3 Repair Boundary

For a repaired segment with repair `effective_at = T_R`:

- Query as-of `T_anchor < T_R`: returns pre-repair object with
  `known_incorrect_at_present = true`.
- Query as-of `T_anchor >= T_R`: returns repaired object with
  `chain_repair_inherited = true`.

### 13.4 Rollback Boundary

For an FR rolled back at `T_R`:

- Query as-of `T_anchor < T_R`: artifacts return with pre-rollback
  `governance.state`.
- Query as-of `T_anchor >= T_R`: artifacts return with post-rollback
  `governance.state` recomputed under the rollback's effects.

### 13.5 Schema Migration Boundary

For a migrated migration from `vA` to `vB` at cutover `T_C`:

- Replay at `T_anchor < T_C`: inputs read under `vA`.
- Replay at `T_anchor >= T_C`: inputs read under `vB`, with reverse-
  migration available for any historical comparison.

### 13.6 Future-Information Refusal

Query "what was the regime on 2026-04-15?" filters all
`RegimeAssessment` objects to those with `as_of <= 2026-04-15` (US/
Eastern close, translated to UTC). A regime classifier update on
2026-05-01 MUST be fenced out.

### 13.7 Unreplayable Reporting

If a producer module's release tag is unavailable, a replay request for
an object built with that producer returns
`replay_result: "UNREPLAYABLE"`, with `unreachable_components` naming
the missing tag. The MCP MUST NOT substitute a current-version
producer.

### 13.8 Hybrid Refusal Without Opt-In

A query that lacks an explicit `reconstruction_mode = "HYBRID"` flag
MUST default to strict point-in-time (`CANONICAL` or `RECONSTRUCTED`).
A response tagged `HYBRID` without explicit opt-in is non-conformant.

### 13.9 Reconstruction Pollution Detection

The audit subsystem replays a query whose served response is suspected
of containing post-anchor information. If divergence is detected, the
audit MUST emit `RECONSTRUCTION_POLLUTION` finding (severity `CRITICAL`).

---

## 14. Failure Mode Discipline

When the MCP cannot satisfy a replay or reconstruction guarantee, the
correct behaviour is **transparent refusal**, not silent degradation.

| Situation | Required Response |
|---|---|
| Required input missing. | `UNREPLAYABLE` with named component. |
| Producer version unreachable. | `UNREPLAYABLE` with named tag/image. |
| Non-deterministic state missing. | `UNREPLAYABLE`; cite missing `source_state_hash`. |
| Cross-MAJOR schema gap without migration. | Refuse the query; raise `SCHEMA_INCOMPATIBLE_MAJOR`. |
| Hybrid requested but not authorised by caller. | Refuse; require explicit opt-in. |
| Future anchor requested. | Refuse; `T_anchor` MUST NOT be future. |
| Out-of-band knowledge tag without `AuditFinding`. | Refuse; require governing finding. |
| Repaired object requested as canonical at present. | Serve the repaired object; if pre-repair canonical is requested explicitly at an anchor, serve that anchor's canonical with appropriate annotation. |

In every case, the MCP returns a structured refusal — not a
synthesised, plausible-looking, silently-degraded response. **Refusal
is conformance.**

---

## 15. Relationship to Underlying Specifications

This document freezes and consolidates:

| Source | What Is Frozen Here |
|---|---|
| SEM-003 §7 | Replay guarantees as a corpus invariant. |
| SEM-006 (whole) | Point-in-time reconstruction mechanics. |
| SEM-006 §4.3 | The CANONICAL / RECONSTRUCTED / HYBRID trichotomy. |
| SEM-006 §7 | Repaired-history honesty rule. |
| SEM-006 §8 | Future-information exclusion. |
| SEM-007 §6.5 | Point-in-time application of `ConfidenceAssessment`. |
| SEM-004 §6.3 | Rollback replay. |
| SEM-008 §8 | Migration-stability constraints on replay. |

If a conflict arises between this document and the underlying spec,
the underlying spec governs and this document is corrected via errata.

---

## 16. Enforcement Surface

| Component | Enforcement |
|---|---|
| Retrieval layer | Apply admissibility filter at query-rewrite time; tag responses with truth mode; refuse hybrid without opt-in. |
| Replay subsystem | Produce `ReplayRun` artifacts; classify quality; detect and report divergence. |
| Audit subsystem | Run reconstruction-pollution detection; emit `RECONSTRUCTION_POLLUTION` findings; track replay coverage trajectory. |
| Schema registry | Maintain version-aware dispatch; enforce migration-stability test. |
| Governance subsystem | Maintain `GovernanceTransition` history; expose then-current state queries. |

---

## 17. Errata

*(none at v1)*

---

*SEM-REPLAY-v1 — 2026-05-21. Caerus Semantic Contract Layer.*
*Owner: Architecture / Research Infrastructure.*
*Classification: Institutional — Temporal Honesty Contract.*
