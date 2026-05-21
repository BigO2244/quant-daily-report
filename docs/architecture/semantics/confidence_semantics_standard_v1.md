---
last_reviewed: 2026-05-21
owner: architecture
category: architecture
criticality: critical
canonical: true
related_systems: [research, governance, attribution, shadow, mcp]
spec_id: SEM-007
spec_version: v1
supersedes: null
---

# Specification 7 — Confidence Semantics Standard v1

**Spec ID:** SEM-007
**Version:** v1
**Date:** 2026-05-21
**Status:** Canonical
**Normative Language:** RFC 2119

---

## 1. Scope

Confidence is the institutional admission of the limits of what we know.
It is not a free-text label or a marketing decoration. It is a
lattice-ordered statement about how much weight a downstream decision
may place on an artifact.

This specification defines:

- The closed confidence lattice.
- Propagation rules along the provenance DAG.
- Downgrade triggers (conditions that force lower confidence).
- Reassessment rules (the only way confidence increases).
- Invalidation semantics.

It is binding on every `confidence.level` field in the corpus.

---

## 2. The Confidence Lattice

The confidence lattice is closed. Five values, totally ordered, no
synonyms, no additions:

```
BROKER_AUTHORITATIVE  >  HIGH  >  PARTIAL_CONFIDENCE  >  LOW  >  UNAVAILABLE
```

| Value | Meaning |
|---|---|
| `BROKER_AUTHORITATIVE` | Direct from broker; the institutional ground truth for executed state. Reserved for `LIVE_BROKER_PAPER_NAV` and downstream artifacts that materially depend on no other surface. |
| `HIGH` | Validated through multiple independent checks; no open downgrade triggers; deterministic provenance. |
| `PARTIAL_CONFIDENCE` | Known limitations documented; the artifact is suitable for research but not for primary operational decisions. |
| `LOW` | Missing validation, broken chain, governance gap, or known integrity issues. Suitable as context, not as evidence. |
| `UNAVAILABLE` | Data source unreachable, absent, or unassessable. The artifact's existence is acknowledged; its content is not trusted. |

### 2.1 Lattice Algebra

For confidence values `a, b`:

- `min(a, b)` returns the lower of the two (lattice meet).
- `max(a, b)` returns the higher of the two (lattice join).
- Comparison `<`, `<=`, `>`, `>=` is well-defined.

The lattice has no values outside this enumeration. `UNKNOWN`, `MEDIUM`,
`UNVERIFIED`, etc. are non-conformant — use the canonical value that
applies.

### 2.2 Confidence Is Not Probability

The lattice is **not** a probability distribution. It is a discrete
trust ordering. Mapping between confidence and probability is
intentionally not specified — different downstream consumers may apply
different policies (e.g., "treat LOW as 0 weight"), but no global
mapping is canonical.

---

## 3. Confidence Stamping

Every research object MUST carry `confidence.level` (Spec 1 §3). The
stamped value MUST be the result of applying:

```
stamped = min(
  surface_ceiling,           # Spec 2 §2.2
  governance_ceiling,        # Spec 4 §4
  applicable_downgrade_floor,
  min(parent.confidence)     # propagation floor, §4
)
```

If the producer cannot evaluate all four components, it MUST refuse to
emit the object. Conformance prohibits guessing.

---

## 4. Propagation Rules

### 4.1 Floor Propagation (Rule 1)

When object `C` depends on parents `P1, P2, ..., Pk`:

```
confidence(C) <= min(confidence(P1), ..., confidence(Pk))
```

A child's confidence MUST NOT exceed the minimum of its material
parents' confidences (Spec 4 §3.4 defines "material").

### 4.2 Context Parents Exception

A parent declared `context` (not `material`) in
`provenance.materiality_map` (Spec 4 §3.4) does NOT participate in the
floor. Use of `context` declarations carries documentation burden: the
producer MUST justify, in `provenance.transformation` or an attached
finding, why the parent is non-material.

### 4.3 Surface Ceiling (Rule 2)

