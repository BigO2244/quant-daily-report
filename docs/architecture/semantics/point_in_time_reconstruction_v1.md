---
last_reviewed: 2026-05-21
owner: architecture
category: architecture
criticality: critical
canonical: true
related_systems: [research, governance, attribution, regime, mcp]
spec_id: SEM-006
spec_version: v1
supersedes: null
---

# Specification 6 — Point-in-Time Reconstruction Semantics v1

**Spec ID:** SEM-006
**Version:** v1
**Date:** 2026-05-21
**Status:** Canonical
**Normative Language:** RFC 2119

---

## 1. Scope

The defining institutional question for Caerus is:

> **"What exactly did Caerus believe to be true at time T, on the basis of
> what evidence then available, under what governance then in force?"**

This specification defines the semantics that make that question
answerable. It is the temporal-honesty contract.

It covers:

- Temporal fencing — which artifacts MAY participate in an "as-of" view.
- As-of reconstruction — the mechanics of point-in-time queries.
- Historical replay guarantees — what is and is not promised.
- Future-information exclusion — the prohibition on look-ahead.
- Repaired-history handling — distinguishing canonical from reconstructed.

This specification rests on the provenance contract (Spec 3) and the
governance semantics (Spec 4). It is the temporal-axis projection of
those contracts.

---

## 2. Temporal Anchor (`T_anchor`)

A **temporal anchor** is the ISO-8601 UTC instant `T_anchor` against
which a reconstruction is computed. Every point-in-time query MUST
declare a `T_anchor`.

A `T_anchor` MUST be:

- A UTC instant, ISO-8601 with explicit `Z` or `+00:00`.
- Not in the future relative to the query execution time. (Future
  anchors are forbidden — there is no projection semantics in this spec.)
- Sufficient precision to disambiguate intra-second events; the
  canonical resolution is one second.

The MCP MUST refuse queries with malformed or future anchors.

### 2.1 Trade-Date vs. Instant Anchor

A `T_anchor` is an **instant**. The market concept of *trade date* is a
calendar date. The MCP supports both:

- Instant anchor: `as_of <= T_anchor` is the inclusion test.
- Trade-date anchor: a convenience meaning "the close of trading on date
  D, US/Eastern". The MCP translates trade-date anchors to instants
  (e.g., `D + 1` 00:00 UTC for next-session anchoring) and MUST declare
  the translation in the query result envelope under
  `annotations.anchor_resolution`.

---

## 3. Temporal Fencing

**Temporal fencing** is the rule that a point-in-time reconstruction MUST
NOT consume any object whose existence post-dates `T_anchor`.

### 3.1 Inclusion Rule

An object `O` is **admissible** for an as-of-`T_anchor` view if and only if:

- `O.temporal.as_of <= T_anchor`, AND
- `O.provenance.produced_at <= T_anchor`, AND
- `O` was not superseded at `T_anchor` (see §6), AND
- `O` was not invalidated at `T_anchor` (see §7).

Objects that fail any of these tests are **fenced out** of the view.

### 3.2 Recursive Fencing

Temporal fencing applies recursively along the provenance DAG. An
admissible object's payload may have been re-derived from inputs that
themselves were re-derived later. For point-in-time correctness, the
MCP MUST reconstruct the object from its **then-available** inputs,
using the producer module's then-current source-controlled version.

If recursive fencing is not feasible (e.g., the producer module's
historical version is not reachable), the query MUST be marked
`UNREPLAYABLE` for the fenced segment and the response MUST cite the
unreachable component.

### 3.3 Fencing for Governance

Governance state at `T_anchor` MUST be reconstructed from the FR registry
and `GovernanceTransition` history (Spec 4 §4.1) restricted to events
with `transition_at <= T_anchor`. The MCP MUST NOT apply current
governance to past objects when answering point-in-time queries.

A point-in-time query MUST be able to distinguish:

- The object's **stamped** governance state at production (recorded on
  the envelope).
- The object's **then-current** governance state at `T_anchor`
  (re-computed from then-current FR set).

In practice these are usually identical. They MAY differ if governance
inheritance was re-evaluated after production. Both MUST be available
under `data.governance_at_production` and `data.governance_at_anchor`.

### 3.4 Fencing for Confidence

