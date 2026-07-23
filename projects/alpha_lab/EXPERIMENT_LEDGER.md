# Experiment Ledger

Append-only. Never delete failed or superseded experiments.

| Experiment | Hypothesis | Frozen date | State | Primary classification | Evidence packet | Verdict |
|---|---|---|---|---|---|---|
| EXP-2026-0001 | HYP-2026-001 | pending | `DISCUSS` | `UNPROVEN` | pending | pending |
| EXP-2026-0002 | HYP-2026-002 | 2026-07-14 | `REVIEW` | `UNPROVEN` | `evidence/EXP-2026-0002.md` | `ITERATE` — `BLOCKED_DATA` |
| EXP-2026-0003 | HYP-2026-003 | 2026-07-14 | `REVIEW` | `UNPROVEN` | `evidence/EXP-2026-0003.md` | `ITERATE` — `BLOCKED_DATA` |
| EXP-2026-0004 | HYP-2026-004 | 2026-07-14 | `REVIEW` | `UNPROVEN` | `evidence/EXP-2026-0004.md` | `ITERATE` — `BLOCKED_DATA` |
| EXP-2026-0005 | HYP-2026-005 | 2026-07-14 | `REVIEW` | `UNPROVEN` | `evidence/EXP-2026-0005.md` | `ITERATE` — `BLOCKED_DATA` |

## State vocabulary

`DISCUSS → FROZEN → RUNNING → REVIEW → PURSUE | PARK | KILL`

Any change to the feature definition, universe, benchmark, risk model, holding
horizon, portfolio construction, or primary metric after `FROZEN` creates a new
experiment rather than revising the old result.
