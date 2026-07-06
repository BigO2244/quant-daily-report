---
last_reviewed: 2026-05-21
owner: architecture
category: architecture
criticality: critical
canonical: true
related_systems: [research, governance, attribution, regime, shadow, alpha_stack, mcp]
---

# Caerus Semantic Contract Layer — Index

**Layer Version:** 1.0
**Date:** 2026-05-21
**Baseline:** `docs/architecture/caerus_research_mcp_architecture.md` v1.0
**Classification:** Institutional Semantic Standards — Canonical

---

## Purpose

The Semantic Contract Layer is the canonical institutional specification of
*what Caerus believes, how it knows, and how it can prove it later*. It sits
between the architecture document (which describes the system) and any
implementation (which realises the system).

Its job is to prevent — by construction — the failure modes that destroy
institutional research over time:

- ontology drift
- metadata inconsistency
- provenance ambiguity
- governance fragmentation
- truth-surface contamination
- historical replay instability
- silent confidence inflation
- temporal contamination from future information

The architecture document defines the system. This layer defines the
*meaning* that the system is required to preserve.

---

## Scope

This layer **specifies semantics**. It does not specify storage layouts,
filesystem paths, transport, language bindings, deployment topology, or
implementation modules. Those are implementation concerns covered by the
architecture document and downstream engineering work.

The Semantic Contract Layer is binding on:

- The Caerus Research MCP (cognition, retrieval, governance, storage layers).
- Any tool, script, notebook, or human process that produces or consumes
  research objects intended for institutional consumption.
- Any future research surface that claims interoperability with Caerus
  research artifacts.

It is **not** binding on:

- Operational broker integration (separate plane, separate trust model).
- Cron and execution pipelines (governed by FR/OPS taxonomy, not by this layer).
- Internal scratch work that is never promoted into the research corpus.

---

## Specifications

| # | Specification | Version | File | Status |
|---|---|---|---|---|
| 1 | Metadata Standard | v1 | [metadata_standard_v1.md](metadata_standard_v1.md) | Canonical |
| 2 | Truth Surface Standard | v1 | [truth_surface_standard_v1.md](truth_surface_standard_v1.md) | Canonical |
| 3 | Provenance Contract | v1 | [provenance_contract_v1.md](provenance_contract_v1.md) | Canonical |
| 4 | Governance Semantics | v1 | [governance_semantics_v1.md](governance_semantics_v1.md) | Canonical |
| 5 | Semantic Versioning Framework | v1 | [semantic_versioning_framework_v1.md](semantic_versioning_framework_v1.md) | Canonical |
| 6 | Point-in-Time Reconstruction Semantics | v1 | [point_in_time_reconstruction_v1.md](point_in_time_reconstruction_v1.md) | Canonical |
| 7 | Confidence Semantics Standard | v1 | [confidence_semantics_standard_v1.md](confidence_semantics_standard_v1.md) | Canonical |
| 8 | Schema Evolution Governance | v1 | [schema_evolution_governance_v1.md](schema_evolution_governance_v1.md) | Canonical |

Cross-surface compatibility rules are normative within Spec 2. Historical
replay guarantees are normative within Spec 6.

---

## Freeze and Implementation Contracts (v1)

The eight specifications above are **frozen** as of 2026-05-21 under the
freeze documents below. The freeze documents are constitutional: they
formalise what may evolve, what is prohibited under v1, and what the
implementation is required to guarantee.

| # | Document | File | Status |
|---|---|---|---|
| F1 | Semantic Freeze v1 | [SEMANTIC_FREEZE_v1.md](SEMANTIC_FREEZE_v1.md) | Canonical — Frozen |
| F2 | Implementation Conformance Guide v1 | [IMPLEMENTATION_CONFORMANCE_GUIDE_v1.md](IMPLEMENTATION_CONFORMANCE_GUIDE_v1.md) | Canonical — Implementation Contract |
| F3 | Registry Invariants v1 | [REGISTRY_INVARIANTS_v1.md](REGISTRY_INVARIANTS_v1.md) | Canonical — Registry Correctness |
| F4 | MCP Implementation Boundaries v1 | [MCP_IMPLEMENTATION_BOUNDARIES_v1.md](MCP_IMPLEMENTATION_BOUNDARIES_v1.md) | Canonical — Constitutional Boundary |
| F5 | Replay and Reconstruction Guarantees v1 | [REPLAY_AND_RECONSTRUCTION_GUARANTEES_v1.md](REPLAY_AND_RECONSTRUCTION_GUARANTEES_v1.md) | Canonical — Temporal Honesty Contract |

