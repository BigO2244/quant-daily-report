---
last_reviewed: 2026-05-18
owner: architecture
category: architecture
criticality: medium
canonical: true
related_systems: [alpha_stack, execution, recovery, dashboard]
---

# Architecture Lineage

This document records the high-level architecture lineage that should guide
future operational and documentation updates.

## Current Major Systems

- Alpha Stack strategy and sleeve allocation.
- Broker-authoritative paper execution through Alpaca.
- VM cron scheduler for daily phases.
- Shadow candidates and non-blocking shadow reporting.
- Dashboard static publication to protected routes.
- Recovery lifecycle intelligence, dev-only.
- Governance, provenance, confidence, and documentation intelligence infrastructure.
- Institutional research cognition infrastructure through the Caerus Research MCP semantic layer.
- Read-only provenance-aware research object architecture separated from execution systems.

## Lineage Rules

- Git history is necessary but insufficient for operational memory.
- Semantic changes should be summarized in governance or architecture docs.
- Execution-semantic changes require explicit validation and observation notes.
- Dev-only systems must remain clearly separated from runtime execution paths.

## Semantic Architecture Rules

- The Semantic Contract Layer under `docs/architecture/semantics/` is canonical.
- Institutional ontology, provenance semantics, confidence semantics, governance semantics, replay semantics, and truth-surface semantics are frozen under Semantic Contract Layer v1.
- Filesystem layout is implementation detail only and must not become institutional ontology.
- Point-in-time reconstruction and replay integrity are first-class architectural invariants.
- Research-plane cognition infrastructure must remain isolated from execution-plane mutation systems.
- Read-only enforcement is architectural, not procedural.
