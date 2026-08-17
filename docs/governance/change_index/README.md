# Caerus Change Index

This directory is the searchable operational history for concentrated periods
of production change. Git remains the exact file-level record; these indexes
explain why each commit exists, what runtime behavior changed, how it was
validated, and how it can be rolled back.

## Indexes

- [2026-08-10 through 2026-08-17](2026-08-10_to_2026-08-17.md) — Orion PAPER
  authority, Choice 2 exact execution, schema-3 portfolio operating model, and
  the August 17 partial-account incident/remediation.

## Search

From the repository root:

```text
rg -n "symbol|reason_code|commit|incident|rollback" docs/governance/change_index
```

Use a full or abbreviated commit SHA to jump from this operational index to the
exact Git diff.

## Required cadence

Create or update an index when any of the following occurs:

- more than five production commits land in seven calendar days;
- an execution, reconciliation, broker, deployment, or authority incident occurs;
- a migration changes the canonical artifact or operating model;
- two or more hot fixes are required for the same scheduled workflow.

Each entry must satisfy `docs/governance/change_lineage_standard.md` and record
date, category, operational meaning, validation, rollback, and canonical docs.
