---
last_reviewed: 2026-05-21
owner: architecture
category: architecture
criticality: critical
canonical: true
related_systems: [research, attribution, shadow, broker, mcp]
spec_id: SEM-002
spec_version: v1
supersedes: null
---

# Specification 2 — Truth Surface Standard v1

**Spec ID:** SEM-002
**Version:** v1
**Date:** 2026-05-21
**Status:** Canonical
**Normative Language:** RFC 2119

---

## 1. Scope

A **truth surface** is the union of (a) a NAV calculation methodology,
(b) an execution realism profile, and (c) a confidence ceiling.

This specification defines:

- The canonical taxonomy of truth surfaces in the Caerus ecosystem.
- The compatibility matrix governing which surfaces MAY be compared,
  combined, or aggregated.
- Prohibited synthesis rules — operations that MUST NOT occur silently.
- Repaired-chain semantics — how the system treats surfaces with broken
  or reconstructed history.
- Reconciliation semantics — how distinct surfaces may be brought into
  side-by-side view without contamination.
- Promotion restrictions — which surfaces are admissible inputs to
  promotion assessments.

This specification governs every performance number that enters
institutional consumption. It is the single most consequential firewall
against silent error in the Caerus research plane.

---

## 2. Canonical Surface Taxonomy

The Caerus surface taxonomy is closed. Three surfaces are canonical at v1.
Implementations MUST NOT introduce additional surfaces without a
specification revision.

| Surface ID | Display Name | Execution Realism | Confidence Ceiling |
|---|---|---|---|
| `LIVE_BROKER_PAPER_NAV` | Broker Paper NAV | `BROKER_PAPER_FILLS` (orders executed via broker paper engine, fills and account state authoritative) | `BROKER_AUTHORITATIVE` |
| `OPERATIONAL_SHADOW_NAV` | Operational Shadow NAV | `MODEL_PORTFOLIO_NO_BROKER_FILLS` (model-computed portfolio, no order submission, no broker fills) | `PARTIAL_CONFIDENCE` |
| `RESEARCH_BACKTEST_NAV` | Research Backtest NAV | `MODEL_CLOSE_WITH_SYNTHETIC_COSTS` (historical close prices, synthetic transaction costs, no live order surface) | `PARTIAL_CONFIDENCE` |

### 2.1 Surface Identity

Every NAV-bearing object MUST declare its surface via
`envelope.surface.nav_surface_type` (Spec 1 §3.1). The surface is part of
the object's identity for compatibility purposes: two objects with the
same `strategy_ref` and `trade_date` but different surfaces are
**distinct objects**, not variants of the same object.

### 2.2 Surface Confidence Ceiling

A surface's confidence ceiling is the maximum confidence any object on
that surface MAY claim, regardless of dependency quality:

- `LIVE_BROKER_PAPER_NAV` → ceiling `BROKER_AUTHORITATIVE` (top of lattice).
- `OPERATIONAL_SHADOW_NAV` → ceiling `PARTIAL_CONFIDENCE`. No shadow object
  may claim `HIGH` or `BROKER_AUTHORITATIVE`, ever.
- `RESEARCH_BACKTEST_NAV` → ceiling `PARTIAL_CONFIDENCE`. Same prohibition.

The ceiling is a hard cap. The floor-propagation rule (Spec 7) operates
*beneath* the ceiling; an object's confidence is
`min(ceiling, min_parent_confidence, applicable_downgrades)`.

---

## 3. Compatibility Matrix (Normative)

The following matrix defines what operations MAY be performed between
objects on different surfaces. The matrix is symmetric.

| | `LIVE_BROKER` | `OPERATIONAL_SHADOW` | `RESEARCH_BACKTEST` |
|---|---|---|---|
| **`LIVE_BROKER`** | COMPATIBLE | INCOMPATIBLE | INCOMPATIBLE |
| **`OPERATIONAL_SHADOW`** | INCOMPATIBLE | COMPATIBLE | CAUTIOUS_OK |
| **`RESEARCH_BACKTEST`** | INCOMPATIBLE | CAUTIOUS_OK | COMPATIBLE |

### 3.1 Meaning of Compatibility Classes

- **COMPATIBLE** — Objects MAY be combined, compared, aggregated,
  concatenated, or used together in derived analyses without restriction.
  The combined result inherits the shared surface.

- **CAUTIOUS_OK** — Objects MAY be compared *side-by-side* in research
  analyses. They MUST NOT be combined, aggregated, or concatenated into a
  single numeric series. Every such comparison MUST be annotated with a
  surface-mismatch warning in the result envelope under
  `annotations.surface_mismatch`. Promotion inputs derived from a
  CAUTIOUS_OK comparison MUST inherit the lower of the two surface
  confidence ceilings.

