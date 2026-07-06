---
last_reviewed: 2026-05-21
owner: architecture
category: architecture
criticality: high
canonical: false
related_systems: [research_registry, provenance, replay, governance]
---

# Research Registry Query Layer

This note documents the additive read-only query layer for the Caerus
Research Registry. The frozen SEM-001 through SEM-008 contracts remain
authoritative. This layer does not add transport, orchestration,
execution, broker access, dashboard integration, or mutation APIs.

## Document Contract

| Field | Value |
|---|---|
| Purpose | Document the read-only Research Registry query layer and stop boundary. |
| Owner | `architecture` in front matter. |
| Inputs | Research registry implementation, semantic contracts, query-layer tests. |
| Outputs | Implementation reference for deterministic local registry queries. |
| Related Documents | `docs/architecture/semantics/README.md`, `docs/architecture/research_registry_v1_foundation.md`, `docs/architecture/DOCUMENT_INVENTORY.md`. |
| Related Tests | Query and registry tests listed in `docs/architecture/DOCUMENT_INVENTORY.md`. |
| Related Implementation | `research_registry/query/`, `research_registry/`. |
| Related Artifacts | Registry indexes and MCP/research generated artifacts. |
| Known Gaps | Current implementation state should be refreshed from code/tests before present-tense claims. |

## Scope

Package: `research_registry/query/`

Implemented capabilities:

- deterministic object retrieval: `get_object`, `list_objects`
- typed filtering: type, surface, confidence, governance state
- temporal filtering: as-of anchor and trade date
- provenance traversal: parents, children, ancestors, descendants
- replay-safe reconstruction introspection
- registry summary and statistics
- orphan detection
- surface conflict detection

All results are explicitly sorted by `object_id` where ordering matters.
Queries hydrate through the existing registry validation path and do not
write to the registry or source artifacts.

## Replay-Safe Introspection

`reconstruct_object_state(object_id, anchor)` is an introspection view,
not a reconstruction engine. It reports whether the canonical object is
present at the declared UTC anchor under the existing temporal fence.
It does not persist a reconstructed object and does not substitute
current-state results for fenced-out objects.

## Surface Conflict Detection

`detect_surface_conflicts()` groups NAV-surface-bearing objects by
`strategy_ref` and `trade_date`, then applies the SEM-002 compatibility
matrix. It reports incompatible co-presence for inspection only. It does
not combine, aggregate, migrate, or repair any surface.

## Stop Boundary

This layer intentionally stops at local registry introspection. Future
MCP retrieval tools may call this package, but no MCP transport or
runtime service is implemented here.
