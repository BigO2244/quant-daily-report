# Trade Incident Audit Template

## TASK TYPE

Audit / Review / Patch

## RECOMMENDED MODEL

GPT-5.3-Codex

## GOAL

Perform a full end-to-end diagnostic of a trading incident, determine the exact failure point in the governed execution chain, and produce a deterministic report with evidence.

## CONTEXT

Repository: `quant-daily-report-main`

This template is for incidents where expected trade execution did not occur, only partially occurred, or appears ambiguous from summaries or artifacts.

Expected scope:
- planner output / proposed trades
- latest run and operator summary artifacts
- pre-trade validation / reconciliation gates
- execution workflow trigger and control flow
- broker submission path
- execution audit / post-trade artifacts
- GitHub Actions logs or local run logs if present

## CONSTRAINTS

- minimal changes unless requested
- preserve deterministic artifacts
- do not introduce look-ahead bias
- do not silently change execution behavior
- run targeted validation where possible
- do not make broad refactors
- prioritize diagnosis first; patch only if the root cause is clear
- preserve production-intent behavior unless a narrow observability fix is needed
- if evidence is ambiguous, say so explicitly and rank likely causes

## STANDARD ROOT-CAUSE TAXONOMY

Use one primary cause and, if needed, one secondary observability cause.

- `planner_zero_trades`
- `planner_artifact_missing`
- `pretrade_blocked_reconciliation`
- `pretrade_blocked_governance`
- `workflow_skip_condition`
- `workflow_trigger_missing`
- `execution_payload_missing`
- `order_filtering_to_zero`
- `broker_auth_error`
- `broker_reject_pdt`
- `broker_reject_buying_power`
- `broker_reject_symbol_rule`
- `broker_transport_error`
- `partial_execution_then_abort`
- `post_trade_artifact_gap_only`
- `summary_misreport_only`
- `unknown_needs_more_observability`

## REQUIRED AUDIT STEPS

1. Identify the relevant trading date, run ID, workflow run, and trading mode.
2. Inspect planner outputs and confirm whether trades were proposed.
3. Inspect latest run pointers and operator summary artifacts.
4. Inspect pre-trade validation, reconciliation, and governance gates.
5. Trace execution control flow from orchestrator to broker submission code.
6. Determine whether the broker call was attempted.
7. Determine whether any orders were accepted, rejected, rounded away, or skipped.
8. Inspect post-trade artifacts and execution audit outputs.
9. Inspect GitHub Actions logs or local run logs if present.
10. Produce a causal chain with exact stop point and evidence.
11. If the root cause is clear and fixable, implement the smallest safe observability patch.
12. Run targeted validation for touched code or reporting behavior.

## EVIDENCE TO REVIEW

Review what exists for the incident. Prefer direct artifacts over inference.

- `outputs/latest/latest_run.json`
- `outputs/latest/operator_summary.json`
- `outputs/runs/<run_id>/...`
- `execution_audit.json`
- `execution_results.json`
- `trading_day_summary.json`
- intended orders artifacts
- pretrade validation artifacts
- reconciliation artifacts
- post-trade snapshots
- canonical position refresh artifacts
- `.github/workflows/*.yml`
- `daily_quant_report.py`
- `scripts/execute_alpaca_orders.py`
- broker adapter files under `brokers/` or `paper/`
- GitHub Actions run logs
- local run logs under `logs/` if present

## DELIVERABLE

Use exactly this structure:

1. Executive Summary
2. Root Cause
3. Evidence Reviewed
4. Causal Chain
5. Files Touched or Proposed
6. Validation Run
7. Risks / Assumptions
8. Recommended Next Hardening Step

Required content:
- what happened
- exact root cause
- confidence level
- whether this was a true execution failure vs expected no-trade condition
- artifact and log evidence with precise file paths and key fields
- explicit statement of what remains unproven, if anything

## IMPORTANT

- Do not stop at "no trades occurred." State the precise mechanical reason execution did or did not happen.
- Distinguish between business outcome and reporting outcome. A run can fail after partial broker submission.
- If execution actually ran but downstream artifacts imply zero execution, classify that as a secondary observability issue.
- If evidence does not prove the answer cleanly, state that and propose the smallest deterministic artifact or reporting fix needed to remove the ambiguity going forward.
