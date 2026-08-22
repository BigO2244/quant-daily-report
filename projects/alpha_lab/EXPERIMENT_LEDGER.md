# Experiment Ledger

Preserved append-only human lifecycle projection. Never delete failed or
superseded experiments. After the global-ledger cutover, cumulative family,
experiment-generation, trial, verified-inference, challenge, and independent-
review truth comes from the canonical GCP event chain;
this table remains a readable lifecycle/verdict view.

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
| EXP-2026-0013 | HYP-2026-013 | 2026-07-30 | `RUNNING` | `UNPROVEN` | GCP `HYP-2026-013/ai-power-grid-readiness-20260730T212000Z-hyp-2026-013-data-gate-v1` | `ITERATE` — `BLOCKED_DATA`; first immutable gate exists, the source-audited event tape is still required, and returns/challenge remain unread |

## State vocabulary

`DISCUSS → FROZEN → RUNNING → REVIEW → PURSUE | PARK | KILL`

Any change to the feature definition, universe, benchmark, risk model, holding
horizon, portfolio construction, or primary metric after `FROZEN` creates a new
experiment rather than revising the old result.
