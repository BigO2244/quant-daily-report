# FR-101 Decision-Grade Evidence Window Program

Status: `RESEARCH_ONLY`
Date: `2026-06-19`
Execution Impact: `NON_EXECUTIONAL`
Capital Impact: `$0`

## Objective

FR-101 defines the shortest concrete path from `LOW_EVIDENCE` /
`PARTIAL_EVIDENCE` daily operation to `FULL_EVIDENCE` daily operation for
Level 3 pilot-capital conclusions and scaling evaluation under FR-100.

FR-101 is not a global stop-work order for Level 2.5 pilot evidence collection.
A tightly capped, manually approved, FR-104-controlled live pilot may continue
collecting forward broker and operational evidence while FR-101 matures, but
those live observations remain non-promotional and cannot support scaling until
the FR-101 evidence standard passes.

It does not change strategy selection, sizing, allocation, broker behavior,
runtime scheduling, cron, promotion, or capital deployment.

## Agent Findings

| Agent | Finding |
|---|---|
| Evidence Window Architect | The 30-calendar-day window is `2026-06-20` through `2026-07-19`. It contains 19 XNYS trading days and 11 non-trading days. The default 20-run FR-100 streak cannot be met until the `2026-07-20` run is reviewed on `2026-07-21`. |
| Artifact Completeness Auditor | `FULL_EVIDENCE` is blocked today: 33 local run roots, 0 complete same-run bundles, 0 run-linked verified recon, 0 target-attainment in run roots, and 0 capital-eligible reliability rows. |
| Reliability Evolution Planner | Reliability must become `reliability_signal + evidence_coverage + readiness_usable`; bare `RELIABILITY_GREEN` is telemetry only. |
| Pilot Capital Simulation Architect | FR-101 can simulate Level 3 capital review over paper evidence, but it cannot approve capital. Current classification is `NO_GO_CURRENT_STATE`. |
| Research Portfolio Manager | Cassiopeia deserves most new research evidence work; Orion deserves targeted disposition work; Cygnus is conditional; Phoenix, Polaris, Argo, and Lyra should receive less incremental effort. |
| Skeptical CIO | FR-100 survives only as diagnostic governance. No current evidence supports first-dollar approval. |

## Evidence Window Definition

Counting starts on `2026-06-20`. The 30-calendar-day evidence window ends on
`2026-07-19`.

Trading days:

`2026-06-22`, `2026-06-23`, `2026-06-24`, `2026-06-25`, `2026-06-26`,
`2026-06-29`, `2026-06-30`, `2026-07-01`, `2026-07-02`, `2026-07-06`,
`2026-07-07`, `2026-07-08`, `2026-07-09`, `2026-07-10`, `2026-07-13`,
`2026-07-14`, `2026-07-15`, `2026-07-16`, `2026-07-17`.

Non-trading days:

`2026-06-20`, `2026-06-21`, `2026-06-27`, `2026-06-28`, `2026-07-03`,
`2026-07-04`, `2026-07-05`, `2026-07-11`, `2026-07-12`, `2026-07-18`,
`2026-07-19`.

## Daily Evidence Required

Every calendar day must produce a daily evidence status or non-trading-day
closure classification.

Every eligible trading day must preserve, in the same run root:

- `execution_payload.json`
- `execution_results.json`
- `operator_summary.json`
- broker pretrade account and position snapshots
- broker terminal order/fill evidence
- broker posttrade account and position snapshots
- `broker/recon_posttrade_<TRADE_DATE>.json`
- `broker/post_sell_rebudget_<TRADE_DATE>.json` when sells occur
- `audit/execution_integrity.json`
- `audit/execution_target_attainment_<TRADE_DATE>.json`
- `audit/execution_reliability_report_<TRADE_DATE>.json`
- `audit/sleeve_numeric_trace_*` when numeric invalidation occurs
- global reliability history and readiness artifacts
- date-aligned precompute contract and planned payload when exact planned
  execution is expected

## Required Daily Classifications

Each eligible run must classify:

- evidence coverage: `FULL_EVIDENCE`, `PARTIAL_EVIDENCE`, `LOW_EVIDENCE`, or `NOT_CLASSIFIABLE`;
- reliability: `RELIABILITY_GREEN`, `RELIABILITY_YELLOW`, or `RELIABILITY_RED`;
- compound readiness: `RELIABILITY_GREEN + FULL_EVIDENCE` or a blocked state;
- reconciliation: `RECON_VERIFIED`, `RECON_PARTIAL`, `RECON_MISSING`, or `RECON_NOT_RECONSTRUCTABLE`;
- target attainment: `TARGET_ATTAINED`, `TARGET_WARN`, `TARGET_FAIL`, or `TARGET_UNKNOWN`;
- operator action: present for every non-clean terminal state.

## Gap Analysis

Current blockers:

1. No historical local run root has the complete same-run evidence bundle.
2. Broker-authoritative terminal order/fill evidence is incomplete.
3. Payload, operator, ledger, and run-root lineage is incomplete in legacy roots.
4. Reconciliation is standalone or missing, not run-linked.
5. Target-attainment evidence is missing from run roots.
6. Reliability GREEN exists only as replay telemetry over LOW/PARTIAL evidence.
7. No sleeve is pilot-capital decision-grade for conclusions or scaling.
8. No signed approval, cap, rollback, waiver, or kill-criteria packet exists.

