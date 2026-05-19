---
last_reviewed: 2026-05-18
owner: operations
category: governance
criticality: high
canonical: true
related_systems: [governance, documentation, operations]
---

# Governance Taxonomy

Caerus governance is broader than Friday Refactor work. FR remains a useful
maintenance pattern, but the canonical governance categories are now:

| Category | Meaning | Typical Blast Radius | Deployment Expectation |
|---|---|---|---|
| `ARC` | Architecture / research evolution, design scaffolding, dev-only research systems. | LOW to MEDIUM | Usually local or promotion-gated; no runtime change unless explicitly promoted. |
| `OPS` | Operational / infrastructure hardening, deployment safety, scheduler, recovery, observability. | MEDIUM to HIGH | Requires validation, rollback plan, and observation when deployed. |
| `DOC` | Documentation governance, canonicalization, metadata, drift tooling. | LOW | Read-only or docs-only unless tooling is later wired into CI. |
| `HOTFIX` | Immediate operational risk mitigation. | MEDIUM to HIGH | Requires evidence capture, rollback plan, and later reconciliation into git/governance. |
| `FR` | Friday maintenance/refactor package. | LOW to HIGH | Uses normal lifecycle and blast-radius rules; category should be paired with ARC/OPS/DOC when possible. |

## Lifecycle

The lifecycle remains:

```text
BACKLOG -> READY -> READY_VALIDATED -> IN_PROGRESS -> DONE -> DEPLOYED
```

`DEPLOYED_OBSERVING` and `REVIEWED_DEFERRED` remain valid extended states for
operational observation and explicitly deferred work.

## Rollback Expectations

- `ARC`: ignore or revert dev-only artifacts unless promoted.
- `OPS`: prefer git revert, VM fast-forward, and preserved runtime evidence.
- `DOC`: revert docs/tooling changes; do not delete historical records to hide drift.
- `HOTFIX`: preserve incident evidence, reconcile source through git, then document.
- `FR`: follow the FR governance model and category-specific rollback guidance.

## Maintenance Window Guidance

- `DOC` and low-risk `ARC` can be done during normal development windows.
- `OPS` execution-adjacent work should prefer Friday after market close or a
  deliberately selected non-trading window.
- `HOTFIX` work is incident-driven and must capture evidence before mutation.

