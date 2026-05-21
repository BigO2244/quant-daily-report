---
last_reviewed: 2026-05-21
owner: architecture
category: architecture
criticality: critical
canonical: true
related_systems: [research, governance, attribution, regime, shadow, mcp]
spec_id: SEM-001
spec_version: v1
supersedes: null
---

# Specification 1 — Research Object Metadata Standard v1

**Spec ID:** SEM-001
**Version:** v1
**Date:** 2026-05-21
**Status:** Canonical
**Normative Language:** RFC 2119

---

## 1. Scope

This specification defines the **canonical metadata envelope** that every
research object in the Caerus corpus MUST carry. It is binding on:

- All objects defined in `caerus_research_mcp_architecture.md` §2
  (Strategy, NAVSurface, AttributionRun, ExposureSnapshot, RegimeAssessment,
  AuditFinding, GovernanceFR, PromotionAssessment, PortfolioSnapshot,
  ResearchHypothesis, ValidationRun, StabilityAssessment, FragilityAssessment,
  ConfidenceAssessment, ResearchArtifact, LineageNode, TemporalWindow).
- Every artifact produced by a Caerus research pipeline that is intended for
  institutional consumption, audit, or replay.
- Every MCP tool response (responses re-emit the envelope; they MUST NOT
  strip it).

This specification is **distinct from** `docs/documentation/metadata_standard.md`,
which governs Markdown documentation front-matter. The two coexist; neither
supersedes the other.

---

## 2. The Canonical Metadata Envelope

Every research object MUST be wrapped in a metadata envelope with the
following top-level structure:

```json
{
  "object_type": "<ObjectType>",
  "object_id": "<deterministic_id>",
  "schema": {
    "schema_id": "<schema_namespace>",
    "schema_version": "<semver>",
    "ontology_version": "<semver>"
  },
  "identity": {
    "strategy_ref": "<strategy_id | null>",
    "trade_date": "<YYYY-MM-DD | null>",
    "surface_ref": "<surface_id | null>"
  },
  "temporal": {
    "as_of": "<ISO-8601 UTC>",
    "trade_date": "<YYYY-MM-DD | null>",
    "valid_from": "<ISO-8601 UTC | null>",
    "valid_to": "<ISO-8601 UTC | null>",
    "staleness_threshold_seconds": <int | null>,
    "is_stale": <bool>
  },
  "provenance": {
    "produced_by": "<module_or_script>",
    "produced_at": "<ISO-8601 UTC>",
    "source_paths": ["<path>", "..."],
    "input_object_ids": ["<object_id>", "..."],
    "transformation": "<short_description>",
    "deterministic": <bool>,
    "source_state_hash": "<hex | null>"
  },
  "confidence": {
    "level": "<ConfidenceLevel>",
    "rationale": "<short_string>",
    "limiting_dependency": "<object_id | null>",
    "downgrade_reasons": ["<code>", "..."]
  },
  "governance": {
    "state": "<GovernanceState>",
    "governing_frs": ["<FR-id>", "..."],
    "coverage_type": "DIRECT | INHERITED | UNGOVERNED",
    "observation_status": "not_started | observing | satisfied | blocked | not_required"
  },
  "surface": {
    "nav_surface_type": "<SurfaceType | null>",
    "execution_realism": "<string | null>",
    "chain_status": "OK | NO_PRIOR | BROKEN_CHAIN | REPAIRED | NOT_APPLICABLE"
  },
  "lineage": {
    "node_id": "<lineage_node_id>",
    "parent_refs": ["<object_id>", "..."],
    "transformation_chain_hash": "<hex>"
  },
  "data": { /* payload — typed per object_type */ }
}
```

The envelope is the institutional interface. The `data` block is the
payload; the surrounding fields are the **non-negotiable surface**.

---

## 3. Required Fields

A research object envelope MUST contain every field below. Omission of any
required field renders the object non-conformant.