## Shortest Path To Pilot-Capital Readiness

1. Start the forward evidence clock on `2026-06-20`.
2. Require daily calendar closure for all 30 days.
3. Require `FULL_EVIDENCE` same-run bundles for all 19 trading days.
4. Promote FR-083 semantics into the reliability contract: bare GREEN is not enough.
5. Treat any missing required artifact, invalid lineage, RED, unresolved YELLOW,
   unverified reconciliation, target miss, or unexplained terminal state as a
   clock reset or window extension.
6. After paper trust is closed, evaluate a named sleeve and cap under FR-079,
   FR-081, FR-082, and FR-084 for Level 3 readiness.
7. Require signed Brett/CIO review before any pilot capital action.

Level 2.5 pilot evidence collection, if separately approved under FR-104, is
outside this readiness clock and must be labeled as forward evidence collection,
not as a passed pilot-capital readiness gate.

## Earliest Pilot-Capital Evaluation Date

The earliest calendar-completeness review is `2026-07-20`.

The earliest default FR-100 20-run evaluation is `2026-07-21`, assuming every
eligible trading day from `2026-06-22` through `2026-07-17` passes and the
`2026-07-20` run also passes with complete artifacts.

If the operator wants a 19-run substitute, it must be predeclared before review
and labeled as an exception. It cannot be selected after favorable results.

## What Codex Should Work On Next

1. Implement a run-retention validator that produces the daily
   `daily_evidence_status` and window summary artifacts without changing trading.
2. Add artifact-completeness coverage fields to reliability outputs and operator
   summaries.
3. Add tests for `GREEN + FULL_EVIDENCE`, `GREEN + PARTIAL_EVIDENCE`, no-action
   with full reason, invalid trade-date lineage, and missing broker/recon/target
   artifacts.
4. Add a non-trading-day calendar closure artifact so weekends and holidays are
   classified deterministically.
5. Add a pilot-capital simulation packet generator that consumes the daily
   status artifacts and produces `NO_GO`, `EXTEND_WINDOW`, or
   `GO_FOR_OWNER_APPROVAL_PACKET`.

## What Humans Should Review Manually

- First clean `FULL_EVIDENCE` run bundle after `2026-06-20`.
- Any `NO_ACTION`, `SKIPPED`, `HALTED`, `FAILED`, `PARTIAL`, rejected, or
  unresolved broker state.
- Any target-cash or exposure drift above threshold.
- Any daily evidence gap that would reset the clock.
- Sleeve nomination, cap, rollback, waiver rules, and kill criteria.
- Any proposed 19-run substitute for the default 20-run FR-100 threshold.

## Current FR Disposition

| FR | Disposition |
|---|---|
| FR-074 | Operationalize further: add evidence coverage/readiness semantics. |
| FR-075 | Fold into machine-readable controls registry or FR-101 follow-up. |
| FR-076 | Complete as historical replay baseline; keep as FR-074 child evidence. |
| FR-077 | Operationalize into a machine-readable evidence contract. |
| FR-078 | Operationalize immediately as daily artifact coverage validator. |
| FR-079 | Keep active after paper evidence is stable; performance labels still block capital. |
| FR-080 | Operationalize immediately as run-linked reconciliation validator. |
| FR-081 | Keep active for PIT benchmark/universe/security-master closure. |
| FR-082 | Keep active for named sleeve promotion evidence gate. |
| FR-083 | Operationalize immediately into reliability coverage hardening. |
| FR-084 | Keep active as approval checklist; cannot pass today. |
| FR-100 | Keep as umbrella trust model, not a capital approval gate. |

No current FR should be retired as obsolete. FR-076 can be marked complete as a
baseline replay artifact once its caveats are linked from FR-074/FR-101.

## Generated Research Artifacts

- `outputs/research/decision_grade_window/fr101_2026-06-20_2026-07-19/decision_grade_window_definition.md`
- `outputs/research/decision_grade_window/fr101_2026-06-20_2026-07-19/artifact_completeness_gap_analysis.md`
- `outputs/research/decision_grade_window/fr101_2026-06-20_2026-07-19/reliability_maturity_plan.md`
- `outputs/research/decision_grade_window/fr101_2026-06-20_2026-07-19/pilot_capital_simulation_framework.md`
- `outputs/research/decision_grade_window/fr101_2026-06-20_2026-07-19/research_prioritization_recommendation.md`
- `outputs/research/decision_grade_window/fr101_2026-06-20_2026-07-19/cio_falsification_review.md`

## Governance Boundary

FR-101 is research/governance only. It produces evidence requirements and
readiness semantics. It does not permit capital, change broker behavior, alter
strategy selection, change allocation or sizing, or modify runtime scheduling.
It also does not cancel or pause a separately approved FR-104 Level 2.5 pilot
evidence-collection run, provided that run remains manually approved, capped,
segregated, and non-promotional.
