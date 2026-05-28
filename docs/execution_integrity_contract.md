# Execution Integrity Contract

## Purpose

FR-031 adds a broker-authoritative audit guardrail after paper execution writes
its normal artifacts. The guardrail makes order disappearance, buy-leg
suppression, response-count drift, continuation scope, and cash-target drift
visible to operators without changing strategy selection, order routing, broker
credentials, cron, or scheduler behavior.

## Runtime Boundary

The validator is additive in its first version:

- It reads existing execution artifacts.
- It writes `outputs/runs/<run_id>/audit/execution_integrity.json`.
- It updates `operator_summary.json` with compact integrity status fields.
- It does not block order submission.
- It does not change execution routing, continuation behavior, or recovery
  eligibility.

Execution source, price basis, freshness scope, and fail-closed execution
boundaries are defined in `docs/execution_contract.md`. FR-031 audits the
artifacts produced under that contract; it does not choose the execution source.

## Inputs

The validator compares these artifacts when available:

- `outputs/runs/<run_id>/broker/intended_orders_<trade_date>.json`
- `outputs/runs/<run_id>/execution_payload.json`
- `outputs/runs/<run_id>/execution_results.json`
- `outputs/runs/<run_id>/operator_summary.json`

Explicit buy-only continuation may pass a source intended-orders artifact via
`continuation_intended_orders_path`; in that case the validator treats BUY
orders as the continuation-scoped comparable set.

## Output

The audit artifact contains:

- `status`: `OK`, `WARN`, or `FAIL`
- counts for intended, payload, submitted, accepted, rejected, and broker
  response records
- buy/sell splits for intended and payload orders
- pending buy count and continuation metadata
- missing intended orders, missing BUY orders, and unexpected payload orders
- explicit block/defer reasons
- target and achieved cash weights
- cash drift warning flag
- structured findings with severity, code, and message

## Key Invariants

- Intended orders should reconcile to payload orders unless an explicit
  block/defer/continuation scope explains the difference.
- Intended BUY orders must not disappear silently.
- Pending BUYs with zero submitted BUYs must not be treated as clean success.
- Execution eligible count should match `execution_payload.trades` unless an
  explicit exception reason exists.
- Submitted, accepted, rejected, and broker response counts should reconcile.
- Material cash target drift should surface as an operator warning.
- Continuation runs should identify mode, side, source, and source artifact.

## Operator Surface

`operator_summary.json` receives:

- `execution_integrity_status`
- `execution_integrity_findings`
- `execution_integrity_artifact`

The operator log and execution health banner include the compact integrity
status so a `WARN` or `FAIL` audit is visible without opening the full JSON
artifact.