| Field | Type | Rule |
|---|---|---|
| `object_type` | enum | MUST be a value defined in the ontology (architecture §2). |
| `object_id` | string | MUST be deterministic — same inputs MUST produce same id (see §5). |
| `schema.schema_id` | string | MUST identify a registered schema namespace. |
| `schema.schema_version` | semver | MUST be a published version (see Spec 8). |
| `schema.ontology_version` | semver | MUST match a published ontology version (see Spec 5). |
| `temporal.as_of` | ISO-8601 UTC | MUST be the wall-clock instant the object was produced. |
| `provenance.produced_by` | string | MUST identify the module/script/agent that produced the object. |
| `provenance.produced_at` | ISO-8601 UTC | MUST equal or be earlier than `temporal.as_of`. |
| `provenance.deterministic` | bool | MUST be present; defaults are forbidden. |
| `confidence.level` | enum | MUST be a `ConfidenceLevel` from the lattice (Spec 7). |
| `confidence.rationale` | string | MUST be non-empty. |
| `governance.state` | enum | MUST be a `GovernanceState` value (Spec 4). |
| `governance.coverage_type` | enum | MUST be `DIRECT`, `INHERITED`, or `UNGOVERNED`. |
| `lineage.node_id` | string | MUST be present for every object. |
| `lineage.parent_refs` | list | MAY be empty (raw sources have no parents) but MUST be present. |

### 3.1 Conditional Requirements

| Condition | Additional Required Fields |
|---|---|
| `object_type` is dated (NAVSurface, AttributionRun, ExposureSnapshot, RegimeAssessment, PortfolioSnapshot, FragilityAssessment, ValidationRun, etc.) | `identity.trade_date`, `temporal.trade_date` |
| `object_type` is strategy-scoped | `identity.strategy_ref` |
| `object_type` involves a NAV calculation (NAVSurface, AttributionRun, PortfolioSnapshot, ValidationRun) | `surface.nav_surface_type`, `surface.execution_realism`, `surface.chain_status` |
| `provenance.deterministic = false` | `provenance.source_state_hash` MUST be present and identify the non-deterministic state (e.g., RNG seed, model checkpoint). |
| `governance.state` is `GOVERNED_*` | `governance.governing_frs` MUST be non-empty. |
| `governance.state` is `GOVERNED_OBSERVING` | `governance.observation_status` MUST NOT be `not_started`. |

---

## 4. Forbidden Omissions

The following omissions are forbidden under all circumstances:

1. **No nullable confidence.** `confidence.level` MUST NOT be `null`. If
   confidence cannot be assessed, the value MUST be `UNAVAILABLE` with a
   rationale. Absence is not assessable.

2. **No nullable governance.** `governance.state` MUST NOT be `null`. The
   value `UNGOVERNED` is explicit; absence is not.

3. **No unlabeled NAV-bearing numerics.** Any object whose payload reports a
   NAV, return, drawdown, Sharpe, or similar performance number MUST carry
   `surface.nav_surface_type` and `surface.execution_realism`. Performance
   numbers without surface labels are non-conformant and MUST be rejected by
   the MCP and by downstream consumers.

4. **No silent provenance loss.** A consumer MUST NOT strip envelope fields
   when re-emitting an object. Re-emission MUST preserve all envelope
   fields. If a transformation produces a *new* object, the new object
   carries its own envelope and references the prior object via
   `provenance.input_object_ids` and `lineage.parent_refs`.

5. **No undated dated objects.** If an object's type implies a trade date,
   that date MUST be present. Type and identity MUST agree.

6. **No untraceable derivation.** Every non-raw object MUST have at least
   one entry in `lineage.parent_refs`. Orphan derivatives are non-conformant.

---

## 5. Object Identity (Determinism Rule)

`object_id` MUST be deterministic: given the same `object_type`,
`identity` fields, `temporal.trade_date`, and `schema.schema_version`,
the same `object_id` MUST be produced by any conformant implementation.

The RECOMMENDED construction is:

```
object_id = "<object_type_snake>__<strategy_ref|_>__<trade_date|_>__<surface_ref|_>__<schema_version>"
```

Example: `attribution_run__caerus_polaris__2026-04-30__operational_shadow__v1.2.0`.

Implementations MAY choose a different deterministic scheme but MUST
publish it under `schema.schema_id` and MUST NOT vary identity across runs
without a versioned schema change.

