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
- `outputs/runs/<RUN_ID>/broker/posttrade_positions.json`
- `outputs/runs/<RUN_ID>/broker/posttrade_account_snapshot.json`
- `outputs/runs/<RUN_ID>/broker/orders_<DATE>.csv`

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

Key fields to check:

- `duplicate_guard_status`
- `post_execution_recon_status`
- `affected_symbols`
- `repair_suggestions`
- `duplicate_fill_suspicions_count`

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

## When To Pause The Next Trading Day

Pause or halt the next trading day if any of the following are true:

- `post_execution_recon_status = UNEXPECTED_SHORT`
- `post_execution_recon_status = MANUAL_INTERVENTION_REQUIRED`
- `DRIFT_DETECTED` and the affected symbols are not understood before market open
- duplicate protection fired, but broker/order artifacts do not clearly prove what happened
- `duplicate_fill_suspicions_count > 0` and broker positions still look wrong after review

## Minimum Incident Checklist

- Capture the `RUN_ID`
- Save the dashboard screenshot or note the `Execution Integrity` panel values
- Archive:
  - `operator_summary.json`
  - `trading_day_summary.json`
  - `broker/recon_posttrade_<DATE>.json`
  - `broker/posttrade_positions.json`
  - `broker/orders_<DATE>.csv`
- Record whether the next trading day is safe to continue or should be paused