A child's confidence MUST NOT exceed the surface confidence ceiling
(Spec 2 §2.2) of its declared `surface.nav_surface_type`. The surface
ceiling is independent of parent confidences — it is a property of the
surface itself.

### 4.4 Governance Ceiling (Rule 3)

A child's confidence MUST NOT exceed the governance ceiling implied by
its `governance.state` (Spec 4 §4):

- `GOVERNED_OBSERVING` → ceiling `PARTIAL_CONFIDENCE`.
- `GOVERNED_DRAFT` → ceiling `PARTIAL_CONFIDENCE`.
- `UNGOVERNED` (critical artifacts) → ceiling `LOW`.
- `GOVERNED_DEFERRED`, `GOVERNED_DEPLOYED` → no governance-derived
  ceiling beyond surface/propagation.

"Critical artifact" means any object whose `object_type` produces a
numeric performance result (NAVSurface, AttributionRun, PortfolioSnapshot,
ValidationRun) or directly gates promotion (PromotionAssessment,
StabilityAssessment, FragilityAssessment).

### 4.5 Propagation Transparency

Every stamped `confidence.level` MUST be accompanied by:

- `confidence.rationale` — a short non-empty string.
- `confidence.limiting_dependency` — the `object_id` of the parent (or
  null if the limit comes from ceiling/downgrade, not propagation) whose
  confidence determined the floor.
- `confidence.downgrade_reasons` — list of zero or more downgrade codes
  (§5).

The MCP MUST be able to report the full **confidence chain**: which
component (surface, governance, propagation, or downgrade) was the
binding constraint, with the limiting object or rule named.

---

## 5. Downgrade Triggers

The following conditions force confidence downgrade. Triggers MUST be
applied at stamping; producers MUST NOT emit objects that violate them.

| Code | Condition | Effect |
|---|---|---|
| `CHAIN_NO_PRIOR` | `surface.chain_status = NO_PRIOR` | Floor at `LOW`. |
| `CHAIN_BROKEN` | `surface.chain_status = BROKEN_CHAIN` | Floor at `LOW`. |
| `CHAIN_REPAIRED` | `surface.chain_status = REPAIRED` | Floor at `PARTIAL_CONFIDENCE`. |
| `BROKER_MISSING` | Broker snapshot missing or equity unavailable when required | Floor at `UNAVAILABLE`. |
| `BACKTEST_SYNTHETIC` | `surface.execution_realism = MODEL_CLOSE_WITH_SYNTHETIC_COSTS` | Floor at `PARTIAL_CONFIDENCE`. |
| `STALE` | `temporal.is_stale = true` at evaluation | Downgrade by one lattice level. |
| `GOV_OBSERVING` | `governance.state = GOVERNED_OBSERVING` | Floor at `PARTIAL_CONFIDENCE`. |
| `GOV_UNGOVERNED_CRITICAL` | `governance.state = UNGOVERNED` AND object is critical (§4.4) | Floor at `LOW`. |
| `NON_DETERMINISTIC` | `provenance.deterministic = false` | Floor at `PARTIAL_CONFIDENCE`. |
| `INPUT_INVALIDATED` | Any material parent is invalidated (Spec 3 §6) | Floor at `LOW`; also stamp `annotations.provenance_invalidated = true`. |
| `SCHEMA_DEPRECATED` | `schema.schema_version` is in deprecated status (Spec 8) | Downgrade by one lattice level. |
| `SCHEMA_UNKNOWN` | `schema.schema_version` not in the publication registry | Floor at `UNAVAILABLE` (object also fails validation). |
| `RECONSTRUCTION_HYBRID` | `annotations.reconstruction.kind = "HYBRID"` (Spec 6 §7.3) | Floor at `PARTIAL_CONFIDENCE`. |
| `SURFACE_OVERRIDE` | `annotations.surface_override` present (Spec 2 §3.2) | Floor at `LOW`. |

### 5.1 Multiple Triggers

