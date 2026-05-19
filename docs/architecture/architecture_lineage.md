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
- Governance, artifact, and documentation intelligence, dev-only.

## Lineage Rules

- Git history is necessary but insufficient for operational memory.
- Semantic changes should be summarized in governance or architecture docs.
- Execution-semantic changes require explicit validation and observation notes.
- Dev-only systems must remain clearly separated from runtime execution paths.

