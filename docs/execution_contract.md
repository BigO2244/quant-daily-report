# Execution Source Contract

## Purpose

This document defines the operator-facing execution source and price freshness
contract for Caerus paper execution. It exists because a valid precompute bundle
is not sufficient unless the downstream runner records which execution source,
price basis, and freshness scope it used.

The contract is documentation and observability only. It does not change broker
submission, order routing, position sizing, strategy selection, cron timing, or
stale-price guard behavior.

## Source Taxonomy

### `planned_payload_exact`

`planned_payload_exact` means execution uses
`outputs/precompute/<DATE>/planned_execution_payload.json` as the canonical
source of executable trades.

Required behavior:

- `scripts/cron_execute.sh` validates the precompute bundle before execution.
- The runner uses `planned_execution_payload.trades`.
- The run records `execution_source=planned_payload_exact`.
- The run preserves the planned payload price basis and `pricing_asof`.
- The run does not rebuild executable trades from signals.
- If the planned payload includes sell orders, the buy leg is rebuilt after
  sell submission from confirmed post-sell broker state and existing target
  weights. This is a capital-availability rebudget, not a model/signal rebuild.
- If the planned payload is buy-only/no-sell, exact-plan buy behavior is
  preserved.
- The run does not enter same-day open-price freshness validation solely because
  the planned payload uses prior-close prices.

Fail-closed boundaries:

- Missing or malformed `planned_execution_payload.json` fails closed.
- Trade-date mismatch fails closed.
- Non-`PLANNED` payload status fails closed.
- Declared trade count mismatch fails closed.
- Malformed trade rows or missing planned prices fail closed.
- The runner must not silently fall back to `rebuilt_from_signals`.

### `rebuilt_from_signals`

`rebuilt_from_signals` means execution derives executable trades from the
signals/snapshot path at execution time instead of using the exact planned
payload.

Required behavior:

- The run records `execution_source=rebuilt_from_signals`.
- Same-day open-market price freshness is required.
- Stale same-day prices remain fail-closed.
- This path remains available only as an explicit/manual fallback or governed
  recovery path; cron-driven validated precompute execution should default to
  `planned_payload_exact`.

Fail-closed boundaries:

- Any fetched open price row with `price_date < run_date` must halt with
  `stale_prices`.
- Missing same-day open-price evidence must not be treated as a clean success.

## Price Basis Semantics

| Field | Meaning |
|---|---|
| `planning_price_basis=PREV_CLOSE` | Planned trades were sized from prior-close prices in the validated precompute bundle. |
| `pricing_asof=<DATE>` | The date of the planned pricing evidence. For prior-close execution this is usually the previous market close. |
| `execution_price_requirement=PRECOMPUTE_VALIDATED` | Exact planned-payload execution relies on the validated precompute bundle contract. |
| `execution_price_requirement=SAME_DAY_OPEN_FRESHNESS` | Rebuilt/open-market execution requires same-day open-price freshness. |
| `price_freshness_scope=precompute_bundle` | Freshness is bounded by precompute bundle validation and planned payload provenance. |
| `price_freshness_scope=open_market_fetch` | Freshness is bounded by same-day open-market price fetch evidence. |

## Operator Provenance Fields

Each execution run should make these fields visible in `execution_payload.json`,
operator summaries, timeline artifacts, or helper output when available:

- `execution_source`
- `planning_price_basis`
- `pricing_asof`
- `execution_price_requirement`
- `price_freshness_scope`
- `submitted_count`
- `accepted_count`
- `rejected_count`
- `execution_integrity_status`
- `execution_integrity_findings`
- `terminal_status`
- `post_sell_rebudget`
- `post_sell_rebudget_artifact_path`

The lifecycle timeline artifacts provide the run narrative:

- `outputs/runs/<RUN_ID>/execution_timeline.json`
- `outputs/runs/<RUN_ID>/execution_timeline.md`

For older runs, `scripts/rebuild_execution_timeline.py --run-id <RUN_ID>` can
rebuild those timeline artifacts from existing run artifacts only.

## Integrity And Reconciliation

Execution integrity is an additive post-submit audit. It must not weaken stale
price guards or change order routing.

Expected operator interpretation:

- `OK`: observed artifacts reconcile under the current contract.
- `WARN`: execution may have completed, but the operator should review findings.
- `FAIL`: artifacts violate an execution integrity invariant and must not be
  interpreted as clean success.

`cash_target_drift` remains a warning until post-submit/fill/reconciliation
evidence proves whether it is expected pending-fill drift or an accounting
problem. Do not suppress it globally to make operator output green.

Broker-state reconciliation and target-attainment reconciliation are separate
operator questions. `OK_RECONCILED` means broker positions match the expected
post-execution broker state; it does not prove the actual portfolio reached the
risk-adjusted target portfolio. Use
`outputs/target_attainment/<DATE>/target_attainment_<DATE>.json` or:

```bash
python3 -m research.target_attainment --date YYYY-MM-DD
```

to inspect target cash versus actual cash, target gross exposure versus actual
gross exposure, deployment efficiency, excess cash, attainment score, and top
drift contributors.

## 2026-05-28 Example

On 2026-05-28, precompute produced a valid prior-close planned payload for
`trade_date=2026-05-28`, but cron execution rebuilt from signals because exact
planned-payload mode was still opt-in. That entered same-day open-price
freshness checks and correctly halted on stale open-market prices.

The deployed recovery changed cron-driven validated precompute execution to
`planned_payload_exact` by default and recorded:

- `execution_source=planned_payload_exact`
- `planning_price_basis=PREV_CLOSE`
- `pricing_asof=2026-05-27`
- `execution_price_requirement=PRECOMPUTE_VALIDATED`
- `price_freshness_scope=precompute_bundle`

The manual paper rerun submitted 13 orders and Alpaca accepted all 13. The
stale-price guard remains intact for `rebuilt_from_signals`.

## 2026-06-09 Execution Integrity Updates

Two execution-layer integrity fixes are deployed under the existing source
contract:

- Commit `e249f61` preserves fractional quantities when
  `allow_fractional_shares=true`; downstream executable-order and shadow-order
  construction must not floor valid fractional buys or top-ups.
- Commit `aaf5961` rebuilds buy orders after confirmed sell proceeds when a
  sell leg exists; the runtime writes `post_sell_rebudget_<DATE>.json`.

These fixes do not change cron timing, alpha/model logic, target weights,
95%/5% risk exposure policy, stale-price guards, broker credentials, or broker
cash/buying-power safeguards.

The same June 9 observability wave added target-attainment reconciliation under
commits `81a0468` and `5663313`. It is read-only monitoring. It records whether
the actual portfolio attained the risk-adjusted target portfolio after
execution, but it does not change order generation or broker submission.
