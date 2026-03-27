# Claude Audit Prompt: High-Confidence Workflow Hardening

You are performing a white-hat audit of a production-adjacent paper-trading workflow in the Caerus Quant repository.

Your job is not to defend the implementation. Your job is to find:

- logical flaws
- hidden coupling
- stale fallback paths
- race conditions
- backward-compatibility traps
- confirmation/reporting misrouting
- deployment / ops assumptions
- anything that could cause a Monday morning trading workflow to misfire, silently drift, or report the wrong state

Assume the business goal is high-confidence paper execution on a weekday schedule:

- `7:00 AM ET` precompute
- `9:35 AM ET` execution
- `10:00 AM ET` confirmation

## Background: Why This Change Set Exists

On Friday, March 27, 2026, the automated paper workflow stumbled in multiple ways:

1. Precompute succeeded, but the first `9:35 AM ET` execution attempt halted on a precompute-contract / mode-handoff failure.
2. A subsequent execution attempt halted on pretrade reconciliation `SELF_HEAL` instead of retrying reconciliation once and continuing if the follow-up result was `PASS` or `WARN`.
3. A later non-execution run overwrote `outputs/latest_run.json`.
4. The `10:00 AM ET` confirmation phase then looked at the wrong run root and skipped the true execution results.

The core design issue was that Phase 3 trusted the mutable global pointer `outputs/latest_run.json`, even though non-execution activity could overwrite it.

## Intended Design After This Change Set

The new model is:

- `outputs/latest_run.json` remains a generic "latest activity" pointer for dashboards and backward compatibility.
- Critical trading-phase handoff should use trade-date-scoped workflow pointers under:
  - `outputs/workflow/<trade_date>/execution.json`
- The execution path is the owner of the execution workflow pointer.
- Confirmation should resolve execution artifacts from the execution workflow pointer, not from `latest_run.json`.
- Pre-trade status email should prefer the execution workflow pointer, but can still fall back to `latest_run.json` only under tightly constrained compatibility rules.

## Files Changed In This Change Set

Audit these files closely:

- `core/run_pointer.py`
- `scripts/run_precomputed_alpaca_execution.py`
- `daily_trade_execution_email.py`
- `scripts/send_trading_confirmation_email.py`
- `scripts/cron_confirm.sh`
- `Tests/test_run_pointer.py`
- `Tests/test_daily_trade_execution_email.py`
- `Tests/test_execution_pipeline_integration.py`
- `Tests/test_run_precomputed_alpaca_execution.py`
- `Tests/test_run_precomputed_alpaca_execution_fast.py`

## Exact Behavioral Changes

### 1. `core/run_pointer.py`

New workflow-pointer layer was added:

- `WORKFLOW_POINTER_ROOT = "outputs/workflow"`
- trade-date-scoped pointer files at `outputs/workflow/<trade_date>/<stage>.json`
- valid stages currently include:
  - `precompute`
  - `execution`
  - `confirmation`

New functions:

- `workflow_stage_pointer_path(...)`
- `write_trade_stage_pointer(...)`
- `read_trade_stage_pointer(...)`
- `resolve_trade_stage_pointer(...)`

Second-pass hardening in this file:

- pointer writes now use an atomic temp-file + `os.replace(...)` pattern
- `latest_run.json` can now carry `workflow_stage`
- `resolve_trade_stage_pointer(...)` no longer falls back to `latest_run.json` solely on matching `trade_date`
- fallback now requires both:
  - matching `trade_date`
  - matching `workflow_stage`
- pointer timestamps no longer use `datetime.utcnow()`
- `is_pointer_fresh(...)` now compares timezone-aware UTC datetimes

Audit focus:

- whether the atomic-write pattern is sufficient on the current filesystem assumptions
- whether any caller still assumes the older looser fallback behavior
- whether `latest_run.json` is still too permissive as a compatibility layer

### 2. `scripts/run_precomputed_alpaca_execution.py`

