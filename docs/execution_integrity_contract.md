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
- When `allow_fractional_shares=true`, sub-1-share orders that satisfy the
  minimum-notional and risk/capital checks are valid execution candidates. They
  must not be reclassified as zero-share drops by downstream executable-order
  filtering or shadow-order construction.
- When `allow_fractional_shares=true`, fractional quantities must survive into
  executable orders, intended/shadow orders, and broker payloads unless an
  explicit safeguard blocks the order.
- When sell orders exist, the buy leg must be rebuilt after sell submission
  from refreshed broker account/cash/buying-power/position state and confirmed
  sell proceeds. The execution path must not blindly replay stale precompute
  buy rows after a sell leg.
- Partial, rejected, timed-out, or otherwise unresolved sells release only
  confirmed cash and buying power. Pending or unconfirmed sell proceeds must
  not be assumed for buy sizing.
- Buy-only/no-sell runs preserve existing exact-plan behavior.
- Risk cash target, buying-power checks, cash reserve safeguards,
  min-notional filters, idempotency guards, and broker asset validation remain
  binding after post-sell rebudgeting.
- Execution eligible count should match `execution_payload.trades` unless an
  explicit exception reason exists.
- Submitted, accepted, rejected, and broker response counts should reconcile.
- Material cash target drift should surface as an operator warning.
- Continuation runs should identify mode, side, source, and source artifact.

## Fractional Trading Semantics

`allow_fractional_shares=true` is intended system behavior for the paper
broker. Fractional quantities are expected to survive target-weight conversion,
rebalance sizing, turnover-risk scaling, capital-budget clipping, executable
trade filtering, shadow-order construction, and Alpaca submission as long as
all existing safeguards still pass.

The 2026-06-09 fractional-trading audit found that historical underdeployment
was caused by downstream whole-share normalization, not by portfolio
construction, cash-target policy, or strategy intent. Historical artifact
review measured 53 impacted days, 230 zero-share drops, approximately $93.7k of
aggregate buy capacity lost, and approximately 16.72% average underdeployment
on impacted days. Commit `e249f61` preserves fractional shares in paper
execution. See `docs/governance/operational_lessons.md` for the operational
lesson.

## Post-Sell Buy Rebudgeting Semantics

`planned_payload_exact` remains the canonical execution source for validated
precompute runs, but sell-leg execution changes available buy capital. If a run
contains sell orders, the buy-side executable plan must be recomputed after the
sell leg using confirmed post-sell cash, buying power, account equity, broker
positions, existing target weights, and existing safeguards.

The 2026-06-09 execution audit found that stale precomputed buy rows were
replayed after sells. Ending cash remained approximately $2,196.67 versus a
5% risk cash target of approximately $530.09. Rebuilding buys after confirmed
sell proceeds would have increased buy notional by approximately $1,666.58.
Commit `aaf5961` deploys the post-sell rebudgeting fix.

The rebudget artifact is:

- `outputs/runs/<run_id>/broker/post_sell_rebudget_<trade_date>.json`

It should include:

- pre-sell cash, buying power, and equity
- sell orders submitted and sell-phase status
- confirmed sell proceeds
- post-sell cash, buying power, and equity
- buy budget before safeguards
- broker/cash safeguard budget
- risk cash target and risk-target budget
- buy budget after safeguards
- original precomputed buy notional
- recomputed and final buy notional
- final buy orders submitted
- ending cash and ending cash versus risk target
- reason codes when rebudgeting is skipped or degraded

Operator interpretation:

- `status=REBUILT` means the buy leg was recomputed from confirmed post-sell
  state.
- `reason_codes` should explain `NO_SELLS`, partial/unconfirmed sells,
  missing prices, exhausted buy budget, or any validation block.
- A rebudgeted run may still leave cash above target if min-notional filters,
  buying-power checks, cash reserve, risk target, max-trade limits, asset
  validation, or unresolved sell state prevents additional safe buys.

## Operator Surface

`operator_summary.json` receives:

- `execution_integrity_status`
- `execution_integrity_findings`
- `execution_integrity_artifact`

The operator log and execution health banner include the compact integrity
status so a `WARN` or `FAIL` audit is visible without opening the full JSON
artifact.

## Target-Attainment Reconciliation

Broker-state reconciliation and target-attainment reconciliation answer
different questions:

- Broker-state reconciliation answers: "Did broker positions match the expected
  post-execution broker state?"
- Target-attainment reconciliation answers: "Did the actual portfolio match the
  risk-adjusted intended target portfolio?"

`OK_RECONCILED` is necessary but not sufficient. A run can reconcile cleanly to
the broker-submitted/expected state while still leaving material cash or
exposure drift versus the risk-adjusted target. The 2026-06-09 run is the
baseline example: posttrade reconciliation was `OK_RECONCILED`, but actual cash
was 20.8417% versus a 5.0% risk cash target.

The target-attainment artifact is:

- `outputs/target_attainment/<trade_date>/target_attainment_<trade_date>.json`

The read-only CLI is:

```bash
python3 -m research.target_attainment --date YYYY-MM-DD
```

The artifact records the portfolio chain:

- target portfolio
- risk-adjusted portfolio
- intended orders
- executed orders
- broker holdings
- actual portfolio

Key metrics include:

- target and actual cash percentage
- cash gap
- target and actual gross exposure percentage
- exposure gap
- undeployed capital and excess cash
- deployment efficiency and deployment score
- attainment score
- intended versus executed notional
- concentration drift
- top drift contributors
- reason codes
- confidence

The 2026-06-09 target-attainment baseline is:

- target cash: 5.0%
- actual cash: 20.8417%
- cash gap: 15.8417%
- deployment efficiency: 83.3245%
- attainment score: 37.15
- excess cash: approximately $1,669.68

This layer is observability only. It does not alter execution behavior, model
logic, target weights, risk controls, broker submission, or cron timing.
