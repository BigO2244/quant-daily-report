---
last_reviewed: 2026-05-21
owner: architecture
category: architecture
criticality: critical
canonical: true
related_systems: [governance, research, mcp]
spec_id: SEM-004
spec_version: v1
supersedes: null
---

# Specification 4 — Governance Semantics v1

**Spec ID:** SEM-004
**Version:** v1
**Date:** 2026-05-21
**Status:** Canonical
**Normative Language:** RFC 2119

---

## 1. Scope

Governance in Caerus is not operational metadata. It is the **lineage of
institutional trust**: the record of which authorities have validated
which artifacts, under what observation, with what rollback path, and
what residual risk.

This specification formalises:

- Governance inheritance along the provenance DAG.
- FR lifecycle semantics for MCP consumption.
- Observation-state semantics and their confidence implications.
- Rollback lineage and deployment lineage.
- The governance DAG and its query primitives.

It does **not** redefine FR process. The authoritative process documents
are `docs/governance/fr_governance_model.md` and
`docs/governance/governance_taxonomy.md`. This specification defines the
*semantics* the MCP and downstream consumers MUST apply to artifacts
governed under that process.

---

## 2. Governance State Enumeration

A research object's `governance.state` (Spec 1) takes exactly one value
from the closed enumeration:

| State | Semantics |
|---|---|
| `UNGOVERNED` | No governance authority covers this object, directly or by inheritance. |
| `GOVERNED_DRAFT` | Object is under active governance development (covering FR is `BACKLOG`, `READY`, `READY_VALIDATED`, or `IN_PROGRESS`). |
| `GOVERNED_DEPLOYED` | Object is covered by at least one FR in state `DEPLOYED`. |
| `GOVERNED_OBSERVING` | Object is covered by at least one FR in state `DEPLOYED_OBSERVING`. |
| `GOVERNED_DEFERRED` | Object is covered by at least one FR in state `REVIEWED_DEFERRED`. |

### 2.1 State Selection Rule

If an object is covered by multiple FRs in distinct states, the
canonical state is selected by precedence:

```
GOVERNED_OBSERVING > GOVERNED_DEFERRED > GOVERNED_DEPLOYED > GOVERNED_DRAFT > UNGOVERNED
```

The "most cautious applicable state wins." An object with one `DEPLOYED`
FR and one `DEPLOYED_OBSERVING` FR is `GOVERNED_OBSERVING`.

`governance.governing_frs` MUST list **all** covering FRs, not only the
one whose state was selected.

---

## 3. Governance Inheritance

Governance flows along the provenance DAG (Spec 3) with the following
rules.

### 3.1 Direct Governance

An object is **directly governed** when a `GovernanceFR` names it in
the FR's documented scope. The covering FR is listed in
`governance.governing_frs` and `governance.coverage_type = DIRECT`.

Direct governance is asserted at production by the producer, or at
ingestion by the FR registry parser. It MUST cite the FR registry entry
that establishes the coverage.

### 3.2 Inherited Governance

An object inherits governance from its parents along `DERIVED_FROM`
edges if and only if:

1. The parent's `governance.state` is `GOVERNED_*` (any GOVERNED state).
2. The derivation is **deterministic** (`provenance.deterministic = true`).
3. The derivation does not cross a surface boundary that breaks
   inheritance (see §3.4).

When inheritance applies, the child's `governance.coverage_type = INHERITED`
and `governing_frs` is the union of inherited FRs (deduplicated).

### 3.3 Inheritance Break Conditions

The following conditions BREAK inheritance. A child receiving inputs
across a break MUST have `governance.coverage_type = UNGOVERNED` unless
it is itself directly governed.

- Non-deterministic derivation (Spec 3 §5.2).
- Cross-surface transformation (Spec 2): if the child changes
  `nav_surface_type` relative to the parent, inheritance breaks.
- Cross-ontology-version derivation: if `schema.ontology_version` differs
  between parent and child, inheritance breaks (Spec 5).

### 3.4 Multi-Parent Inheritance

When an object has multiple parents:

- Inherited `governing_frs` is the **union** across deterministic parents.
- Inherited `governance.state` is the **state-selection precedence**
  (§2.1) over the union.
- If **any** parent is `UNGOVERNED` and the derivation depends materially
  on that parent, inheritance is **defeated**: child is `UNGOVERNED`.

