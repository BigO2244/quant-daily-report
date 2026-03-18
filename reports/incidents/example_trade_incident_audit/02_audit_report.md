# Trade Incident Audit Report

## 1. Executive Summary

This was a true execution failure with a secondary reporting gap.

Planner output existed and pretrade checks passed. Alpaca accepted two sell orders, then rejected the third sell with `BROKER_REJECT_PDT`. The engine aborted before writing the execution payload, and downstream artifacts later made the run appear as zero execution.

Confidence: high.

## 2. Root Cause

Primary root cause: `broker_reject_pdt`

Secondary root cause: `post_trade_artifact_gap_only`

Exact failure point:

- broker submission began
- sell orders for `DELL` and `LRCX` were accepted
- sell order for `AEP` was rejected due to pattern day trading protection
- the orchestrator aborted before `execution_payload.json` was written

## 3. Evidence Reviewed

- planner intended orders artifact showed nonzero proposed orders
- pretrade reconciliation artifact showed `allowed_to_execute=true`
- broker log showed `DELL` sell accepted
- broker log showed `LRCX` sell accepted
- broker log showed `AEP` sell rejected with `BROKER_REJECT_PDT`
- latest run pointer showed failed status
- execution results showed halt due to missing execution payload
- execution audit misleadingly marked execution as not attempted

## 4. Causal Chain

`planner_produced_trades` -> `pretrade_passed` -> `execution_started` -> `2_orders_accepted` -> `third_order_rejected_broker_reject_pdt` -> `engine_aborted_before_execution_payload` -> `downstream_zero_execution_artifacts`

## 5. Files Touched or Proposed

- `daily_quant_report.py`
  Persist partial submission counts when broker rejection aborts the run.
- `scripts/execute_alpaca_orders.py`
  Preserve prior execution attempt state when payload is missing after an engine abort.
- `core/execution_audit.py`
  Mark halted runs with nonzero submissions as execution attempted.

## 6. Validation Run

- compile validation for touched Python files
- targeted pytest for broker reject classification
- targeted pytest for execution audit and trading-day summary fallback behavior
- dry replay using preserved failed artifacts without submitting orders

## 7. Risks / Assumptions

- accepted broker submissions were proven from logs, but later fill state was not proven from preserved artifacts alone
- the example assumes the broker reject was the first exception propagated out of the orchestrator

## 8. Recommended Next Hardening Step

Persist a checkpoint artifact immediately after each successful broker submission so partial execution is visible even if downstream handoff fails.