Execution now writes an explicit execution workflow pointer.

New helper:

- `_write_execution_run_pointers(...)`

Behavior:

- On execution-run init, write:
  - `outputs/latest_run.json`
  - `outputs/workflow/<trade_date>/execution.json`
- On precompute-validation failure, update both pointers with failure status.
- On pretrade-reconciliation halt, update both pointers with failure status.
- On terminal execution completion, update both pointers with final status.

Also:

- `_init_run_root(...)` now takes explicit `run_id` instead of re-calling `get_run_id()`.
- The goal is to avoid pointer drift between init and finalization.
- Second pass:
  - latest-run writes now include `workflow_stage="execution"`
  - execution pointer / init metadata no longer hardcode `mode="PAPER"` inside the pointer helper
  - mode label is derived from canonical environment mode normalization

Audit focus:

- Are there any failure paths where the execution pointer is not written?
- Are there any cases where `latest_run.json` and the execution pointer could disagree in dangerous ways?
- Does writing the execution pointer at init introduce any new confirmation hazards if execution dies mid-run?
- Does the environment-derived mode label have any hidden ambiguity left?

### 3. `daily_trade_execution_email.py`

Pre-trade execution-status email now resolves payloads in this order:

1. execution workflow pointer for the trade date
2. `latest_run.json` only if:
   - `trade_date` matches
   - `workflow_stage == "execution"`
3. legacy `outputs/execution_email/<date>.json`

Intent:

- If a later non-execution run overwrites `latest_run.json`, the status email should still use the execution-phase run root when it exists.
- If the stage pointer is malformed, the email path should log the malformed pointer and continue to safer fallbacks.
- Trade-date resolution now uses ET-aware `current_et()` rather than local `date.today()`.

Audit focus:

- Is fallback order correct?
- Could this still select a stale or incorrect payload?
- Are there scenarios where the execution pointer exists but should not be trusted yet?
- Is the remaining `latest_run.json` compatibility path still too permissive?

### 4. `scripts/send_trading_confirmation_email.py`

Confirmation email now resolves `execution_results.json` strictly from:

- `read_trade_stage_pointer(trade_date, "execution")`

It no longer relies on `latest_run.json`.

Intent:

- If the execution workflow pointer is missing, that should be treated as a workflow failure, not silently redirected through a mutable latest pointer.
- If the execution workflow pointer is malformed, that should raise a clean runtime error.
- If the execution workflow pointer is still `status="running"`, confirmation should refuse to proceed.
- Trade-date resolution now uses ET-aware `current_et()` rather than local `date.today()`.

Audit focus:

- Is the strictness appropriate?
- Are there legitimate cases where confirmation would now fail even though a correct execution result exists elsewhere?
- Is `REPORT_DATE` resolution sufficient and safe?
- Is the `running`-pointer refusal sufficient to prevent partial / premature confirmation?

### 5. `scripts/cron_confirm.sh`

The shell wrapper now:

- resolves execution run root from the execution workflow pointer
- gates confirmation-email sending on `execution_results.json` under that run root
- verifies operator summary from the execution workflow pointer, not `latest_run.json`
- treats `status="running"` as in-progress and skips confirmation
- exits non-zero when confirmation is skipped because execution is still running or execution ended failed
- logs pointer-resolution stderr into the confirmation log instead of swallowing it
- sends failure-alert email without interpolating log content into Python source

Audit focus:

- shell quoting / robustness
- whether the helper can fail silently in bad ways
- whether the log messages are sufficiently diagnostic
- whether the confirmation wrapper still has any path that can silently skip the correct execution run
- whether the new exit semantics are correct for alerting / cron monitoring

## Tests Added / Updated

### `Tests/test_run_pointer.py`

Added coverage for:

- writing and reading execution-stage pointers
- workflow pointer path location
- preference of stage pointer over `latest_run.json`
- fallback behavior when stage pointer is missing
- no fallback when `latest_run.json` lacks a matching `workflow_stage`

