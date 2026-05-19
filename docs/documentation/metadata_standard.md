---
last_reviewed: 2026-05-18
owner: operations
category: documentation
criticality: medium
canonical: true
related_systems: [documentation, governance]
---

# Documentation Metadata Standard

Metadata is intentionally lightweight. Canonical docs should use this front
matter when practical:

```yaml
---
last_reviewed: YYYY-MM-DD
owner: operations
category: operations|governance|architecture|documentation|research|historical
criticality: critical|high|medium|low
canonical: true|false
related_systems: [execution, dashboard, recovery]
---
```

## Rules

- Add metadata first to canonical operational and governance docs.
- Do not bulk-edit historical docs just to satisfy a validator.
- `last_reviewed` means the doc was checked against current source and runtime
  semantics, not merely touched.
- `canonical: true` means the doc is a source of truth for its domain.
- Generated reports should not be marked canonical.

