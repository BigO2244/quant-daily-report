# Operations Guide

## Current Strategy State

- `Caerus Polaris` / `caerus_polaris` is the current paper execution control.
- `Caerus Orion` / `caerus_orion` is the primary shadow candidate only.
- `Caerus Lyra` / `caerus_lyra` is the secondary shadow challenger only.
- `SPY` remains the benchmark.
- Shadow is model-portfolio based and artifact-only; it is not broker-authoritative execution state.

## Daily Shadow Automation

- After successful precompute, `scripts/cron_precompute.sh` calls `scripts/run_shadow_candidates_daily.sh`.
- The shadow wrapper writes to:
  - `outputs/shadow_candidates/YYYY-MM-DD/`
  - `outputs/shadow_candidates/performance/`
- Failures are logged to `logs/shadow_YYYY-MM-DD.log` and swallowed.
- Shadow generation must never block production paper execution.

## Table of Contents
- [Reconciliation Failure Recovery](#reconciliation-failure-recovery)
  - [Overview](#overview)
  - [Auto-Bootstrap Feature](#auto-bootstrap-feature)
  - [Configuration](#configuration)
  - [Behavior](#behavior)
  - [Monitoring](#monitoring)
  - [Manual Intervention](#manual-intervention)
  - [Troubleshooting](#troubleshooting)

---

## Reconciliation Failure Recovery

### Overview

The daily workflow performs **pretrade reconciliation** before executing any orders. This reconciliation compares the canonical model snapshot (our expected positions) against the actual broker positions to ensure they match.

**When reconciliation fails:**
- The workflow detects position drift between model and broker
- **No trades are sent** (safety-first behavior)
- An execution email is sent with detailed drift information
- Optionally, the system can **auto-bootstrap** to recover for the next run

### Auto-Bootstrap Feature

Auto-bootstrap automatically refreshes the canonical model snapshot from the broker's current positions when pretrade reconciliation fails. This prevents "stuck" days where the workflow repeatedly fails due to persistent drift.

**Key Properties:**
- ✅ **Safe:** Never places orders when drift is detected
- ✅ **Controlled:** Disabled by default; opt-in via repository variable
- ✅ **Auditable:** Creates marker files and detailed execution email
- ✅ **Self-healing:** Next run should pass reconciliation and resume trading

### Configuration

The auto-bootstrap feature is controlled by the **`AUTO_BOOTSTRAP_ON_RECON_FAIL`** repository variable.

#### Enable Auto-Bootstrap

1. Go to **Repository Settings** → **Secrets and variables** → **Actions** → **Variables**
2. Create a new variable (or update if exists):
   - **Name:** `AUTO_BOOTSTRAP_ON_RECON_FAIL`
   - **Value:** `1` (or `true`)
3. Save

#### Disable Auto-Bootstrap (default)

Set the variable to `0` or delete it entirely. When disabled, reconciliation failures will cause the workflow to exit with status 2 (failure).

### Behavior

#### When Auto-Bootstrap is DISABLED (default)

```
Pretrade Reconciliation → FAIL (exit code 2)
↓
Workflow FAILS
↓
Execution email NOT sent (no payload created)
↓
Operator must manually bootstrap via workflow_dispatch:
  - Input: bootstrap_model_ledger_from_broker = true
```

#### When Auto-Bootstrap is ENABLED

```
Pretrade Reconciliation → FAIL (exit code 2)
↓
Auto-bootstrap step activates
↓
Canonical snapshot refreshed from broker
↓
Execution email sent with:
  - ⚠️ Recon failure banner
  - Detailed drift info (missing positions, qty mismatches)
  - Auto-recovery confirmation
↓
Workflow succeeds (exit code 0)
↓
Next scheduled run should pass reconciliation
```

### Monitoring

#### Execution Email (Recon Failure)

When reconciliation fails and auto-bootstrap triggers, the execution email will show:

**Text Email:**
```
Mode: ALPACA
Trade Date: 2026-03-04
Execution Status: HALTED — PRETRADE RECONCILIATION FAILED — AUTO BOOTSTRAP TRIGGERED

======================================================================
⚠️  NO TRADES SENT — PRETRADE RECONCILIATION FAILED  ⚠️
======================================================================

AUTO-RECOVERY ACTION TAKEN:
✓ Canonical model snapshot auto-refreshed from broker positions
✓ Next scheduled run should pass reconciliation
✓ Normal trading will resume once positions sync

RECONCILIATION DETAILS:
• Verdict: FAIL

Missing in Broker (model expected these positions):
  - AAPL
  - TSLA

Quantity Mismatches:
  Ticker | Broker Qty | Model Qty | Diff
  ------ | ---------- | --------- | ----
  MSFT   | 100        | 120       | -20.00
```

**HTML Email:**
- Prominent yellow warning banner at top
- Green success box showing auto-recovery actions
- Table of position diffs with color-coded mismatches

#### Workflow Run Artifacts

Check GitHub Actions run artifacts for:

1. **`auto_bootstrap.json`** (in run directory):
   ```json
   {
     "auto_bootstrap_triggered": true,
     "reason": "pretrade_recon_fail",
     "timestamp": "2026-03-04T14:35:22Z"
   }
   ```

2. **Recon Report** (`outputs/broker/recon_pretrade_YYYY-MM-DD.json`):
   ```json
   {
     "phase": "pretrade",
     "verdict": "FAIL",
     "diffs": {
       "missing_in_broker": ["AAPL", "TSLA"],
       "missing_in_model": [],
       "qty_mismatches": [...]
     }
   }
   ```

3. **Updated Canonical Snapshot** (`outputs/paper_state/canonical_positions.json`):
   - `reason`: `"bootstrap_from_broker"`
   - `timestamp_utc`: updated timestamp

### Manual Intervention

#### When to Manually Bootstrap

Even with auto-bootstrap enabled, you may need manual intervention if:
- Auto-bootstrap itself fails (exits with code 1)
- Persistent drift occurs across multiple days
- You want to force a snapshot refresh outside the scheduled run

#### How to Manually Bootstrap

1. Go to **Actions** tab → **daily-alpaca-paper** workflow
2. Click **Run workflow** (top right)
3. Set inputs:
   - **report_date:** (leave empty for today, or specify YYYY-MM-DD)
   - **bootstrap_model_ledger_from_broker:** ✅ **true**
4. Click **Run workflow**

This will:
- Fetch current broker positions
- Write canonical snapshot
- Exit **without** generating orders
- Update cache for next run

### Troubleshooting

#### Problem: Auto-bootstrap triggers every day

**Diagnosis:** Persistent position drift (broker vs model never sync)

**Possible Causes:**
1. External trades being placed outside the workflow
2. Broker API returning stale/incorrect positions
3. Ledger corruption or timestamp issues

**Resolution:**
1. Check broker dashboard for unexpected trades
2. Review `outputs/broker/recon_pretrade_*.json` for consistent drift patterns
3. Manually review canonical snapshot vs broker positions
4. Consider temporarily disabling auto-bootstrap and investigating root cause
5. Check environment variables: `RECON_MAX_QTY_DIFF` (default 0.0 = strict)

#### Problem: Auto-bootstrap fails (exit code 1)

**Diagnosis:** Bootstrap step itself errored

**Possible Causes:**
1. Broker API unavailable or returning errors
2. Credentials expired/invalid
3. File system permission issues

**Resolution:**
1. Check workflow logs for bootstrap error details
2. Verify Alpaca API credentials in repo secrets
3. Retry manually via workflow_dispatch
4. If persistent, disable auto-bootstrap and alert DevOps

#### Problem: Reconciliation still fails after auto-bootstrap

**Diagnosis:** Cache not updated or timing issue

**Possible Causes:**
1. Cache save step failed (check workflow logs)
2. New drift introduced between bootstrap and next run
3. Cache key mismatch

**Resolution:**
1. Check "Save updated canonical snapshot to cache" step logs
2. Manually trigger bootstrap again
3. Review cache artifacts in Actions cache dashboard
4. Clear cache and re-bootstrap if stale

#### Problem: Want to disable auto-bootstrap temporarily

**Resolution:**
1. Set `AUTO_BOOTSTRAP_ON_RECON_FAIL=0` in repo variables
2. Or delete the variable entirely
3. Next recon failure will exit with code 2 (fail fast)

---

## Additional Topics

(Future sections can be added here for other operational concerns)

---

## References

- Reconciliation implementation: `reconciliation.py`
- Bootstrap function: `bootstrap_model_ledger_from_broker()`
- Workflow definition: `.github/workflows/daily-alpaca-paper.yml`
- Execution email rendering: `paper/build_execution_email.py`
