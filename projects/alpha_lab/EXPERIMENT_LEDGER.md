# Experiment Ledger

Append-only. Never delete failed or superseded experiments.

| Experiment | Hypothesis | Frozen date | State | Primary classification | Evidence packet | Verdict |
|---|---|---|---|---|---|---|
| EXP-2026-0001 | HYP-2026-001 | 2026-07-23 | `REVIEW` | `UNPROVEN` | `evidence/EXP-2026-0001.md` | `ITERATE` — `BLOCKED_DATA` |
| EXP-2026-0002 | HYP-2026-002 | 2026-07-14 | `REVIEW` | `UNPROVEN` | `evidence/EXP-2026-0002.md` | `ITERATE` — `BLOCKED_DATA` |
| EXP-2026-0003 | HYP-2026-003 | 2026-07-14 | `REVIEW` | `UNPROVEN` | `evidence/EXP-2026-0003.md` | `ITERATE` — `BLOCKED_DATA` |
| EXP-2026-0004 | HYP-2026-004 | 2026-07-14 | `REVIEW` | `UNPROVEN` | `evidence/EXP-2026-0004.md` | `ITERATE` — `BLOCKED_DATA` |
| EXP-2026-0005 | HYP-2026-005 | 2026-07-14 | `REVIEW` | `UNPROVEN` | `evidence/EXP-2026-0005.md` | `ITERATE` — `BLOCKED_DATA` |
| EXP-2026-0006 | HYP-2026-006 | 2026-07-23 | `REVIEW` | `UNPROVEN` | `evidence/EXP-2026-0006.md` | `PARK` — `NON_POSITIVE_VALIDATION` |
| EXP-2026-0007 | HYP-2026-007 | 2026-07-23 | `REVIEW` | `UNPROVEN` | `evidence/EXP-2026-0007.md` | `PARK` — `NON_POSITIVE_VALIDATION` |
| EXP-2026-0008 | HYP-2026-008 | 2026-07-23 | `REVIEW` | `UNPROVEN` | `evidence/EXP-2026-0008.md` | `PARK` — `NON_POSITIVE_VALIDATION_AND_COST_FAILURE` |
| EXP-2026-0009 | HYP-2026-009 | 2026-07-23 | `REVIEW` | `UNPROVEN` | `evidence/EXP-2026-0009.md` | `ITERATE` — `BLOCKED_DATA` |
| EXP-2026-0010 | HYP-2026-010 | 2026-07-23 | `REVIEW` | `UNPROVEN` | `evidence/EXP-2026-0010.md` | `ITERATE` — `BLOCKED_DATA` |
| EXP-2026-0011 | HYP-2026-011 | 2026-07-23 | `REVIEW` | `UNPROVEN` | `evidence/EXP-2026-0011.md` | `ITERATE` — `BLOCKED_DATA` |
| EXP-2026-0012 | HYP-2026-012 | 2026-07-23 | `REVIEW` | `UNPROVEN` | `evidence/EXP-2026-0012.md` | `ITERATE` — `BLOCKED_DATA` |
| EXP-2026-0013 | HYP-2026-013 | 2026-07-30 | `RUNNING` | `UNPROVEN` | Pending first immutable data-gate packet | `PENDING` — source-audited AI power/grid event tape required; returns and challenge period remain unread |
| EXP-2026-0015 | HYP-2026-015 | 2026-08-30 | `RUNNING` | `UNPROVEN` | `evidence/EXP-2026-0015.md`; `hypotheses/HYP-2026-015-ADDENDUM-001_source_materiality_and_evaluator_determinism.md` | `AUTHORIZED_RERUN_PENDING` — first absolute gate blocked at 313,449/313,450; owner-approved Addendum 001 freezes a 99.9% materiality rerun and evaluator determinism; no additional outcome or challenge read |

`EXP-2026-0014` is intentionally absent pending the separately governed
reconciliation of the already-completed HYP-2026-014 source-bridge work. This
freeze does not infer, backfill, or register that evidence.

## State vocabulary

`DISCUSS → FROZEN → RUNNING → REVIEW → PURSUE | PARK | KILL`

Any change to the feature definition, universe, benchmark, risk model, holding
horizon, portfolio construction, or primary metric after `FROZEN` creates a new
experiment rather than revising the old result.
