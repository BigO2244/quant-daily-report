# Drawdown Recovery Runbook — 2026-07-28

Status: Deployed-observing candidate; paper only
Owner: Brett Olson
Related decision: `docs/governance/decision_records/ADR-001_portfolio_construction_strategy.md`

## Current Control State

- Live account: flat and held in cash.
- Live kill switch: engaged.
- Live rearm: prohibited by the recovery policy.
- Paper candidate: `weekly_rotation_guard_v1`.
- Shared daily precompute: unchanged.
- Paper execution engine: derives a weekly, factor-guarded target immediately
  before paper plan construction.

Do not copy paper recovery targets into `outputs/precompute/`. The shared bundle
remains the evidence source; the derived paper target belongs under
`outputs/paper_lane/recovery_targets/`.

## Why This Candidate

The broker-truth ledger, rather than model NAV, is the governing source for
realized account performance. Through 2026-07-27 it showed:

- paper: +4.517% since 2026-03-06, with a -6.205% maximum drawdown;
- live: -6.778% since 2026-06-23, with a -9.020% maximum drawdown.

The stored-target replay showed that daily target churn was the dominant
controllable failure mode. The selected weekly factor-guard candidate improved
return, drawdown, and turnover in the retained window, while remaining narrow
enough for a paper-only test.

## Daily Evidence

Review these artifacts after each paper session:

- `outputs/ledger/paper/daily_nav.csv` — broker-truth paper NAV;
- `outputs/paper_lane/recovery_targets/paper_recovery_targets_<DATE>.json` —
  weekly source and strictly-prior factor decision;
- `outputs/paper_lane/plans/live_pilot_plan_<DATE>.json` — exact paper plan;
- `outputs/paper_lane/runs/<RUN_ID>/audit/execution_target_attainment_<DATE>.json`
  — target versus achieved broker weights;
- `outputs/paper_lane/runs/<RUN_ID>/live_pilot_reconciliation.json` —
  execution completeness;
- `outputs/precompute/<DATE>/signals.json` — desired target-book turnover and
  concentration diagnostics.

Desired turnover is not executed turnover. Use `target_book_metrics` for the
desired target-book change and broker order/fill evidence for executed activity.

## Observation Gates

The candidate remains observation-only until all of these are true:

1. At least 20 forward paper sessions exist.
2. At least 10 sessions report `OK_TARGET_ATTAINED`.
3. No unresolved reconciliation, identity, stale-data, or factor-history gaps
   remain.
4. Broker-truth paper drawdown and turnover are reviewed against the prior daily
   baseline.
5. Brett explicitly approves the next stage.

Meeting these gates permits a review; it does not rearm live automatically.

## Immediate Stop Conditions

Disable the paper candidate and move paper to cash if any of the following
occurs:

- factor data is not strictly prior to the weekly decision;
- a paper recovery target is built from a different ISO week;
- live accepts a paper recovery policy;
- target-attainment evidence is absent after a submitted run;
- reconciliation is not clean and cannot be resolved;
- broker-truth drawdown breaches the operator's current stop threshold;
- identity validation does not pass.

Live remains killed and flat regardless of paper status.

## Disable and Hold Paper in Cash

Set `"enabled": false` in `config/paper_recovery_policy.json` and deploy that
change. The builder will then fail closed instead of creating another recovery
plan.

Disabling the policy does not itself liquidate existing paper positions. To hold
paper in cash, use the existing governed paper liquidation workflow, verify the
paper broker has zero positions and zero open orders, refresh the paper
broker-truth ledger, and retain the reconciliation evidence. Never substitute
the live account or live credentials while performing this step.

## Live Safety Verification

Before and after any deployment:

1. Confirm the live kill-state mirror reports `engaged: true`.
2. Confirm the live broker reports zero positions and zero open orders.
3. Confirm a live plan rejects `weekly_rotation_guard_v1`.
4. Confirm current shared targets fail the Orion identity check.
5. Confirm an unset, invalid, or greater-than-$500 live capital cap blocks.

Any failed check stops deployment or leaves live disabled.
