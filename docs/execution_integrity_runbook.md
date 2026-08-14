# Execution Integrity Runbook

## Purpose

Use this runbook when the paper trading workflow reports duplicate-submission protection, post-execution drift, unexpected shorts, or other execution-integrity issues.

This is an operator response guide only. It does not change trading logic.

## Where To Look

Primary operator surfaces:

- Dashboard: `Execution Integrity` panel
- Console/logs: `[EXECUTION_HEALTH] ...` banner
- Run summary: `outputs/runs/<RUN_ID>/operator_summary.json`
- Trading-day summary: `outputs/runs/<RUN_ID>/trading_day_summary.json`
- Latest run pointer: `outputs/latest_run.json`

Primary broker audit artifacts:

- `outputs/runs/<RUN_ID>/broker/recon_posttrade_<DATE>.json`
- `outputs/runs/<RUN_ID>/broker/post_sell_rebudget_<DATE>.json`
- `outputs/runs/<RUN_ID>/broker/posttrade_positions.json`
- `outputs/runs/<RUN_ID>/broker/posttrade_account_snapshot.json`
- `outputs/runs/<RUN_ID>/broker/orders_<DATE>.csv`

Primary deployment-attainment artifacts:

- `outputs/operational_drag/<DATE>/operational_drag.json`
- `outputs/target_attainment/<DATE>/target_attainment_<DATE>.json`

Duplicate-protection artifacts:

- Per-run replay lock: `outputs/runs/<RUN_ID>/broker/orders_<DATE>.csv`
- Same-day sent ledger: `outputs/orders_sent/orders_sent.csv`

## Status Meanings

### `post_execution_recon_status`

- `OK_RECONCILED`
  Broker post-trade positions match the expected post-execution state.

- `DRIFT_DETECTED`
  Broker state differs from expected state, but no unexpected short was found.
  Common cases:
  - missing expected position
  - extra broker position
  - quantity mismatch

- `UNEXPECTED_SHORT`
  Broker has at least one negative position quantity.
  Treat this as an incident requiring manual review before the next trading day.

- `MANUAL_INTERVENTION_REQUIRED`
  The system could not complete the comparison reliably from local artifacts.
  Treat this as an incident until reviewed.

### `duplicate_guard_status`

- `CLEAR`
  No duplicate-submission guard fired.

- `BLOCKED_SAME_DAY_LOCK`
  The in-process Alpaca path refused to submit because same-day local sent-ledger records already existed.

- `BLOCKED_DUPLICATE_SUBMISSION`
  The standalone executor refused to submit because orders were already recorded for the run or trade date.

- `REMOTE_IDEMPOTENT_REPLAY`
  Broker-side idempotency detected a replay or remote duplicate condition. No new submission should have occurred for those orders.

## What To Check First

1. Open `outputs/latest_run.json` and identify the current `RUN_ID` and `run_root`.
2. Inspect the dashboard `Execution Integrity` panel.
3. Inspect `outputs/runs/<RUN_ID>/operator_summary.json`.
4. Inspect `outputs/runs/<RUN_ID>/trading_day_summary.json`.
5. If drift exists, inspect `outputs/runs/<RUN_ID>/broker/recon_posttrade_<DATE>.json`.

Before those run-level checks, verify the morning authority line:

- `outputs/precompute/<DATE>/contract.json` has `schema_version: 2` and
  `authority_model: orion_single_sealed_target_v1`.
- `approved_target_hash` matches across `contract.json`,
  `paper_target_package.json`, `signals.json`, and
  `planned_execution_payload.json`.
- `planned_execution_payload.json` has an empty `trades` list,
  `precompute_execution_authority: false`, and
  `exact_orders_deferred_to_0935: true`.
- The 09:35 plan carries the same `approved_target_hash` and the exact
  authorizer binds the `paper_target_package.json` file hash.

Any mismatch is an authority-line incident. Do not bypass the bundle gate or
manually copy a target into the 09:35 plan.

Key fields to check:

- `duplicate_guard_status`
- `post_execution_recon_status`
- `affected_symbols`
- `repair_suggestions`
- `duplicate_fill_suspicions_count`
- `post_sell_rebudget`
- `post_sell_rebudget_artifact_path`

If sell orders were present, also inspect
`outputs/runs/<RUN_ID>/broker/post_sell_rebudget_<DATE>.json`:

- `sell_phase_status`
- `confirmed_sell_proceeds`
- `buy_budget_before_safeguards`
- `buy_budget_after_safeguards`
- `original_precomputed_buy_notional`
- `recomputed_buy_notional`
- `final_submitted_buy_notional`
- `final_buy_orders_submitted`
- `estimated_ending_cash_vs_risk_target`
- `ending_cash_vs_risk_target`
- `reason_codes`

Also build or inspect target-attainment reconciliation:

```bash
python3 -m research.target_attainment --date YYYY-MM-DD
```

Key fields:

- `summary.target_cash_pct`
- `summary.actual_cash_pct`
- `summary.cash_gap_pct`
- `summary.target_gross_exposure_pct`
- `summary.actual_gross_exposure_pct`
- `summary.exposure_gap_pct`
- `summary.deployment_efficiency_pct`
- `summary.deployment_score`
- `summary.attainment_score`
- `summary.excess_cash`
- `execution.intended_notional`
- `execution.executed_notional`
- `top_drift_contributors`
- `reason_codes`
- `confidence`

## How To Use The Paper Repair Helper

Default to latest run:

```bash
python3 scripts/print_paper_repair_actions.py
```

Specific run:

```bash
python3 scripts/print_paper_repair_actions.py --run-root outputs/runs/<RUN_ID>
```

Specific trade date:

```bash
python3 scripts/print_paper_repair_actions.py --run-root outputs/runs/<RUN_ID> --trade-date YYYY-MM-DD
```

The helper is read-only. It only prints recommended paper repair actions from the post-trade reconciliation artifact.

## Response Guide

### If `duplicate_guard_status != CLEAR`

- Confirm no new orders were submitted in `execution_results.json` or `broker/orders_<DATE>.csv`.
- Verify whether the duplicate block was expected:
  - rerun / replay protection
  - same-day duplicate protection
  - broker-side idempotent replay
- Do not force a second submission unless you have confirmed the first submission did not occur.

### If `post_execution_recon_status = DRIFT_DETECTED`

- Review `affected_symbols` in:
  - dashboard panel
  - `operator_summary.json`
  - `broker/recon_posttrade_<DATE>.json`
- Determine whether the drift is:
  - missing expected broker position
  - extra broker position
  - quantity mismatch
- Review `broker/orders_<DATE>.csv` and `posttrade_positions.json` before the next run.

### If `post_execution_recon_status = UNEXPECTED_SHORT`

- Treat as high priority.
- Review:
  - `broker/recon_posttrade_<DATE>.json`
  - `broker/posttrade_positions.json`
  - `broker/orders_<DATE>.csv`
- Use the repair helper output and `repair_suggestions` to identify the flattening action needed.
- Do not ignore the issue or allow it to roll silently into the next trading day.

### If `post_execution_recon_status = MANUAL_INTERVENTION_REQUIRED`

- Treat as a missing-artifact or incomplete-audit incident.
- Confirm whether `posttrade_positions.json` and `recon_posttrade_<DATE>.json` were written.
- If artifacts are missing or corrupt, pause before the next run and inspect the failed run logs.

### If a sell-leg run leaves excess cash

- Open `post_sell_rebudget_<DATE>.json`.
- Confirm `sell_phase_status` and `confirmed_sell_proceeds`.
- Compare `original_precomputed_buy_notional` to `recomputed_buy_notional`.
- Confirm fractional quantities appear in `final_buy_orders_submitted` when
  `allow_fractional_shares=true` and fractional targets are applicable.
- Confirm `ending_cash_vs_risk_target` moved toward zero.
- Review `reason_codes` before treating excess cash as intentional target cash.
- Do not assume proceeds from rejected, timed-out, or partially unresolved
  sells; only confirmed cash should release buy capacity.
- Confirm every submitted sell has a stable `alpaca_order_id` and that
  `order_lifecycle` or `sell_phase_poll_observations` show a terminal broker
  state. If sells remain `accepted`, `new`, or otherwise nonterminal after the
  recovery window, the buy leg must show an explicit skip reason
  (`sell_phase_timeout`, `sell_state_unresolved`, or
  `broker_status_refresh_failed`) and the run must not be treated as a clean
  execution.
- If Alpaca later shows filled sell orders that Caerus did not persist, treat
  the run as an execution-integrity incident: preserve artifacts, compare the
  broker fill timestamps to sell-phase polls, and do not regenerate
  confirmation email from stale local state without a broker-authoritative
  recovery pass.

### Daily deployment monitoring

For each paper execution day, confirm:

- `post_sell_rebudget_<DATE>.json` exists when sell orders were present.
- Fractional quantities survive into intended/shadow/final orders when
  `allow_fractional_shares=true` and fractional targets are applicable.
- Ending cash moved toward the 5% risk target unless explicit safeguards or
  missing evidence explain otherwise.
- `outputs/target_attainment/<DATE>/target_attainment_<DATE>.json` exists.
- Target cash versus actual cash and target gross exposure versus actual gross
  exposure are reviewed.
- Deployment efficiency and attainment score are reviewed.
- Current-date operational drag is decision-grade or carries explicit blockers.
- Posttrade reconciliation remains `OK_RECONCILED`.
- Rejected orders remain 0.
- Target-attainment reason codes explain any material excess cash,
  underdeployment, or target-weight drift.

## When To Pause The Next Trading Day

Pause or halt the next trading day if any of the following are true:

- `post_execution_recon_status = UNEXPECTED_SHORT`
- `post_execution_recon_status = MANUAL_INTERVENTION_REQUIRED`
- `DRIFT_DETECTED` and the affected symbols are not understood before market open
- duplicate protection fired, but broker/order artifacts do not clearly prove what happened
- `duplicate_fill_suspicions_count > 0` and broker positions still look wrong after review
- a sell-leg run has missing/corrupt `post_sell_rebudget_<DATE>.json` or
  unexplained excess cash versus the 5% risk target
- rejected orders are non-zero or reconciliation is not `OK_RECONCILED` after
  the post-sell rebudget path runs
- target-attainment shows material cash/exposure drift without an explicit
  reason code explaining the constraint or missing input

## Minimum Incident Checklist

- Capture the `RUN_ID`
- Save the dashboard screenshot or note the `Execution Integrity` panel values
- Archive:
  - `operator_summary.json`
  - `trading_day_summary.json`
  - `broker/recon_posttrade_<DATE>.json`
  - `broker/post_sell_rebudget_<DATE>.json`
  - `broker/posttrade_positions.json`
  - `broker/orders_<DATE>.csv`
  - `outputs/target_attainment/<DATE>/target_attainment_<DATE>.json`
  - `outputs/operational_drag/<DATE>/operational_drag.json`
- Record whether the next trading day is safe to continue or should be paused
