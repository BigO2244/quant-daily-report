---
last_reviewed: 2026-08-11
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

## PAPER Authority and Whole-Share Convergence

The governed PAPER path is an immutable Evidence → Decision → Risk →
Execution → Audit chain. `core/target_attainment_policy.py` validates the
owner-approved 5% cash target, 2.5% hard floor, exact nearest-feasible proof,
first-clean comparison epoch, and strict GREEN propagation.
`core/whole_share_feasibility.py` translates Decision's weights into a
deterministic, provably bounded whole-share optimum without selecting symbols.
`core/lane_target_attainment.py` compares reconciled broker quantities with that
proof; it cannot relabel a missing or mismatched proof as attained. Execution
package v2 carries Risk's immutable constraints to the Trader, while version 1
remains read-compatible only for historical lineage.