When multiple triggers apply, the **lowest** resulting floor wins
(strict lattice meet). The full set of triggered codes MUST be recorded
in `confidence.downgrade_reasons`. Hiding any applicable code is
non-conformant.

### 5.2 Trigger Effect Order

Triggers are applied **after** propagation floor and ceiling computation
(§3). The final stamped confidence is:

```
final = min(
  propagation_floor,
  surface_ceiling,
  governance_ceiling,
  trigger_floor_meet
)
```

If `final` differs from the producer's naive expectation, the producer
MUST stamp `final`. Confidence is not optional and not negotiable.

---

## 6. Reassessment Rules

Confidence MAY increase only through **explicit reassessment**, never
implicitly.

### 6.1 ConfidenceAssessment Object

A reassessment is recorded as a `ConfidenceAssessment` object (ontology
type per architecture §2) with:

- `assesses` — `object_id` of the object whose confidence is being
  reassessed.
- `assessed_confidence` — the new confidence level.
- `contributing_factors` — list of factors justifying the upgrade.
- `downgrade_reasons` — list of any remaining open downgrade codes
  (which limit how high the new assessment may go).
- `assessed_date` — timestamp.
- `temporal_validity` — the validity window of the assessment.
- `assessor` — operator, audit process, or governance FR that authorises
  the upgrade.

### 6.2 Constraints on Upgrade

An upgrade reassessment MUST satisfy:

1. The new confidence MUST NOT exceed any applicable ceiling
   (surface, governance, dependency floor) at assessment time.
2. The reassessment MUST close (or explicitly waive) every downgrade
   code that was open at the prior assessment.
3. The reassessment MUST be governed: an `assessor` field referencing
   either an operator with `AuditFinding` backing or a specific
   `GovernanceFR` is required.
4. The reassessment MUST NOT be retroactive across `as_of` boundaries
   without explicit declaration. By default, a reassessment applies
   prospectively from `assessed_date` forward.

### 6.3 Constraints on Downgrade

Downgrade is automatic when triggers fire; no `ConfidenceAssessment` is
required for downgrade caused by trigger application. However, a
deliberate downgrade outside of trigger semantics (e.g., operator
judgment that an artifact is less trustworthy than triggers indicate)
MUST be recorded as a `ConfidenceAssessment` for auditability.

### 6.4 No Silent Upgrades

The MCP MUST refuse to serve a confidence level that exceeds the
trigger-implied floor unless a covering `ConfidenceAssessment` is
present, valid, and within its `temporal_validity` window.

### 6.5 Reassessment Lifecycle

A `ConfidenceAssessment` is itself a research object with its own
envelope. Reassessments accrue over time. Point-in-time queries (Spec 6)
MUST apply only the `ConfidenceAssessment` records active at
`T_anchor`.

---

## 7. Invalidation Semantics

Confidence MAY be invalidated entirely (set to `UNAVAILABLE`) by:

- Discovery of a critical flaw in the artifact's data or methodology.
- Rollback of the governing FR (Spec 4 §6).
- Migration that breaks the artifact's schema irrecoverably.
- Identification of a contaminating input (e.g., an upstream artifact
  that used future information).

Invalidation MUST be recorded as an `AuditFinding` of severity
`CRITICAL`. Invalidation propagates downstream per Spec 3 §6. Invalidated
objects MUST carry `confidence.level = UNAVAILABLE` and
`annotations.invalidation_finding_ref`.

Invalidation MUST NOT delete the object. The temporal honesty rule
(Spec 6 §7) requires that historical states remain queryable in their
then-believed form.

---

## 8. Confidence Reporting Discipline

Every MCP response that reports a confidence value MUST also report:

- The chain of contributing components (surface, governance,
  propagation source, triggers).
- The limiting component.
- The set of triggered downgrade codes.
- Any active `ConfidenceAssessment` references.

A response that reports `confidence.level` alone, without the chain, is
non-conformant.

---

## 9. Cross-Object Confidence

