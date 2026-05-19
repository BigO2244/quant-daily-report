---
last_reviewed: 2026-05-18
owner: operations
category: documentation
criticality: high
canonical: true
related_systems: [agents, operations, governance]
---

# AGENTS.md Hardening Guidance

`AGENTS.md` is the operational runtime handoff for agents. It should remain
concise enough to be useful during incidents and detailed enough to prevent
unsafe actions.

## Target Shape

- Current runtime strategy state.
- Scheduler and VM ownership.
- Execution and broker safety rules.
- Deployment and rollback expectations.
- Links to canonical docs for deep architecture.

## Avoid

- Long historical architecture narratives.
- Full copies of runbooks.
- Generated report excerpts.
- Detailed research roadmaps that belong in `docs/architecture/` or
  `docs/governance/`.

## Preservation Rule

Do not remove operational safety guidance unless the replacement canonical doc
is already present and linked.