The freeze documents do not introduce new ontology, governance, surface,
or confidence semantics. They formalise and constrain the existing
Spec 1–Spec 8 surface for implementation phase entry. If a freeze
document conflicts with its underlying SEM-00N specification, the
SEM-00N specification governs.

---

## Normative Language

All specifications in this layer use [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119)
keywords with their conventional meaning:

- **MUST / MUST NOT / REQUIRED / SHALL / SHALL NOT** — absolute requirement / prohibition.
- **SHOULD / SHOULD NOT / RECOMMENDED** — strong preference; violation requires documented justification.
- **MAY / OPTIONAL** — permitted; either choice is conformant.

A research object, tool, or process is **conformant** with this layer
exactly when it satisfies every MUST/MUST NOT clause across all eight
specifications.

---

## The Institutional Question

Every clause in this layer exists to make the system answer, with
provenance, the following question:

> **"What exactly did Caerus believe to be true at a specific point in time,
> on the basis of what evidence, under what governance, and with what
> confidence — and would a faithful reconstruction of that state today
> produce identical conclusions?"**

If a proposed change to any specification weakens the system's ability to
answer that question, it is non-conformant by definition.

---

## Stability Guarantees

Each `vN` specification is **frozen on publication**. Errata are recorded
inline as numbered notes; substantive semantic changes require a new `v(N+1)`
specification and a documented migration path (see Spec 5 and Spec 8).

The Semantic Contract Layer as a whole carries a layer version
(`Layer Version: 1.0`). The layer version increments only when:

- A new specification is added.
- An existing specification supersedes a prior version.
- A cross-specification compatibility rule changes.

Individual erratum notes do not change the layer version.

As of 2026-05-21 the layer is **FROZEN at v1.0** under
[`SEMANTIC_FREEZE_v1.md`](SEMANTIC_FREEZE_v1.md). Amendments are
governed by the freeze's amendment process; MAJOR changes to any
clause governed by the freeze require a successor freeze (v2).

---

## Relationship to Existing Governance

| Existing Document | Relationship |
|---|---|
| `docs/architecture/caerus_research_mcp_architecture.md` | **Baseline.** This layer specifies the semantics the architecture implements. |
| `docs/documentation/metadata_standard.md` | Governs *documentation* front-matter. Spec 1 governs *research object* metadata. Distinct, complementary. |
| `docs/governance/change_lineage_standard.md` | Governs prose lineage notes. Spec 3 governs machine-readable lineage. Both are required; neither replaces the other. |
| `docs/governance/governance_taxonomy.md` | Defines FR categories and lifecycle. Spec 4 formalises their semantics for MCP consumption. |
| `docs/governance/fr_governance_model.md` | Defines FR process. Spec 4 references it as the authoritative lifecycle definition. |
| `docs/governance/fr_registry.md`, `docs/governance/fr_active_backlog.md` | Define current FR-024..029 status and routing. Spec 2 incorporates their surface labels as canonical. |

This layer does not supersede any existing canonical document. It
*formalises* the semantics they collectively imply.

---

## Conformance and Drift

A future drift audit MUST be able to answer, for any research object in the
corpus:

1. Which specifications in this layer apply to the object?
2. Is the object conformant to each applicable specification?
3. If non-conformant, which clause is violated and what is the remediation?

Spec 1 (Metadata Standard) ensures every research object carries enough
identity to answer (1). Spec 3 (Provenance Contract) ensures the answer is
auditable. Spec 8 (Schema Evolution Governance) ensures it remains
answerable as the corpus evolves.

---

*Caerus Semantic Contract Layer v1.0 — 2026-05-21*
*Owner: Architecture / Research Infrastructure*
*Classification: Institutional — Internal Use Only*