Confidence at `T_anchor` MUST be reconstructed from the lattice rules
(Spec 7) applied to the then-admissible dependency set. The MCP MUST NOT
project current confidence onto past objects.

---

## 4. As-Of Reconstruction

An **as-of reconstruction** of an object `O` at `T_anchor` is the object
that would have been produced if `O` had been computed at `T_anchor`.

### 4.1 Reconstruction Resolution Order

For an object `O` requested as-of `T_anchor`:

1. Locate all versions of `(O.object_type, O.identity)` with
   `as_of <= T_anchor`.
2. Among those, select the non-superseded version at `T_anchor`. (Use the
   `SUPERSEDES` edges valid at `T_anchor`.)
3. If a selection exists, return it. Apply §3.3 and §3.4 to compute
   governance-at-anchor and confidence-at-anchor.
4. If no selection exists but `O` could be reproduced from inputs
   admissible at `T_anchor`, the reconstruction MAY be a **derived
   reconstruction** (see §4.2).
5. If no selection exists and derivation is not feasible, return
   `OBJECT_NOT_PRESENT_AT_ANCHOR`.

### 4.2 Derived Reconstruction

A derived reconstruction MUST satisfy:

- All inputs are admissible (§3.1).
- The producing module at the source-controlled version then in force
  is reachable.
- The derivation is deterministic (Spec 3 §5.1). Non-deterministic
  derivations cannot be derivedly reconstructed.

A derived reconstruction MUST be returned wrapped in a
`ReconstructedView` envelope marker:

```json
"annotations.reconstruction": {
  "kind": "DERIVED",
  "anchor": "<T_anchor>",
  "produced_by_module_version": "<git_sha_or_release_tag>",
  "input_object_ids": ["..."]
}
```

A derived reconstruction MUST NOT be persisted as a new object in the
corpus. It is a view, not a record.

### 4.3 Canonical vs. Reconstructed Truth

Caerus distinguishes two truth modes:

| Mode | Definition |
|---|---|
| **Canonical truth** | The object as recorded at the time of production, retrieved from the corpus unchanged. |
| **Reconstructed truth** | An as-of view computed from inputs, presenting "what would have been produced at T_anchor". |

The MCP MUST distinguish them in every response. Tools MUST tag
returns with `annotations.truth_mode = "CANONICAL" | "RECONSTRUCTED"`.

Mixing modes silently in a single response is forbidden.

---

## 5. Historical Replay Guarantees

Replay is the operational verification of point-in-time correctness.

### 5.1 Guaranteed Replays

The corpus MUST guarantee replay for:

1. **Any object `O` with `deterministic = true`** whose entire upstream
   chain is also deterministic, when all inputs are individually
   replayable.
2. **Any object `O` with `deterministic = false`** when its
   `source_state_hash` is intact and the producer module version is
   reachable.

### 5.2 Best-Effort Replays

The corpus provides best-effort replay (no strict guarantee) for:

- Objects whose external-resource dependencies (Spec 3 §5.4) have
  immutable snapshots but whose producers have been refactored
  semantically.
- Objects from grandfathered (pre-spec) eras (Spec 1 §11).

Best-effort replays MUST be annotated `replay_quality: "BEST_EFFORT"`
and MUST cite reasons.

### 5.3 Unreplayable States

A state is **unreplayable** when:

- A required input is missing from the corpus and not recoverable.
- A producer module's then-current version is unreachable.
- A non-deterministic derivation's `source_state_hash` is absent.

Unreplayable states MUST be reported as `replay_result: "UNREPLAYABLE"`
with the specific unreachable component(s) identified. They MUST NOT be
silently substituted with current-state results.

### 5.4 Replay Stability Guarantees

For any object `O` with two replays at `T1` and `T2` (both ≥ `O.as_of`),
the corpus MUST guarantee:

- If `O` is deterministic and all inputs are unchanged between `T1` and
  `T2`, the two replays MUST be byte-identical (modulo Spec 3 §3.4
  encoding differences).
- If anything in the upstream chain has changed (including supersession
  events), the second replay MUST EITHER produce a byte-identical result
  by faithfully reapplying then-current inputs, OR be marked
  `replay_result: "DIVERGENT"` with a divergence summary.

Divergence is permitted only when the divergence itself is a recorded
event — not silent drift. See Spec 3 §7.