### `Tests/test_daily_trade_execution_email.py`

Added coverage that:

- pre-trade status email prefers execution-stage pointer over `latest_run.json`
- stale `latest_run.json` for the wrong trade date is ignored
- trade-date resolution uses ET-aware `current_et()`

### `Tests/test_execution_pipeline_integration.py`

Added coverage that:

- trading confirmation email can resolve execution results from execution-stage pointer
- confirmation prefers execution-stage pointer over a conflicting `latest_run.json`
- confirmation fails cleanly when:
  - execution pointer exists but `execution_results.json` is missing
  - execution pointer is still `status="running"`
  - execution pointer JSON is malformed
- trade-date resolution uses ET-aware `current_et()`

### `Tests/test_run_precomputed_alpaca_execution.py`

Updated for new execution-pointer write dependency.

### `Tests/test_run_precomputed_alpaca_execution_fast.py`

Updated stubs for new execution-pointer writer import.

## Local Validation Already Run

These commands were run successfully:

```bash
python3 -m py_compile core/run_pointer.py scripts/run_precomputed_alpaca_execution.py daily_trade_execution_email.py scripts/send_trading_confirmation_email.py
bash -n scripts/cron_confirm.sh
pytest -q Tests/test_run_pointer.py Tests/test_daily_trade_execution_email.py Tests/test_execution_pipeline_integration.py Tests/test_run_precomputed_alpaca_execution.py Tests/test_run_precomputed_alpaca_execution_fast.py
```

The focused pytest result was:

- `46 passed`

## Specific Audit Targets From Claude's First Review

The first external review identified 12 follow-up items. This change set is intended to address all 12.

Please explicitly verify whether each of these is now actually fixed:

1. `daily_trade_execution_email.py` no longer uses `resolve_trade_stage_pointer(...)` in the payload path.
2. `daily_trade_execution_email.py` fallback to `latest_run.json` now guards on matching `trade_date`.
3. `scripts/cron_confirm.sh` no longer interpolates log text into inline Python source for failure email.
4. Confirmation flow now rejects execution pointers still marked `running`.
5. Pointer writes are atomic enough to prevent torn reads from normal crash windows, and malformed pointer reads fail cleanly.
6. Execution pointer writer no longer hardcodes mode metadata inside the pointer helper.
7. `cron_confirm.sh` no longer swallows stderr from execution-pointer resolution.
8. `resolve_trade_stage_pointer(...)` fallback no longer treats any same-date `latest_run.json` as if it belonged to the requested stage.
9. Remaining `datetime.utcnow()` risk in this slice is cleaned up.
10. Confirmation cron semantics now surface failed executions via non-zero exit.
11. Missing test gap for "pointer exists but `execution_results.json` is missing" is closed.
12. Trade-date resolution across the affected scripts now uses the same ET-aware source.

## What I Want From Your Audit

Please review this as if you are the last serious engineering gate before Monday morning.

I want explicit findings on:

1. Any race condition or pointer inconsistency still possible between:
   - precompute
   - execution
   - confirmation
   - unrelated later runs

2. Any case where:
   - confirmation could still read the wrong execution run
   - pre-trade status email could still read the wrong payload
   - stage pointer could become stale or misleading

3. Any hidden coupling to:
   - `latest_run.json`
   - old `latest.json`
   - `REPORT_DATE`
   - current working directory
   - mutable repo-local state on the VM

4. Any regression or gap around:
   - failure states
   - partial execution
   - duplicate execution prevention
   - missing `execution_results.json`
   - missing stage pointer
   - backward compatibility

5. Any operational/design issue that would make this unsafe for Monday even if all focused tests are green.

## Review Style Requested

Please give:

1. Findings first, ordered by severity.
2. File and line references where possible.
3. Concrete failure scenarios, not just abstractions.
4. A short section on residual risks even if no major bugs are found.

Do not optimize for politeness. Optimize for catching what could still break.
