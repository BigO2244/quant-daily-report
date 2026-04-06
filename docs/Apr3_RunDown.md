# Apr 3 Run Down

Date: 2026-04-03

## What happened

- Precompute completed successfully for Friday, April 3, 2026.
- Phase 2 execution did start at 9:35 AM ET.
- No trades were sent.
- The executor halted on `stale_prices detected (last_price_date=2026-04-02)`.
- The underlying reason was holiday handling: April 3, 2026 was Good Friday, but the local market calendar incorrectly treated it as an open NYSE session.
- The stale-price guard prevented the system from trading on a closed market, which is why no bad execution occurred.

## Why the emails looked confusing

- The execution run crashed before it finalized its terminal artifacts.
- That left the execution workflow pointer stuck at `running`.
- Phase 3 confirmation then skipped with the "Execution still running" warning.
- The pre-trade execution email fell back to the legacy `outputs/execution_email/<date>.json` payload, which still showed `PLANNED`.
- Result: the email trail looked like "precompute built, execution still running, trade execution planned" even though the real path was "execution attempted, holiday misclassified, stale-price halt, pointer left running".

## Code updates made

- `paper/trading_calendar.py`
  - Replaced the static holiday stub with deterministic market-holiday logic.
  - Added Good Friday, Juneteenth, MLK Day, Presidents Day, Memorial Day, Labor Day, Thanksgiving, and observed fixed-date holidays.
  - April 3, 2026 now resolves as `MARKET_CLOSED_DAY`, with next open on Monday, April 6, 2026.

- `scripts/run_precomputed_alpaca_execution.py`
  - Added terminal exception handling for mid-execution failures.
  - On safe pre-execution halts like `[HALT] stale_prices...`, the script now:
    - writes a terminal `HALTED` execution payload
    - updates the execution workflow pointer out of `running`
    - writes the legacy execution-email payload as `HALTED`
    - releases the same-day execution lock

- `daily_trade_execution_email.py`
  - If an execution-stage pointer exists but its `execution_payload.json` is missing, the email flow no longer falls back to the stale legacy `PLANNED` payload.
  - It now resolves the pointed path and emits a synthetic `HALTED / MISSING EXECUTION PAYLOAD` payload instead.

- Tests added or updated
  - `Tests/test_trading_calendar.py`
  - `Tests/test_daily_trade_execution_email.py`
  - `Tests/test_run_precomputed_alpaca_execution.py`

## Smoke test run

I ran targeted smoke validation on the updated code.

1. Compile smoke

- Command:
  - `python3 -m py_compile paper/trading_calendar.py daily_trade_execution_email.py scripts/run_precomputed_alpaca_execution.py Tests/test_trading_calendar.py Tests/test_daily_trade_execution_email.py Tests/test_run_precomputed_alpaca_execution.py`
- Result:
  - Passed

2. Holiday classification smoke

- Result:
  - `{'trading_day': False, 'next_day': '2026-04-06', 'reason': 'MARKET_CLOSED_DAY', 'next_open': '2026-04-06T09:30:00-04:00'}`

3. Execution-email fallback smoke

- Result:
  - Pointer with missing payload now resolves to the execution-stage path, not legacy `PLANNED`
  - Returned payload:
    - `payload_status = HALTED`
    - `payload_reason = MISSING EXECUTION PAYLOAD`

4. Stale-price exception cleanup smoke

- Result:
  - `exit_code = 1`
  - `lock_exists = False`
  - `payload_status = HALTED`
  - `email_status = HALTED`
  - `pointer_status = failed_pre_execution`

## Validation gap

- Local `pytest` is still not reliable in this workspace because the local `.venv` hangs on `import pandas` during collection.
- Because of that, I validated with:
  - `py_compile`
  - targeted import-smoke scripts
  - targeted executor-smoke harnesses

## Current state

- This section reflects the state immediately after the Apr 3 incident.
- By the Apr 6 addendum below, these files were deployed to the scheduler host and the missed trading day was recovered intraday.

## Recommended next step

After the Apr 6 recovery:

1. Commit and push the scheduler fixes to GitHub so the VM can track a real git-backed source of truth.
2. Reconcile the scheduler host to that commit instead of relying on SCP overlays.
3. Confirm the next holiday or closed-session path produces:
   - `MARKET_CLOSED_DAY`
   - no stale-price execution attempt
   - no stuck `running` pointer
   - no misleading `PLANNED` fallback email

## Apr 6 addendum

Date: 2026-04-06

### What happened

- The 9:35 AM ET paper execution missed again, but this time the reason was the scheduler host still running the old holiday calendar and old execution/email cleanup code.
- The stale remote calendar treated Friday, April 3, 2026 as a trading day.
- On Monday, April 6, 2026, the executor therefore tried to fetch prior-close data for `2026-04-03`, which had no market data.
- Validation dropped the whole actionable set as `missing_prev_closes` and halted with `After dropping missing-priced/blocked tickers, no targets remain.`

### Recovery actions completed

- Deployed these local fixes to the scheduler host:
  - `paper/trading_calendar.py`
  - `scripts/run_precomputed_alpaca_execution.py`
  - `daily_trade_execution_email.py`
- Verified on the VM that `prev_trading_day("2026-04-06") == "2026-04-02"`.
- Cleared the stale same-day execution lock.
- Re-ran Phase 2 once; that exposed a stale 7:00 AM precompute bundle whose `signals.json` still had `asof_date = 2026-04-03`.
- Re-ran Phase 1 precompute on the VM under the fixed calendar.
- Verified the rebuilt bundle now had `asof_date = 2026-04-02`.
- Re-ran Phase 2 execution successfully.
- Re-ran Phase 3 confirmation so operator emails and artifacts matched the recovered execution.

### Final execution outcome

- Recovery run id: `2026-04-06T103216-0400_4e8eab6`
- Orders submitted: `5`
- Orders accepted: `5`
- Orders filled immediately in sell phase: `3`
- Buy orders submitted after sells completed: `2`
- Latest execution pointer:
  - `outputs/latest_run.json -> status = success`
- Workflow execution pointer:
  - `outputs/workflow/2026-04-06/execution.json -> status = success`
- Confirmation rerun:
  - `scripts/cron_confirm.sh` completed successfully at `2026-04-06T14:33:13Z`

### Orders sent on recovery run

- `SELL USB 5`
- `SELL GM 3`
- `SELL VZ 4`
- `BUY CL 6`
- `BUY MCD 1`

### Current operational state

- The scheduler host is now using the fixed holiday logic.
- Pre-execution halts now release the same-day lock and write terminal artifacts instead of leaving execution stuck at `running`.
- The legacy execution email path no longer falls back to stale `PLANNED` status when an execution-stage pointer exists but its payload is missing.
- April 6, 2026 is no longer a missed paper-trading day; execution was recovered intraday.
