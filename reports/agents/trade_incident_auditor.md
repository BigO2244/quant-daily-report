# Trade Incident Auditor

## What This Agent Is

The Trade Incident Auditor is an internal operating guide for diagnosing failed, partial, or ambiguous trading runs in a deterministic way. It is used to reconstruct the governed execution path from planner output through broker submission and post-trade reporting.

## When To Use It

Use this guide when any of the following occur:

- expected trades did not execute
- execution partially occurred
- summaries show zero execution but logs suggest otherwise
- pre-trade or reconciliation status is unclear
- a workflow ran but the broker outcome is unknown
- operators need a repo-standard incident report and evidence package

## Trigger Conditions

Start an incident audit when at least one of these conditions is true:

- a scheduled or manual trading workflow failed
- `latest_run.json` and `operator_summary.json` disagree
- intended orders exist but `execution_results.json` shows zero submissions
- execution payload is missing for a run that reached the execution stage
- broker reject or exception appears in logs
- post-trade artifacts are incomplete or misleading

## Root-Cause Taxonomy

Use one primary label and one optional secondary label.

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

## Expected Output Structure

Every completed audit should produce or conform to this structure:

1. Executive Summary
2. Root Cause
3. Evidence Reviewed
4. Causal Chain
5. Files Touched or Proposed
6. Validation Run
7. Risks / Assumptions
8. Recommended Next Hardening Step

The report should explicitly answer:

- Were trades proposed?
- Was execution attempted?
- Did the broker call happen?
- Were any orders accepted, rejected, rounded away, or skipped?
- Was the apparent outcome caused by true execution failure or by incomplete reporting?

## Escalation Guidance

Escalate when:

- artifacts are missing and logs are unavailable
- broker state after submission cannot be proven from preserved evidence
- there is disagreement between canonical state and incident artifacts
- a patch would alter trading behavior instead of observability
- multiple plausible causes remain after reviewing primary evidence

Escalation response:

- state what is proven
- state what is inferred
- rank the remaining plausible causes
- propose the smallest deterministic hardening step that would remove the ambiguity next time

## Recommended Next Hardening Step Philosophy

Prefer hardening that improves observability without changing strategy or execution policy.

Good hardening examples:

- make `execution attempted / not attempted / reason` explicit in summary artifacts
- persist blocker reason in operator and trading-day summaries
- persist partial submission counts before downstream handoff
- preserve broker reject classification in canonical audit artifacts

Avoid:

- changing portfolio construction to hide an operational issue
- changing workflow behavior before the failure point is proven
- broad refactors during incident response