- **INCOMPATIBLE** — Objects MUST NOT be combined, compared, or aggregated
  by any conformant tool or pipeline. An MCP tool that receives an
  INCOMPATIBLE combination request MUST refuse, return the individual
  results separately, and emit an explanation. Silent override is
  forbidden.

### 3.2 Override Semantics

A caller MAY request an INCOMPATIBLE combination only by passing an
explicit `surface_override` argument with:

- An `override_rationale` string (free text, MUST be non-empty).
- An `override_audit_ref` (the `object_id` of an `AuditFinding` or
  `GovernanceFR` that authorises the override).

Without both, the tool MUST refuse. With both, the tool MUST execute the
operation but MUST stamp the result envelope with:

```json
"annotations.surface_override": {
  "rationale": "...",
  "audit_ref": "...",
  "override_severity": "HIGH"
}
```

and downgrade the result's `confidence.level` to `LOW`.

---

## 4. Prohibited Synthesis Rules

The following operations are forbidden under all circumstances. There is
no override mechanism for prohibited synthesis.

1. **No surface promotion in concatenation.** If a NAV series is built by
   concatenating segments from different surfaces, the resulting series
   MUST NOT be re-labelled with a higher-confidence surface than any of
   its segments. (Concatenation across surfaces is itself INCOMPATIBLE
   absent an override; see §3.)

2. **No silent surface inference.** If an object lacks an explicit surface
   label and a tool is asked to assign one, the tool MUST refuse. Surfaces
   are produced, not inferred.

3. **No surface laundering.** A derived object MUST inherit (or be lower
   than) the surface confidence ceiling of its inputs. No transformation —
   smoothing, resampling, aggregating, attribution — can elevate a
   `RESEARCH_BACKTEST_NAV` output to broker-authoritative status.

4. **No surface forgery via display.** Display layers (dashboards,
   reports) MUST render the surface label adjacent to every performance
   number. A NAV chart that displays both broker and shadow NAV on the
   same axis without distinguishing labels is non-conformant.

5. **No surface drop on summary.** Aggregated reports (weekly, monthly,
   YTD summaries) MUST preserve the surface label. A summary built from
   multiple surfaces MUST be CAUTIOUS_OK-annotated even if its inputs
   share `trade_date` ranges.

---

## 5. Chain Semantics

Every NAV-bearing object carries a `surface.chain_status` field with one
of the following values:

| Value | Meaning |
|---|---|
| `OK` | Continuous chain from a prior session NAV on the same surface. |
| `NO_PRIOR` | First session for this surface and strategy; no prior NAV to chain from. |
| `BROKEN_CHAIN` | A prior NAV existed but the chain link is missing or unverifiable (e.g., missing intermediate day). |
| `REPAIRED` | The chain was reconstructed after a known break (see §5.1). |
| `NOT_APPLICABLE` | The object is not part of a chained NAV series (e.g., single-day attribution). |

### 5.1 Repaired-Chain Semantics

A `REPAIRED` chain status MUST satisfy all of the following:

1. The repair MUST be governed by an `AuditFinding` or `GovernanceFR`
   whose `object_id` is recorded in the envelope under
   `annotations.chain_repair.governing_ref`.

2. The repaired object MUST carry confidence at most `PARTIAL_CONFIDENCE`.

3. The repair MUST preserve the original (pre-repair) artifact via a
   `SUPERSEDES` lineage edge; the original is archived, not deleted
   (see Spec 3 §8).

4. The repair record MUST include:
   - `annotations.chain_repair.method` — how the gap was reconstructed.
   - `annotations.chain_repair.assumptions` — what was assumed.
   - `annotations.chain_repair.affected_range` — the inclusive date range
     reconstructed.

5. A repaired chain segment MUST be flagged in every downstream
   consumer's `annotations.chain_repair_inherited = true`.

### 5.2 Broken-Chain Containment

A `BROKEN_CHAIN` status MUST trigger:

- `confidence.level` floor of `LOW` (Spec 7 downgrade trigger `CHAIN_BROKEN`).
- `governance.observation_status = observing` if the governing FR allows.
- Exclusion from promotion assessment inputs (see §7).

`BROKEN_CHAIN` is a recoverable state via §5.1; it is not terminal.

---

## 6. Reconciliation Semantics

Reconciliation is the act of placing two or more surfaces in side-by-side
view for institutional comparison. It is a research operation, not an
aggregation.

A reconciliation report MUST:

1. Render each surface's NAV/returns as separate, labelled series.
2. Not compute combined metrics (e.g., a "blended Sharpe") unless the
   surfaces are COMPATIBLE.
3. Include a `surface_compatibility_summary` block in its envelope
   listing the pairwise compatibility class for every pair of surfaces
   in the report.