`lineage.node_id` MUST be globally unique across the corpus and MUST NOT
collide between object types. The RECOMMENDED form is `<object_id>#node`.

---

## 6. Inheritance Rules

Metadata inherits from parents along the provenance graph (see Spec 3) with
the following floor rules:

| Field | Inheritance |
|---|---|
| `confidence.level` | Floor — child's confidence MUST NOT exceed `min(parent.confidence)` (see Spec 7). |
| `surface.nav_surface_type` | Pass-through — child's surface MUST match parent's surface unless the transformation is an explicit surface re-projection. |
| `surface.execution_realism` | Pass-through under same rule. |
| `governance.state` | Inherited if and only if `provenance.deterministic = true` AND parent's governance is `GOVERNED_*` (see Spec 4 §3). |
| `temporal.trade_date` | MUST match parent's `trade_date` if the derivation is intra-day; cross-date derivations MUST be declared explicitly in `provenance.transformation`. |
| `schema.ontology_version` | Inherited unless the transformation explicitly migrates ontology version (see Spec 5). |

Inheritance is computed at hydration time, not at production time. Producers
MUST set the field to the correct value at production; consumers MUST
re-verify inheritance during validation.

---

## 7. Temporal Rules

1. `temporal.as_of` MUST be a UTC instant in ISO-8601 with explicit `Z` or
   `+00:00`. Local-time stamps are non-conformant.

2. `temporal.as_of` MUST be monotonic with respect to wall-clock production
   order. A re-issued object (e.g., a re-run of attribution for the same
   `trade_date`) carries a later `as_of` and MUST link to the prior object
   via `provenance.input_object_ids` if it consumed any of the prior
   object's fields, otherwise via a `SUPERSEDES` lineage edge.

3. `temporal.trade_date` MUST be the canonical *business* date the object
   pertains to, in the market timezone (US/Eastern for current Caerus
   strategies). It is intentionally a calendar date, not an instant.

4. `temporal.valid_from` / `temporal.valid_to` are OPTIONAL. When present,
   they define an explicit validity window. When absent, the object is
   considered valid from `temporal.as_of` until superseded or stale.

5. `temporal.is_stale` MUST be computed at hydration time, not stamped at
   production time. Producers SHOULD set `staleness_threshold_seconds` to a
   value appropriate for the object type (see architecture §7); the
   `is_stale` boolean is a derived view.

---

## 8. Validation Requirements

A conformant validator MUST reject an envelope that violates any of the
following:

| Code | Check |
|---|---|
| `M001` | Required field missing. |
| `M002` | `object_type` not in ontology. |
| `M003` | `confidence.level` not in lattice. |
| `M004` | `governance.state` not in enumeration. |
| `M005` | `temporal.as_of` not UTC ISO-8601. |
| `M006` | `temporal.trade_date` missing for a dated object type. |
| `M007` | Performance number in payload without surface labels (forbidden omission §4.3). |
| `M008` | Non-raw object with empty `lineage.parent_refs`. |
| `M009` | Confidence higher than minimum parent confidence (floor violation; see Spec 7). |
| `M010` | Non-deterministic provenance without `source_state_hash`. |
| `M011` | `produced_at` later than `as_of`. |
| `M012` | `schema.schema_version` references an unpublished version. |
| `M013` | `governance.state = GOVERNED_*` with empty `governing_frs`. |

A validator MAY emit warnings (e.g., stale data, ungoverned non-critical
artifacts) but rejections are reserved for MUST violations.

The MCP retrieval layer MUST run validation on hydration and MUST refuse
to serve non-conformant objects. Non-conformant objects are surfaced as
findings, not as data.

---

## 9. Re-Emission Rules

When an MCP tool, downstream script, or human report re-emits an object:

1. The envelope MUST be preserved in full. Fields MAY be re-ordered for
   presentation but MUST NOT be omitted.

2. Annotation fields (added by the consumer) MUST be placed under a
   top-level `annotations` key, never inside the canonical envelope blocks.
   Example: `annotations.display_label`, `annotations.viewer_warnings`.