"Material dependence" is defined as appearance in `provenance.input_object_ids`
combined with a non-zero contribution to payload values. The producer
MUST declare materiality explicitly via
`provenance.materiality_map: {input_object_id: "material" | "context"}`.
Absence of declaration defaults to `material` for safety.

---

## 4. FR Lifecycle Semantics (MCP View)

The canonical FR lifecycle is defined in `fr_governance_model.md`. This
specification defines how the MCP MUST interpret each state.

| FR Status | MCP Semantics for Governed Artifacts |
|---|---|
| `BACKLOG` | Artifact is at most `GOVERNED_DRAFT`. Confidence ceiling: `PARTIAL_CONFIDENCE`. Not admissible for promotion gates. |
| `READY` | Same as `BACKLOG`. |
| `READY_VALIDATED` | Same as `BACKLOG`. |
| `IN_PROGRESS` | Same as `BACKLOG`. |
| `PROMOTION_READY` | Artifact is `GOVERNED_DRAFT`. Confidence ceiling: `PARTIAL_CONFIDENCE`. Admissible only as supporting evidence for promotion gates, not primary input. |
| `DEPLOYED_OBSERVING` | Artifact is `GOVERNED_OBSERVING`. Confidence ceiling: `PARTIAL_CONFIDENCE` (Spec 7 downgrade `GOV_OBSERVING`). |
| `DEPLOYED` | Artifact is `GOVERNED_DEPLOYED`. No governance-driven confidence cap. |
| `REVIEWED_DEFERRED` | Artifact is `GOVERNED_DEFERRED`. Confidence ceiling: object-type default (no governance lift, no cap beyond default). |

### 4.1 FR Status Transitions and Downstream Effects

An FR status transition is a **governance event**. The MCP MUST:

1. Re-compute `governance.state` for all artifacts in the FR's coverage
   set at the next index pass.
2. Re-evaluate confidence propagation for downstream objects.
3. Emit a `GovernanceTransition` object (envelope-bearing) recording:
   - The FR's prior and new status.
   - The set of affected `object_id`s.
   - The transition timestamp.
   - The transition's `governing_ref` (commit, PR, or operator action).

`GovernanceTransition` objects are first-class. They populate the
governance history used by replay (Spec 6).

### 4.2 Forbidden Transitions

The MCP MUST refuse to recognise the following transitions:

- Any backward transition that loses observation evidence (e.g.,
  `DEPLOYED` → `DEPLOYED_OBSERVING` without an `AuditFinding`).
- Promotion to `DEPLOYED` from `DEPLOYED_OBSERVING` without recorded
  observation evidence satisfying the FR's `observation_criteria`.
- Coverage extension to artifacts produced **before** the FR's `Introduced`
  date — coverage MUST be forward-from-introduction unless the FR is
  explicitly retrospective.

Retrospective coverage is permitted but MUST be declared via
`fr.retrospective = true` in the registry entry, with a documented
rationale and bounded date range.

---

## 5. Observation Semantics

`DEPLOYED_OBSERVING` is the institutional admission that **deployment
does not equal settlement**. Observation semantics are normative.

### 5.1 Observation State Field

Every artifact governed by an `DEPLOYED_OBSERVING` FR MUST carry:

```json
"governance.observation_status": "not_started" | "observing" | "satisfied" | "blocked" | "not_required"
```

Field semantics:

- `not_started` — the FR is in observation but no qualifying session has
  yet occurred. Forbidden if the FR has been observing > 1 trading day.
- `observing` — observation in progress; criteria not yet met.
- `satisfied` — observation criteria met; FR is eligible for transition
  to `DEPLOYED`.
- `blocked` — observation criteria cannot be met as currently defined; an
  `AuditFinding` MUST exist documenting the blockage.
- `not_required` — the FR has no observation criteria (rare; OPS/DOC
  category FRs with read-only behaviour MAY be `not_required`).

### 5.2 Observation Criteria Stability

An FR's `observation_criteria` MUST NOT be silently weakened during the
observation window. Strengthening criteria is permitted with rationale.
Weakening criteria requires a new `AuditFinding` explaining why the
original criteria were too strict, and the modified criteria MUST be
re-applied from the FR's `Introduced` date forward.

### 5.3 Observation Evidence

An observation transition from `observing` to `satisfied` MUST cite
machine-readable evidence:

