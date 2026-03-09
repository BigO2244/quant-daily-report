# Execution Pipeline Smoke Test Guide

## Purpose

This guide provides commands for locally validating the hardened execution pipeline without live broker submission.

## Prerequisites

- Python virtual environment activated
- All dependencies installed (`pip install -r requirements.txt`)
- Environment variables configured (see `.env.example`)

## Smoke Test Steps

### 1. Generate Planner Artifacts (Dry Run)

Generate execution payload without broker submission:

```bash
# Set paper mode and dry-run
export TRADING_MODE=shadow
export EMAIL_DRY_RUN=true

# Run planner for today's date
python daily_quant_report.py --mode paper

# Or specify a date
python daily_quant_report.py --mode paper --report-date 2024-01-15
```

**Verify:**
- `outputs/latest_run.json` was created
- `outputs/runs/<run_id>/execution_payload.json` exists
- `outputs/runs/<run_id>/operator_summary.json` exists
- Operator summary shows `planner_completed: true`

### 2. Inspect Latest Run Pointer

```bash
cat outputs/latest_run.json | python -m json.tool
```

**Expected fields:**
- `run_id`: unique identifier
- `run_root`: path to `outputs/runs/<run_id>/`
- `trade_date`: YYYY-MM-DD
- `mode`: PAPER, SHADOW, etc.

### 3. Inspect Execution Payload

```bash
# Using run_id from latest_run.json
RUN_ID=$(python -c "import json; print(json.load(open('outputs/latest_run.json'))['run_id'])")
cat outputs/runs/$RUN_ID/execution_payload.json | python -m json.tool
```

**Verify:**
- `status`: One of NO_ACTION, READY, HALTED
- `halt_reason`: Present if status is HALTED
- `trades`: Array of proposed orders
- `executable_trades_count`: Count of valid trades

### 4. Inspect Operator Summary

```bash
cat outputs/runs/$RUN_ID/operator_summary.json | python -m json.tool
```

**Verify:**
- `pretrade_status`: Matches execution_payload.json status
- `proposed_trades_count`: Total trades before filters
- `executable_trades_count`: Trades passing filters
- `planner_completed`: true
- `executor_completed`: false (not yet run)

### 5. Run Executor in Safe Mode (Mock Broker)

**Option A: Shadow Mode (No Broker Submission)**

```bash
export TRADING_MODE=shadow
python scripts/execute_alpaca_orders.py
```

This will validate orders but halt before broker submission due to mode check.

**Option B: Mock Broker Test**

```bash
# Run unit tests that mock broker
pytest Tests/test_execution_pipeline_hardening.py::TestExecutionResults -v
```

**Verify Console Output:**
- `[EXECUTION_SUMMARY]` line with counts
- `[OPERATOR_SUMMARY]` line with final status
- No actual orders submitted to broker

### 6. Inspect Execution Results

```bash
cat outputs/runs/$RUN_ID/execution_results.json | python -m json.tool
```

**Verify:**
- `status`: EXECUTED, HALTED, or SKIPPED_DUPLICATE
- `submitted_count`: Orders that passed validation
- `accepted_count`: Orders accepted by broker
- `rejected_count`: Orders rejected
- `rejected_reasons`: Array of rejection reasons (if any)

### 7. Inspect Updated Operator Summary

```bash
cat outputs/runs/$RUN_ID/operator_summary.json | python -m json.tool
```

**Verify:**
- `executor_completed`: true
- `submitted_count`, `accepted_count`, `rejected_count`: Match execution_results.json
- `skipped_duplicate`: Shows if execution was skipped

### 8. Run Confirmation Email (Dry Run)

```bash
export EMAIL_DRY_RUN=true
python scripts/send_trading_confirmation_email.py
```

**Verify Console Output:**
- `[TRADING_CONFIRMATION] dry-run enabled; skipping send`
- `[OPERATOR_SUMMARY]` line shows `confirmation_email=False` (dry run)

### 9. Full Pipeline Validation

Run comprehensive tests:

```bash
# Run all hardening tests
pytest Tests/test_execution_pipeline_hardening.py -v

# Run with coverage
pytest Tests/test_execution_pipeline_hardening.py --cov=core --cov=scripts --cov-report=term

# Run specific test category
pytest Tests/test_execution_pipeline_hardening.py::TestOperatorSummary -v
```

## Validation Checklist

After completing smoke test, verify:

- [ ] `latest_run.json` points to correct run_root
- [ ] `execution_payload.json` has normalized status (NO_ACTION, READY, or HALTED)
- [ ] `operator_summary.json` created after planner
- [ ] `operator_summary.json` updated after executor
- [ ] Invalid orders rejected with clear reasons
- [ ] `execution_results.json` captures rejection details
- [ ] Confirmation email includes run_id, mode, and counts
- [ ] Duplicate execution skipped (if rerun)
- [ ] Machine-readable log lines present: `[PRETRADE_SUMMARY]`, `[EXECUTION_SUMMARY]`, `[OPERATOR_SUMMARY]`

## Common Issues

### Issue: execution_payload.json Missing

**Symptom:** Executor fails with `MISSING_EXECUTION_PAYLOAD`

**Fix:**
1. Run planner first: `python daily_quant_report.py --mode paper`
2. Check `latest_run.json` exists
3. Verify `run_root` path is correct

### Issue: Operator Summary Not Created

**Symptom:** `operator_summary.json` missing after planner

**Fix:**
1. Check planner completed successfully
2. Verify `run_root` exists
3. Check logs for errors during summary write

### Issue: All Orders Rejected

**Symptom:** `submitted_count=0`, `rejected_count=N`

**Fix:**
1. Check `rejected_reasons` in `execution_results.json`
2. Common reasons:
   - `missing_ticker`: Trade missing symbol
   - `invalid_side`: Side not BUY/SELL
   - `non_positive_qty`: Quantity <= 0

### Issue: Duplicate Execution Warning

**Symptom:** `status=SKIPPED_DUPLICATE` on second run

**Expected Behavior:** This is correct! Idempotency guard prevents duplicate submission.

**Override (if needed):** `python scripts/execute_alpaca_orders.py --force-resubmit`

## Production Validation

Before enabling live execution:

1. Run smoke tests in shadow mode
2. Verify all artifacts created
3. Check operator summary aligns across stages
4. Confirm no legacy execution_email path usage
5. Review rejected_reasons for any systematic issues
6. Test duplicate execution guard
7. Validate confirmation email format

## Environment Variables

```bash
# Trading mode
export TRADING_MODE=paper  # or: shadow, alpaca

# Email control
export EMAIL_DRY_RUN=true  # Skip actual email sends

# Broker credentials (paper trading)
export APCA_API_BASE_URL=https://paper-api.alpaca.markets
export ALPACA_API_KEY_ID=your_paper_key
export ALPACA_API_SECRET_KEY=your_paper_secret

# Date override (optional)
export REPORT_DATE=2024-01-15
```

## Next Steps

After successful smoke test:

1. Review operator_summary.json shape
2. Verify status alignment across artifacts
3. Test with small real orders in paper mode
4. Monitor logs for [PRETRADE_SUMMARY], [EXECUTION_SUMMARY], [OPERATOR_SUMMARY]
5. Audit any rejected orders
6. Validate confirmation email clarity