3. A consumer that filters payload fields (e.g., a tool returning only
   summary metrics) MUST set `data._partial = true` and MUST include
   `data._omitted_fields` listing the omitted payload keys. The envelope
   remains complete.

---

## 10. Examples

### 10.1 Conformant NAVSurface Envelope

```json
{
  "object_type": "NAVSurface",
  "object_id": "nav_surface__caerus_orion__2026-04-30__operational_shadow__v1.1.0",
  "schema": {
    "schema_id": "caerus.navsurface",
    "schema_version": "1.1.0",
    "ontology_version": "1.0.0"
  },
  "identity": {
    "strategy_ref": "caerus_orion",
    "trade_date": "2026-04-30",
    "surface_ref": "operational_shadow"
  },
  "temporal": {
    "as_of": "2026-04-30T20:15:00Z",
    "trade_date": "2026-04-30",
    "valid_from": null,
    "valid_to": null,
    "staleness_threshold_seconds": 86400,
    "is_stale": false
  },
  "provenance": {
    "produced_by": "scripts/research/build_shadow_nav.py",
    "produced_at": "2026-04-30T20:14:58Z",
    "source_paths": ["outputs/shadow_candidates/2026-04-30/orion.json"],
    "input_object_ids": ["portfolio_snapshot__caerus_orion__2026-04-30____v1.0.0"],
    "transformation": "shadow_nav_from_portfolio_snapshot",
    "deterministic": true,
    "source_state_hash": null
  },
  "confidence": {
    "level": "LOW",
    "rationale": "chain_status=NO_PRIOR; first session for strategy",
    "limiting_dependency": null,
    "downgrade_reasons": ["CHAIN_NO_PRIOR"]
  },
  "governance": {
    "state": "GOVERNED_DEPLOYED",
    "governing_frs": ["FR-024"],
    "coverage_type": "DIRECT",
    "observation_status": "not_required"
  },
  "surface": {
    "nav_surface_type": "OPERATIONAL_SHADOW_NAV",
    "execution_realism": "MODEL_PORTFOLIO_NO_BROKER_FILLS",
    "chain_status": "NO_PRIOR"
  },
  "lineage": {
    "node_id": "nav_surface__caerus_orion__2026-04-30__operational_shadow__v1.1.0#node",
    "parent_refs": ["portfolio_snapshot__caerus_orion__2026-04-30____v1.0.0"],
    "transformation_chain_hash": "9a4f1c..."
  },
  "data": { "nav": 1000420.12, "return_pct": 0.0042, "drawdown_pct": 0.0 }
}
```

### 10.2 Non-Conformant Examples (and Why)

- Performance summary JSON with `return_pct` but no `surface.nav_surface_type`
  → `M007`, refuse.
- `governance.state = "GOVERNED_DEPLOYED"` with `governing_frs = []`
  → `M013`, refuse.
- `confidence.level = "HIGH"` with parent envelope confidence `LOW`
  → `M009`, refuse. Confidence floor violation (Spec 7).
- `provenance.deterministic = false` with `source_state_hash = null`
  → `M010`, refuse. Non-determinism without identity is unreplayable.

---

## 11. Migration from Pre-Spec Artifacts

Existing artifacts produced before this specification are **grandfathered**
under the following rules:

1. They are *not* required to be re-emitted with envelopes.
2. The MCP ingestion layer MUST attempt to synthesise the envelope at index
   time from available metadata (file path, embedded fields, governance
   cross-reference).
3. Synthesised envelopes MUST carry `provenance.source_state_hash = null`
   and a synthetic `provenance.produced_by = "mcp.ingestion.synth"`.
4. Synthesised objects MUST be flagged in their `annotations.envelope_origin = "synthesised"`.
5. Any *new* artifact produced after the publication date of this spec
   (2026-05-21) MUST be conformant at production time; synthesis is not a
   substitute for native conformance going forward.

Spec 8 (Schema Evolution Governance) defines the lifecycle for raising
synthesised artifacts to native conformance.

---

## 12. Errata

*(none at v1)*

---

*SEM-001 v1 — 2026-05-21. See SEM-INDEX (`README.md`).*