- `observation_evidence.metric_object_ids` — `object_id`s of artifacts
  demonstrating the criterion (e.g., 20 consecutive `attribution_run`
  objects with `chain_status = OK`).
- `observation_evidence.evaluated_at` — the timestamp of the evaluation.
- `observation_evidence.evaluator` — the producer of the evaluation
  (script, audit, or human).

Verbal claims of observation success without machine-readable evidence
are non-conformant.

---

## 6. Rollback Lineage

A rollback is a governance event that withdraws an FR's authority over
some or all of its coverage set. Rollback semantics are normative.

### 6.1 Rollback Object

Every rollback MUST produce a `Rollback` object (envelope-bearing) with:

- `data.target_fr_id` — the FR being rolled back.
- `data.rollback_scope` — `FULL` or `PARTIAL`. If `PARTIAL`, a list of
  scoped `object_id`s.
- `data.rollback_reason` — non-empty rationale.
- `data.rollback_method` — one of `GIT_REVERT`, `VM_FAST_FORWARD`,
  `ARTIFACT_QUARANTINE`, `INTERPRETATION_REVERSAL`, `COMPOSITE`.
- `data.preserved_evidence_refs` — list of `object_id`s of evidence that
  MUST be retained (per `fr_governance_model.md` Rollback Discipline).
- `data.governing_audit_finding_ref` — the `AuditFinding` that authorises
  the rollback.

### 6.2 Effects of Rollback

Upon a rollback:

1. The target FR's status MUST become `REVIEWED_DEFERRED` (if research-side
   intent retained) or be removed from active status with an explicit
   `RETIRED` marker.
2. Artifacts whose coverage came **only** from the target FR MUST be
   re-computed for `governance.state` and MAY become `UNGOVERNED`.
3. Downstream artifacts MUST have provenance invalidation evaluated
   (Spec 3 §6).
4. The rollback MUST NOT remove the rolled-back artifacts from the
   corpus. Artifacts persist; their governance changes.

### 6.3 Rollback Replay

A historical replay (Spec 6) at `T_anchor` **before** the rollback MUST
return the pre-rollback governance state. The rollback is a temporally
located event; it does not retroactively rewrite prior governance.

---

## 7. Deployment Lineage

Deployment is the act of moving a governance-bearing change from research
state into operational state.

### 7.1 Deployment Object

Every deployment MUST produce a `Deployment` object (envelope-bearing)
with:

- `data.deployed_frs` — list of FR ids whose status transitions to
  `DEPLOYED` or `DEPLOYED_OBSERVING` as a result.
- `data.source_revision` — the source-control commit SHA being deployed.
- `data.target_environment` — `VM_PRIMARY`, `VM_RESEARCH`, etc.
- `data.deployed_at` — ISO-8601 UTC timestamp.
- `data.deployer` — operator or automated agent identifier.
- `data.preflight_evidence_refs` — list of validation artifact object ids.

`Deployment` objects form a totally ordered history per
`target_environment`. The MCP MUST be able to answer "what was deployed
at `T`?" by selecting the latest `Deployment` with
`deployed_at <= T` for that environment.

### 7.2 Deployment-Governance Coupling

A `DEPLOYED` FR status MUST be backed by at least one `Deployment`
object that includes the FR. An FR whose status is `DEPLOYED` without a
backing `Deployment` is **administratively deployed** and MUST be
flagged in the governance health report.

Administrative deployment is permitted for grandfathered FRs but is a
governance debt. Spec 8 §5 defines the lifecycle for resolving it.

---

## 8. Governance DAG

Governance forms its own DAG, parallel to and intersecting the provenance
DAG.

### 8.1 Governance Nodes

- `GovernanceFR` objects.
- `AuditFinding` objects.
- `Deployment` objects.
- `Rollback` objects.
- `GovernanceTransition` objects.

### 8.2 Governance Edges

| Edge Type | Semantics |
|---|---|
| `FR_DEPENDS_ON` | FR-B requires FR-A to be `DEPLOYED` first (see `fr_governance_model.md` Dependencies). |
| `FR_COVERS` | FR governs an artifact (directly). |
| `AUDIT_FINDS` | AuditFinding pertains to an object. |
| `AUDIT_BLOCKS` | AuditFinding blocks a PromotionAssessment or transition. |
| `DEPLOY_INCLUDES` | Deployment included an FR transition. |
| `ROLLBACK_REVERSES` | Rollback reverses a Deployment. |
| `TRANSITION_CAUSED_BY` | GovernanceTransition caused by an event (Deployment, Rollback, or operator action). |

