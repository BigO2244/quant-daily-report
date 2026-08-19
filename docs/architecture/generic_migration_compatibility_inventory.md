---
status: CURRENT_REFERENCE
owner: Caerus implementation team
as_of: 2026-08-18
execution_impact: NONE
---

# Generic Migration Compatibility Inventory

This inventory defines what must remain readable while the generic deployment
and accounting path is observed and cut over. It does not authorize any legacy
artifact to create a new target, order, fill, deployment, or performance claim.

| Historical family | Current reader or record | Migration rule |
|---|---|---|
| PAPER target-authority/schema-3 bundles | `core/paper_target_authority.py` | Read and replay only until generic PAPER authority passes parity; no new generic artifact may be translated back into schema 3 as authority |
| Exact execution plan v3 | `authority/exact_plan.py` | Preserve validation and historical audit reads; all new generic plans use advisory v4 until cutover |
| Submission WAL v1 | `core/submission_wal.py` | Preserve recovery and historical order lineage; do not rewrite v1 records into v2 or reuse their identities |
| Causal ownership ledger | `core/causal_ownership_ledger.py` | Preserve factual pre-cutover ownership and explicit legacy-unattributed rows; never manufacture sleeve attribution |
| FR-104 Live-pilot evidence | `docs/governance/fr_active/fr_104_live_pilot_unlock_program.md` and `scripts/live_pilot_*` | Historical/read-only compatibility only; the generic path may not inherit FR-104 target or execution authority |
| Legacy PAPER/Live run artifacts | Existing output readers and dated runbooks | Retain immutable artifacts and date scope; reporting must suppress claims that lack the new journal/reconciliation/valuation lineage |

Acceptance requirements:

1. Historical readers remain available through observation and rollback
   acceptance.
2. New generic writers never overwrite, relabel, or backfill a historical
   artifact.
3. A historical read grants no execution, activation, approval, or capital
   authority.
4. Cutover may disable legacy writers only after the owner-selected PAPER
   baseline passes parity and rollback has been demonstrated.
5. Retirement removes runtime authority, not evidence. Historical files and
   parsers remain available for audit.

The controlling migration status remains
[`generic_deployment_accounting_migration_plan.md`](generic_deployment_accounting_migration_plan.md).
