---
last_reviewed: 2026-05-18
owner: operations
category: governance
criticality: medium
canonical: true
related_systems: [governance, architecture, operations]
---

# Change Lineage Standard

Git history records file changes. Governance docs record operational meaning.

## What Requires Lineage Notes

- Architecture milestones.
- Operational semantic changes.
- Governance model changes.
- Execution semantic changes.
- Recovery, reconciliation, or deployment behavior changes.
- Canonical documentation hierarchy changes.

## Minimum Lineage Fields

- Date.
- Category: `ARC`, `OPS`, `DOC`, `HOTFIX`, or `FR`.
- Summary.
- Runtime impact.
- Validation.
- Rollback or deactivation path.
- Canonical documents updated.

## Non-Goals

- This standard does not create a new deployment gate.
- This standard does not replace the FR registry.
- This standard does not require editing historical records retroactively.

