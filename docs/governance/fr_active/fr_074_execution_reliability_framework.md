# FR-074 — Execution Reliability Framework

## Status

`DEPLOYED_OBSERVING` — Phase A minimal deterministic reliability report.

## Objective

Stop discovering execution failures one incident at a time by centralizing
operational invariant checks into one daily run-scoped reliability artifact.
Every skipped, blocked, dropped, stale, unreconciled, or zero-trade outcome must
carry a machine-readable reason, human-readable summary, severity, evidence, and
recommended operator action.

## Phase A Scope

Phase A is observe-first. It adds:

- `core/operational_invariants.py`
- `outputs/runs/<RUN_ID>/audit/execution_reliability_report_<TRADE_DATE>.json`
- `execution_reliability_status`, `execution_reliability_score`,
  `execution_reliability_artifact`, and `execution_reliability_actions` in
  `operator_summary.json`
- deterministic tests for planned-payload drops, legitimate empty no-action,
  broker acceptance failure, cash target drift, reconciliation mismatch, missing
  terminal reasons, and sleeve numeric non-finite traces

No strategy selection, sleeve behavior, allocation policy, sizing, cash policy,
or broker submission semantics are changed. Phase A only preserves existing
fail-closed behavior where a nonempty planned payload becomes zero executable or
submitted trades.

## Invariants

Phase A normalizes these checks:

- nonempty planned payload with zero executable or submitted trades
- submitted orders greater than zero with zero accepted orders
- accepted orders with zero fills and unresolved broker state
- material target-cash versus actual-cash drift using target-attainment evidence
- non-clean model/broker reconciliation
- missing or stale required precompute artifacts
- blocking non-finite sleeve numeric trace artifacts
- terminal `NO_ACTION`, `SKIPPED`, `HALTED`, `FAILED`, or partial states without
  an explicit reason
- existing `audit/execution_integrity.json` findings as reliability findings

Each invariant row includes `invariant_id`, `status`, `severity`,
`reason_code`, `human_summary`, `operator_action`, and `evidence`.

## Artifact Contract

Daily report path:

`outputs/runs/<RUN_ID>/audit/execution_reliability_report_<TRADE_DATE>.json`

Required top-level fields:

- `run_id`
- `trade_date`
- `score`
- `overall_status`
- `invariant_results`
- `summary_counts`
- `recommended_operator_actions`
- `source_artifact_paths`

The execution integrity score is deterministic from invariant rows. It starts at
100, subtracts larger penalties for critical failures, smaller penalties for
warnings, and floors at 0. Phase A does not use the score for trading decisions.

## Future Phases

Phase B should harden report consumption:

- block operator email/report publication when reliability is `FAIL`
- make missing operator actions impossible for non-clean reconciliation
- require explicit source-artifact freshness metadata for every invariant
- promote accepted-but-unfilled unresolved status from warning to fail where
  broker semantics demand fail-closed behavior
- add dashboard and MCP surfaces for reliability history and recurring reason
  code trends
- add runbook links per reason code once enough incident classes stabilize

## Rollback

Revert the FR-074 module, execution-path report write, tests, and governance
rows. Existing execution safeguards from FR-031, FR-070, and FR-073 should remain
in place.