### 5.5 Replay Audit (`ReplayRun`)

Every replay MUST produce a `ReplayRun` envelope per Spec 3 §7.4.
`ReplayRun` objects form the auditable history of "we have actually
verified that this object replays."

The MCP MUST be able to answer: "When was object `O` last successfully
replayed?" via `ReplayRun` history queried by target object id.

---

## 6. Supersession in Point-in-Time Queries

A `SUPERSEDES` edge (Spec 3 §8) carries an `effective_at` timestamp —
the instant at which the supersession takes effect.

For a query as-of `T_anchor`:

- An object that was superseded **before** `T_anchor` MUST NOT be
  returned as canonical for its identity.
- An object that was superseded **after** `T_anchor` MUST be returned
  as canonical (the supersession had not occurred at the anchor).
- If multiple supersession edges form a chain
  (`O1 ← O2 ← O3`, each superseding the prior), the canonical at
  `T_anchor` is the latest member of the chain with
  `effective_at <= T_anchor`.

`effective_at` defaults to the new object's `as_of` if not separately
recorded. Producers SHOULD record `effective_at` explicitly for
supersessions that take effect on a delayed schedule.

---

## 7. Repaired-History Handling

A **repair** is a corrective re-production of a prior segment of the
corpus (Spec 2 §5.1, Spec 3 §6).

### 7.1 Repair Semantics

A repair MUST NOT erase the prior segment. The prior segment persists
in the corpus with `is_superseded = true` and an `is_invalidated = true`
flag where appropriate.

A point-in-time query as-of `T_anchor` returns:

- For `T_anchor` **before** the repair's `effective_at`: the prior
  (incorrect-then-but-canonical-at-time) version. The response MUST
  carry `annotations.known_incorrect_at_present = true` with a
  reference to the repair.
- For `T_anchor` **after** the repair's `effective_at`: the repaired
  (corrected) version. The response MUST carry
  `annotations.chain_repair_inherited = true` per Spec 2 §5.1.

### 7.2 The Honesty Rule

The MCP MUST surface, at every point-in-time query, whether the returned
object is:

- **Believed-correct at anchor and believed-correct now.** No annotation
  needed.
- **Believed-correct at anchor but known-incorrect now** (returned for
  historical fidelity). Annotation: `known_incorrect_at_present`.
- **Repaired** (returned because the query post-dates a repair).
  Annotation: `chain_repair_inherited`.

The user of a point-in-time query MUST be able to determine which mode
applies without inspecting source code.

### 7.3 The Reconstruction-Pollution Prohibition

When reconstructing object `O` as-of `T_anchor`, the MCP MUST NOT use
any artifact produced after `T_anchor`, including:

- Repaired versions of `O` or its inputs whose `effective_at > T_anchor`.
- Governance transitions with `transition_at > T_anchor`.
- Confidence reassessments with `assessed_at > T_anchor`.
- Schema migrations completed after `T_anchor`.