For queries that synthesise across multiple objects (e.g., "what is the
effective confidence of this promotion assessment?"), the synthesised
confidence is:

```
synth = min(confidence(o) for o in material_inputs)
```

with downgrade triggers re-applied at the synthesis level (e.g., if the
synthesis itself is non-deterministic, `NON_DETERMINISTIC` fires).

The MCP MUST surface the synthesis chain in the response, not only the
final value.

---

## 10. Conformance Examples

### 10.1 Floor Propagation

```
NAVSurface (OPERATIONAL_SHADOW, chain_status=NO_PRIOR)
  surface_ceiling = PARTIAL_CONFIDENCE
  trigger: CHAIN_NO_PRIOR → floor LOW
  final: LOW
  rationale: "chain_status=NO_PRIOR"
  limiting_dependency: null  (limit from trigger, not parent)
  downgrade_reasons: [CHAIN_NO_PRIOR]
  ↓
AttributionRun (uses the NAVSurface above)
  parent_floor = LOW
  surface_ceiling = PARTIAL_CONFIDENCE
  governance_ceiling = none
  no triggers fire at this level
  final: LOW
  rationale: "inherited from NAVSurface chain status"
  limiting_dependency: "nav_surface__orion__2026-04-30__operational_shadow__v1.1.0"
  downgrade_reasons: []
```

### 10.2 Upgrade via Reassessment

A NAVSurface initially stamped `LOW` due to `CHAIN_NO_PRIOR`. After 20
trading sessions of `chain_status = OK`, an operator-authorised
`ConfidenceAssessment` records:

```json
{
  "object_type": "ConfidenceAssessment",
  "assesses": "nav_surface__orion__operational_shadow",
  "assessed_confidence": "PARTIAL_CONFIDENCE",
  "contributing_factors": ["20 consecutive sessions chain_status=OK", "FR-024 DEPLOYED"],
  "downgrade_reasons": [],
  "assessed_date": "2026-06-15T00:00:00Z",
  "temporal_validity": {"valid_from": "2026-06-15T00:00:00Z", "valid_to": null},
  "assessor": "FR-024 closure audit"
}
```

After this assessment, **new** NAVSurface objects on this surface for
this strategy may stamp `PARTIAL_CONFIDENCE`. The original `LOW`-stamped
objects remain `LOW` historically (temporal honesty); their confidence
at-anchor stays as it was.

### 10.3 Forbidden Silent Upgrade

A producer emits `confidence.level = HIGH` for a `OPERATIONAL_SHADOW_NAV`
artifact. This is non-conformant: surface ceiling is `PARTIAL_CONFIDENCE`.
The ingestion layer MUST refuse the object.

### 10.4 Trigger Stacking

A `RESEARCH_BACKTEST_NAV` object built non-deterministically (model
training with stochasticity), with stale dependencies, under
`UNGOVERNED` status (critical artifact):

```
triggers fired: BACKTEST_SYNTHETIC, NON_DETERMINISTIC, STALE, GOV_UNGOVERNED_CRITICAL
floors: PARTIAL_CONFIDENCE, PARTIAL_CONFIDENCE, (downgrade), LOW
final: LOW
downgrade_reasons: ["BACKTEST_SYNTHETIC", "NON_DETERMINISTIC", "STALE", "GOV_UNGOVERNED_CRITICAL"]
```

All four codes are surfaced. Hiding any of them would be non-conformant.

---

## 11. Enforcement Surface

| Component | Enforcement |
|---|---|
| Producers | Apply §3 stamping; refuse to emit objects whose confidence cannot be evaluated. |
| Ingestion layer | Validate every stamped confidence against §3..§5; refuse non-conformant objects. |
| Retrieval layer | Refuse to serve confidence levels that exceed floor without active `ConfidenceAssessment`. |
| Reassessment subsystem | Maintain `ConfidenceAssessment` records; expire them per `temporal_validity`. |
| Audit subsystem | Emit `CONFIDENCE_SILENT_INFLATION` findings when producers attempt prohibited upgrades. |

---

## 12. Errata

*(none at v1)*

---

*SEM-007 v1 — 2026-05-21. See SEM-INDEX (`README.md`).*