### 8.3 Required Queries

The MCP MUST support the following governance-DAG queries:

- "What FRs govern this object directly or by inheritance?" — Spec 1
  consumer.
- "What artifacts will be affected if this FR is rolled back?" —
  precondition for any rollback decision.
- "What FRs have been `DEPLOYED_OBSERVING` for more than N days?" —
  governance health.
- "Which AuditFindings currently block promotion of strategy X?" —
  promotion readiness.
- "What was the governance state of object O at time T?" — point-in-time
  governance reconstruction (Spec 6).

---

## 9. Governance Coverage as an Institutional Metric

Governance coverage is a first-class metric, not a decoration.

### 9.1 Coverage Computation

For a given scope (a strategy, a date range, the full corpus), governance
coverage is:

```
coverage_ratio = count(objects where governance.state != UNGOVERNED) /
                 count(objects in scope)
```

Aggregated by `coverage_type`:

```
direct_ratio    = count(DIRECT)    / total
inherited_ratio = count(INHERITED) / total
ungoverned      = count(UNGOVERNED) / total
```

### 9.2 Coverage Reporting Rules

- Coverage MUST be reported separately by object type. Aggregated
  single-number coverage hides type-specific gaps.
- Coverage MUST be reported separately by criticality. An ungoverned
  NAV-bearing object is materially different from an ungoverned
  research note.
- Coverage MUST be reported with the timestamp of computation and the
  set of FRs whose status was considered.

### 9.3 Coverage Trend

The MCP MUST be able to produce coverage trend over time using
`GovernanceTransition` history. Coverage at `T` reconstructs the FR
status set at `T` and applies §3 inheritance to compute coverage at `T`.

---

## 10. Enforcement Surface

| Component | Enforcement |
|---|---|
| FR registry parser | Build governance DAG from `fr_registry.md` and related sources; surface parsing failures as findings. |
| Ingestion layer | Apply §3 inheritance; stamp `governance.state` and `governing_frs` on every object. |
| Retrieval layer | Validate state precedence (§2.1); refuse to serve objects whose declared state contradicts their FR set. |
| Promotion gate evaluator | Apply §4 admissibility rules; block on observing-state inputs to PAPER/LIVE transitions (Spec 2 §7). |
| Audit subsystem | Emit governance health findings (administrative deployments, stale observations, ungoverned critical artifacts). |

---

## 11. Examples

### 11.1 Inheritance Computation

```
broker_snapshot__2026-04-30
  governance.state = GOVERNED_DEPLOYED (FR-024 deployed)
  ↓ (DERIVED_FROM, deterministic)
nav_surface__polaris__2026-04-30__broker_paper
  governance.state = GOVERNED_DEPLOYED (inherited from FR-024)
  governance.coverage_type = INHERITED
  ↓ (DERIVED_FROM, deterministic)
attribution_run__polaris__2026-04-30
  governance.state = GOVERNED_DEPLOYED (still inherited from FR-024)
  governance.coverage_type = INHERITED
```

### 11.2 Inheritance Break

```
attribution_run__polaris__2026-04-30  (GOVERNED_DEPLOYED)
  ↓ (DERIVED_FROM, NON-DETERMINISTIC: LLM summarisation)
research_brief__polaris__2026-04-30
  governance.state = UNGOVERNED  (non-deterministic breaks inheritance per §3.3)
  governance.coverage_type = UNGOVERNED
  confidence.level ≤ PARTIAL_CONFIDENCE  (Spec 7)
```

Inheritance is broken because the summarisation is non-deterministic.
The research brief is `UNGOVERNED` unless an FR directly covers
LLM-generated briefs.

### 11.3 Rollback Effect

FR-028 is rolled back on 2026-05-15. A replay query "as of 2026-05-10"
returns artifacts with their governance state as it was on 2026-05-10
(FR-028 status: `DEPLOYED_OBSERVING`). A query "as of 2026-05-20" returns
the same artifacts with governance state recomputed under FR-028's
post-rollback `REVIEWED_DEFERRED` status. The artifacts themselves are
unchanged; only their `governance.state` differs across queries.

---

## 12. Errata

*(none at v1)*

---

*SEM-004 v1 — 2026-05-21. See SEM-INDEX (`README.md`).*
