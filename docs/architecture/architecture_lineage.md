---
last_reviewed: 2026-06-25
owner: architecture
category: architecture
criticality: medium
canonical: false
canonical_status: Needs Repository Verification until committed and reconciled
related_systems: [alpha_stack, execution, recovery, dashboard, research_data]
---

# Architecture Lineage

This document records the high-level architecture lineage that should guide
future operational and documentation updates.

## Document Contract

| Field | Value |
|---|---|
| Purpose | Preserve high-level architecture lineage pointers and topic-level context. |
| Owner | `architecture` in front matter; repository verification still required. |
| Inputs | Architecture records, governance docs, engineering decision index. |
| Outputs | Lineage context for future architecture updates. |
| Related Documents | `docs/architecture/ENGINEERING_DECISION_INDEX.md`, `docs/architecture/DOCUMENT_INVENTORY.md`. |
| Related Tests | N/A. |
| Related Implementation | Cross-system. |
| Related Artifacts | N/A. |
| Known Gaps | Canonical status is `Needs Repository Verification` until committed and reconciled. |

## Current Major Systems

- Alpha Stack strategy and sleeve allocation.
- Broker-authoritative paper execution through Alpaca.
- VM cron scheduler for daily phases.
- Shadow candidates and non-blocking shadow reporting.
- Research Data Platform / FR-DH research-data layer, observe-only.
- Dashboard static publication to protected routes.
- Recovery lifecycle intelligence, dev-only.
- Governance, artifact, and documentation intelligence, dev-only.

## Architecture Records

Use `docs/architecture/ENGINEERING_DECISION_INDEX.md` for the current
architecture decision index. This lineage file remains a topic-level pointer
until its canonical status is repository-verified.

## Lineage Rules

- Git history is necessary but insufficient for operational memory.
- Semantic changes should be summarized in governance or architecture docs.
- Execution-semantic changes require explicit validation and observation notes.
- Dev-only systems must remain clearly separated from runtime execution paths.