4. Inherit the lowest confidence ceiling of any included surface.

The MCP `compare_strategies` tool (architecture §15) is a reconciliation
tool by definition.

---

## 7. Promotion Restrictions

A promotion assessment (`PromotionState` transition) is governed by
strict surface requirements per target state:

| Target State | Required Input Surface | Notes |
|---|---|---|
| `BACKTEST` | `RESEARCH_BACKTEST_NAV` MAY be sole input. | |
| `SHADOW` | `RESEARCH_BACKTEST_NAV` plus a documented validation plan. | |
| `PAPER` | `OPERATIONAL_SHADOW_NAV` with `chain_status = OK` for at least the observation window of the gating FR (default 20 trading sessions). | `RESEARCH_BACKTEST_NAV` MAY accompany as supporting evidence but MUST NOT be the primary input. |
| `LIVE` (future) | `LIVE_BROKER_PAPER_NAV` with `chain_status = OK` for the gating window, plus all prior states' evidence. | |

A `PromotionAssessment` with primary inputs failing the surface
requirement above MUST be marked `blocking_findings += [SURFACE_INSUFFICIENT]`
and MUST NOT be marked `governance_readiness = true`.

A `PromotionAssessment` MUST NOT consume objects with `chain_status` in
`{NO_PRIOR, BROKEN_CHAIN}` as primary evidence. Such inputs MAY appear
as context but MUST be explicitly excluded from gate computation.

---

## 8. Surface Versioning

Surface identifiers (`LIVE_BROKER_PAPER_NAV`, etc.) are stable. The
*methodology* under a surface MAY evolve under Spec 8 (Schema Evolution
Governance) with the following constraint:

- Methodology changes that alter the meaning of any historical NAV MUST
  trigger surface deprecation and reissue under a new identifier.
- Methodology changes that strictly improve precision (e.g., better
  rounding, fixed bugs that did not affect historical results) MAY remain
  under the same identifier but MUST be recorded in this spec's errata
  with a methodology revision number.

A consumer querying a NAV series MUST be able to determine the methodology
revision in force at production time via `envelope.surface.methodology_revision`
(OPTIONAL field; absence means `1` for backward compatibility).

---

## 9. Enforcement Surface

The following components MUST enforce this specification:

| Component | Enforcement |
|---|---|
| MCP retrieval layer | Validate surface labels at hydration; refuse to hydrate NAV-bearing objects without surface fields. |
| MCP cognition layer | Apply compatibility matrix to every cross-surface query; refuse INCOMPATIBLE combinations. |
| Promotion gate evaluator | Apply §7 restrictions; block on `SURFACE_INSUFFICIENT`. |
| Display/reporting layers | Render surface labels adjacent to every NAV-bearing number. |
| Ingestion layer | Reject artifacts missing surface labels at index time and surface them as findings, not data. |

---

## 10. Examples

### 10.1 Refused Combination

Query: "Show me Polaris (LIVE_BROKER) and Orion (OPERATIONAL_SHADOW) on
one NAV chart from April 1 to April 30."

Correct response: refuse the combination. Return two separate series,
each fully labelled. Emit:

```json
{
  "refusal": "INCOMPATIBLE_SURFACES",
  "explanation": "LIVE_BROKER_PAPER_NAV and OPERATIONAL_SHADOW_NAV are INCOMPATIBLE per SEM-002 §3.",
  "individual_results": [
    { "strategy_ref": "caerus_polaris", "surface": "LIVE_BROKER_PAPER_NAV", "...": "..." },
    { "strategy_ref": "caerus_orion", "surface": "OPERATIONAL_SHADOW_NAV", "...": "..." }
  ]
}
```

### 10.2 Permitted Cautious Comparison

Query: "Compare Orion's shadow NAV to its backtest NAV for April."

Correct response: render side-by-side. Annotate `surface_mismatch`.
Refuse to compute a "tracking error" or any combined metric unless the
caller explicitly invokes a research statistic that the spec defines for
CAUTIOUS_OK pairs (e.g., correlation, MAE) — and the result MUST inherit
`PARTIAL_CONFIDENCE` ceiling.

### 10.3 Blocked Promotion

Query: "Is Orion ready to promote from SHADOW to PAPER?"

Correct response if Orion's most recent `OPERATIONAL_SHADOW_NAV` has
`chain_status = NO_PRIOR`:

```
PromotionAssessment:
  target: PAPER
  governance_readiness: false
  blocking_findings: [
    "SURFACE_INSUFFICIENT: chain_status=NO_PRIOR; SEM-002 §7 requires OK for gating window."
  ]
```

---

## 11. Errata

*(none at v1)*

---

*SEM-002 v1 — 2026-05-21. See SEM-INDEX (`README.md`).*