Reconstruction MUST proceed from the then-available inputs. If
reconstruction at `T_anchor` is required for analysis (e.g., "what would
the attribution have said at `T_anchor` given inputs known at that
time?"), and the relevant inputs were later repaired, the reconstruction
MUST use the **pre-repair** inputs — even though they are known-incorrect
today. This is the point-in-time honesty rule.

A consumer that wants "what we would compute today using inputs as they
stood at `T_anchor`" is asking a different question. That mode is
`HYBRID_RECONSTRUCTION` and is explicitly supported only via:

- An explicit caller flag `reconstruction_mode = "HYBRID"`.
- Stamped annotation `annotations.reconstruction.kind = "HYBRID"`.
- Full disclosure of which components are at-anchor vs. at-present.

The default MUST be strict point-in-time. Hybrid is opt-in and clearly
labelled.

---

## 8. Future-Information Exclusion

It is **forbidden** to use any artifact in an as-of-`T_anchor` view if
that artifact:

- Has `produced_at > T_anchor`, OR
- Has `as_of > T_anchor`, OR
- Was unreachable / non-existent at `T_anchor` even if it would have
  been admissible by timestamp (e.g., the artifact existed but was not
  yet visible to the MCP's then-current ingestion).

The third condition is the **discoverability** requirement: temporal
fencing means fencing to the artifact's *visibility* at `T_anchor`, not
merely its existence.

In practice, the corpus tracks artifact discoverability via the
`indexed_at` timestamp in the registry (architecture §10). An artifact's
admissibility at `T_anchor` is `min(as_of, indexed_at) <= T_anchor`.

### 8.1 Exception: Out-of-Band Operator Knowledge

A query MAY be tagged `operator_was_aware = true` to indicate that an
operator at `T_anchor` had out-of-band knowledge not yet reflected in
the corpus (e.g., a phone call from the broker before the artifact
landed). Such tags are non-conformant by default and MUST be explicitly
documented in an `AuditFinding`. Future-information exclusion is not
casually overridden.

---

## 9. Time Zone Discipline

All instants in this specification are UTC. The corpus uses calendar
trade dates in US/Eastern for current Caerus strategies.

The MCP MUST:

- Stamp every instant in UTC.
- Translate trade-date anchors to UTC instants using a single canonical
  calendar (US/Eastern + NYSE holiday calendar).
- Refuse queries whose timezone is ambiguous.

Spec-internal date arithmetic uses ISO weekday and ISO calendar.
Holiday calendars are sourced from a versioned market-calendar artifact
under separate FR governance.

---

## 10. Examples

### 10.1 Simple As-Of Query

Query: "Show the attribution for Polaris on 2026-04-30, as of
2026-04-30T22:00:00Z."

Procedure:

1. Locate all `attribution_run` objects for
   `strategy_ref = caerus_polaris`, `trade_date = 2026-04-30`.
2. Filter to those with `as_of <= 2026-04-30T22:00:00Z`.
3. Apply supersession resolution at the anchor.
4. Compute governance-at-anchor (state of governing FRs at the anchor).
5. Compute confidence-at-anchor (lattice with then-admissible deps).
6. Return with `annotations.truth_mode = "CANONICAL"`.

### 10.2 Reconstruction After Repair

The Polaris attribution for 2026-04-15 was repaired on 2026-05-10 due
to a corrected broker snapshot.

- Query as-of `2026-04-20T00:00:00Z`: returns the **pre-repair** object.
  `annotations.known_incorrect_at_present = true`,
  `annotations.repair_reference = "<rollback_object_id>"`.
- Query as-of `2026-05-15T00:00:00Z`: returns the **repaired** object.
  `annotations.chain_repair_inherited = true`.
- Default (no anchor specified): returns the repaired object (current
  canonical). `annotations.truth_mode = "CANONICAL"`.

### 10.3 Unreplayable Segment

Query: "Replay Orion's shadow NAV chain for 2026-01-01 to 2026-01-31."

The producing module's January version is no longer reachable (release
tag was deleted). The MCP returns:

```json
{
  "replay_quality": "UNREPLAYABLE",
  "unreachable_components": ["scripts/research/build_shadow_nav.py@2026-01-tag"],
  "available_canonical_objects": [...],
  "guidance": "Canonical artifacts are returned. Replay-based verification is not available for this segment."
}
```

This is **temporal honesty**: refuse to fabricate a replay; admit the
unreplayability.

### 10.4 Future Information Refusal

Query: "What was the regime on 2026-04-15?"

The MCP filters all `RegimeAssessment` objects to those with
`as_of <= 2026-04-15T23:59:59Z (US/Eastern close translated to UTC)`.
A later regime classifier update on 2026-05-01 is fenced out. The
returned regime is the one Caerus believed at the time, not the one
Caerus would compute today.

---

## 11. Enforcement Surface

| Component | Enforcement |
|---|---|
| MCP retrieval layer | Apply temporal fencing on every as-of query; refuse to consume future-dated inputs. |
| MCP cognition layer | Distinguish `CANONICAL` vs. `RECONSTRUCTED` vs. `HYBRID` truth modes; tag responses. |
| Replay subsystem | Produce `ReplayRun` records; detect and report divergence. |
| Registry | Track `indexed_at` and `effective_at` for discoverability and supersession resolution. |
| Audit subsystem | Emit `RECONSTRUCTION_POLLUTION` findings if temporal fencing violations are detected post-hoc. |

---

## 12. Errata

*(none at v1)*

---

*SEM-006 v1 — 2026-05-21. See SEM-INDEX (`README.md`).*
