# Operator Runbook

**System:** Caerus Quant — Daily Execution & Research System
**Author:** Brett Olson
**Last reviewed:** March 2026

This runbook covers day-to-day operation of the Caerus trading system. It assumes you have access to the GitHub repository (Actions tab, Secrets/Variables), and optionally a local checkout.

---

## Table of Contents

1. [Pre-Market Checklist](#pre-market-checklist)
2. [Expected Daily Workflow Sequence](#expected-daily-workflow-sequence)
3. [Expected Outputs from a Healthy Run](#expected-outputs-from-a-healthy-run)
4. [How to Verify a Run Succeeded](#how-to-verify-a-run-succeeded)
5. [Common Failure Modes](#common-failure-modes)
6. [Recovery and Rollback Steps](#recovery-and-rollback-steps)
7. [Reconciliation Procedure](#reconciliation-procedure)
8. [What to Do If Broker State and Model State Diverge](#what-to-do-if-broker-state-and-model-state-diverge)
9. [GitHub Actions vs Local Runs](#github-actions-vs-local-runs)
10. [Escalation and Manual Intervention Points](#escalation-and-manual-intervention-points)

---

## Pre-Market Checklist

Review this before 9:35 AM ET each trading day.

### 1. Confirm Alpha Daily completed (~6:15 AM ET)

- Go to **Actions → Alpha Daily Run** and confirm the most recent scheduled run completed with a green checkmark.
- Check that `data/live_nav.csv` was committed by `github-actions` in the repo's commit history.
- If the alpha run failed, it does not block execution — but the alpha email will not be sent. Investigate separately.

### 2. Check research digest (~7:00 AM ET)

- Confirm **Actions → Research Digest — Nightly** ran and produced an artifact.
- Email digest failures are isolated and do not affect trading.

### 3. Verify canonical model snapshot exists

The `daily-alpaca-paper` workflow will fail in the "Verify canonical model snapshot exists" step if the cache is missing. This happens on the very first run or after cache eviction.

- Go to **Actions → Daily Alpaca Paper Run** → any recent successful run.
- Check that the `canonical-model-snapshot` artifact is present.
- If missing, manually bootstrap before market open (see [Bootstrap procedure](#manual-bootstrap-procedure) below).

### 4. Confirm Alpaca API credentials are valid

Run the smoke test manually if there is any doubt:

```bash
source .venv/bin/activate
export ALPACA_API_KEY_ID=...
export ALPACA_API_SECRET_KEY=...
export ALPACA_PAPER=1
python3 alpaca_smoke_test.py
```

Or trigger the workflow and watch the "Alpaca smoke test" and "Diag Alpaca auth" steps.

---

## Expected Daily Workflow Sequence

| Time (ET) | Workflow | Job | Key Steps |
|---|---|---|---|
| ~6:15 AM | `alpha_daily` | `alpha` | alpha_report.py → daily_alpha_run.py → commit live_nav.csv → email alpha report |
| ~7:00 AM | `research-digest` | `digest` | quant_research_agent/main.py → email digest |
| 9:35 AM | `daily-alpaca-paper` | `engine_run` | restore cache → smoke test → env check → daily_quant_report.py → auto-bootstrap if recon fails → upload artifacts |
| After engine_run | `daily-alpaca-paper` | `email` | download artifacts → send execution email → send snapshot email |

Both cron entries for each workflow handle DST shifts (EST vs EDT). Both entries fire every weekday, but one fires on the correct ET hour depending on the current UTC offset. The duplicate is harmless — the second fires 1 hour off-peak and will either be a no-op or produce a redundant run depending on how `REPORT_DATE` resolves.

---

## Expected Outputs from a Healthy Run

### After `alpha_daily`

- `data/live_nav.csv` — updated and committed to repo
- `outputs/runs/<RUN_ID>/reports/` — alpha report artifacts
- `outputs/runs/<RUN_ID>/snapshots/live_nav.csv` — NAV snapshot
- `outputs/runs/<RUN_ID>/meta.json` — run metadata
- Alpha report email received at `EMAIL_RECIPIENT`

### After `daily-alpaca-paper` (engine_run job)

- `outputs/runs/<RUN_ID>/logs/ci_alpaca_run.log` — full execution log
- `outputs/runs/<RUN_ID>/reports/quant_report_<DATE>.html` — HTML daily report
- `outputs/runs/<RUN_ID>/reports/trade_snapshot_<DATE>.txt` — text trade snapshot
- `outputs/runs/<RUN_ID>/broker/` — broker state files
- `outputs/runs/<RUN_ID>/meta.json` and `manifest.json`
- `outputs/execution_email/<DATE>.json` — execution email payload
- `outputs/paper_state/canonical_positions.json` — updated canonical snapshot
- `outputs/broker/recon_pretrade_<DATE>.json` — pre-trade reconciliation report
- `signals/<DATE>.json` — daily signal snapshot
- Orders sent to Alpaca (or blocked if recon failed)

### After `daily-alpaca-paper` (email job)

- Execution email received with trade details, position changes, and status
- Snapshot email received with daily portfolio HTML report

---

## How to Verify a Run Succeeded

### Via GitHub Actions

1. Go to **Actions → Daily Alpaca Paper Run** and click the most recent run.
2. Confirm `engine_run` and `email` jobs both show green.
3. Expand the **"Run daily quant execution"** step and scan for:
   - `[RECON] PRETRADE verdict: PASS` — reconciliation passed
   - `[ORDERS]` lines showing planned or sent orders
   - No `[ERROR]` lines
4. Download the **`alpaca-paper`** artifact and inspect:
   - `outputs/runs/<RUN_ID>/meta.json` — verify `mode: "alpaca"` and correct `report_date`
   - `outputs/runs/<RUN_ID>/logs/ci_alpaca_run.log` — check exit status and key log lines
   - `outputs/paper_state/canonical_positions.json` — verify positions match Alpaca dashboard

### Via execution email

The email subject line is `Daily Quant Snapshot <DATE>`. The email body shows:

- **Mode** — should be `ALPACA`
- **Execution Status** — should be `OK` or `SHADOW` (never `HALTED` on a clean run)
- **Positions** table — lists current holdings with weights and P&L
- **Orders** section — shows buys and sells executed

If the email subject shows `HALTED — PRETRADE RECONCILIATION FAILED`, see [Reconciliation Procedure](#reconciliation-procedure).

---

## Common Failure Modes

### 1. Pre-trade reconciliation fails (exit code 2)

**Symptom:** `engine_run` job fails or is marked "recovered." Execution email shows `HALTED — PRETRADE RECONCILIATION FAILED`. Recon report at `outputs/broker/recon_pretrade_<DATE>.json` shows `verdict: FAIL`.

**Cause:** Canonical model snapshot and Alpaca positions have drifted. Common causes: manual trades in Alpaca outside the workflow, cache eviction (snapshot lost), or first run with no snapshot.

**Resolution:** See [Reconciliation Procedure](#reconciliation-procedure).

### 2. Missing canonical model snapshot

**Symptom:** Workflow fails at "Verify canonical model snapshot exists" step with message: `[RECON][ERROR] Missing outputs/paper_state/canonical_positions.json`.

**Resolution:** Run manual bootstrap (see [Manual Bootstrap Procedure](#manual-bootstrap-procedure)).

### 3. Alpaca API credentials invalid or expired

**Symptom:** Diag step fails with HTTP 403 or `[DIAG][PY] ERROR`. Smoke test exits non-zero.

**Resolution:**
1. Verify keys in Alpaca dashboard — regenerate if needed.
2. Update GitHub Secrets: `Settings → Secrets and variables → Actions → ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY`.
3. Re-run the workflow.

### 4. yfinance data download fails

**Symptom:** Log shows yfinance timeout or empty DataFrame errors. VIX fetch may fail; system falls back to VIX = 25.0 (ELEVATED). Signal generation may produce empty results if price data is unavailable.

**Resolution:**
- This is typically transient (Yahoo Finance rate limits or maintenance). Re-run the workflow later in the day.
- If persistent, check whether `yfinance` package needs an update: `pip install --upgrade yfinance`.

### 5. Email not received

**Symptom:** Workflow `email` job is green but no email arrived.

**Checks:**
1. Verify `EMAIL_STRICT=0` (default) in workflow env — SMTP failures are non-fatal.
2. Check `email` job logs for `[EMAIL][DRY_RUN]` — if `EMAIL_DRY_RUN=1` in repo variables, emails are skipped.
3. Verify `EMAIL_SENDER`, `EMAIL_APP_PASSWORD`, `EMAIL_RECIPIENT` are set correctly in Secrets.
4. Check spam folder.

### 6. Alpha daily NAV commit fails

**Symptom:** `alpha_daily` fails at "Commit updated live NAV ledger" step.

**Cause:** Usually a git conflict or permissions issue.

**Resolution:** Run the workflow again. If persistent, check that `GITHUB_TOKEN` has `contents: write` permission (already in the workflow definition).

### 7. Research digest pipeline fails

**Symptom:** `research-digest` job shows failure at "Run digest pipeline" step.

**Impact:** None on trading — research digest is fully isolated.

**Resolution:** Check `ANTHROPIC_API_KEY` and `FRED_API_KEY` secrets are set. Inspect the `digest.log` artifact. Failures here are non-blocking.

---

## Recovery and Rollback Steps

### Manual Bootstrap Procedure

Use when the canonical snapshot is missing or you want to force a fresh sync from broker:

1. Go to **Actions → Daily Alpaca Paper Run**
2. Click **Run workflow** (top right, `workflow_dispatch`)
3. Set:
   - `report_date`: leave blank for today, or enter `YYYY-MM-DD`
   - `bootstrap_model_ledger_from_broker`: ✅ **true**
   - `force_bootstrap_flat`: leave unchecked unless the Alpaca account is genuinely at 0 positions (all cash after a reset)
4. Click **Run workflow**
5. Verify the run completes successfully
6. Verify `outputs/paper_state/canonical_positions.json` is present in the artifact

This run writes the canonical snapshot and exits without placing any orders. The next scheduled run will load this snapshot and proceed normally.

### Rollback to a Previous Run State

1. Go to **Actions → Daily Alpaca Paper Run** → find the last known-good run
2. Download the `canonical-model-snapshot` artifact
3. Place `canonical_positions.json` in `outputs/paper_state/` (locally or by triggering a bootstrap)
4. On next run, the snapshot is restored from the updated cache

### Re-running a Failed Day

1. Confirm the root cause is resolved (credentials, data, reconciliation)
2. Trigger **workflow_dispatch** on `daily-alpaca-paper` with `report_date` set to the failed date (if re-running historical) or leave blank for today
3. Monitor the "Run daily quant execution" step log

**Important:** Re-running for the same date when orders were already partially sent may result in duplicate or conflicting orders at Alpaca. Check Alpaca paper dashboard first and manually cancel any open orders before re-running.

---

## Reconciliation Procedure

### Understanding Reconciliation

Before placing any orders, `reconciliation.py` performs a **pre-trade reconciliation**:

1. Loads the canonical model snapshot from `outputs/paper_state/canonical_positions.json`
2. Fetches current positions from Alpaca API
3. Compares position-by-position (tickers and quantities)
4. Returns `PASS` if all positions match within `RECON_MAX_QTY_DIFF` tolerance (default 0.0 = strict)
5. Returns `FAIL` if any position is missing in broker, missing in model, or has a quantity mismatch

On `FAIL`: all orders are blocked. No trades are sent.

### Reading the Recon Report

Download the run artifact and open `outputs/broker/recon_pretrade_<DATE>.json`:

```json
{
  "phase": "pretrade",
  "verdict": "FAIL",
  "diffs": {
    "missing_in_broker": ["AAPL"],
    "missing_in_model": [],
    "qty_mismatches": [
      {"ticker": "MSFT", "broker_qty": 100, "model_qty": 120, "diff": -20.0}
    ]
  }
}
```

### Auto-Bootstrap (if enabled)

If `AUTO_BOOTSTRAP_ON_RECON_FAIL=1` is set as a repository variable:
- The workflow automatically bootstraps the canonical snapshot from broker on recon failure
- The execution email will show `HALTED — PRETRADE RECONCILIATION FAILED — AUTO BOOTSTRAP TRIGGERED` with the drift details
- The run is marked as "recovered" (exit 0)
- The next scheduled run should pass reconciliation

If auto-bootstrap is disabled (default), the workflow exits with code 2 and the operator must manually bootstrap.

### Enable / Disable Auto-Bootstrap

1. Go to **Settings → Secrets and variables → Actions → Variables**
2. Set `AUTO_BOOTSTRAP_ON_RECON_FAIL` = `1` to enable, `0` to disable
3. Delete the variable to disable (same effect as `0`)

---

## What to Do If Broker State and Model State Diverge

### Scenario: Positions exist in Alpaca that the model doesn't know about

Cause: Manual trade placed outside the workflow, or a previous bootstrap was incomplete.

Action:
1. Check Alpaca paper dashboard to see actual open positions
2. Decide whether to accept the broker state (more common) or restore model state (rarely needed)
3. If accepting broker state: run manual bootstrap with `bootstrap_model_ledger_from_broker=true`
4. If the Alpaca account was manually reset to flat (0 positions): run bootstrap with **both** `bootstrap_model_ledger_from_broker=true` and `force_bootstrap_flat=true`

### Scenario: Model holds position X but Alpaca shows 0 shares of X

Cause: Position was closed manually in Alpaca, or an order failed silently.

Action:
1. Run bootstrap to sync canonical snapshot with current broker state
2. Review execution emails for the days around the discrepancy to identify when it started

### Scenario: Quantity mismatch (model says 120 shares, broker says 100)

Cause: Partial fill that wasn't recorded, or a prior run's order got partially executed.

Action:
1. Check Alpaca order history for the ticker
2. Run bootstrap to adopt broker's actual position as canonical

### Persistent drift (auto-bootstrap triggers every day)

Cause: Something is placing trades outside the workflow, or the broker API is returning stale data.

Diagnosis:
1. Check `outputs/broker/recon_pretrade_*.json` files for repeated patterns
2. Review Alpaca dashboard for any activity not matching workflow execution times
3. Check `RECON_MAX_QTY_DIFF` env var — if set to 0.0 (default), even tiny fractional share differences fail recon

Resolution:
1. Temporarily disable auto-bootstrap (`AUTO_BOOTSTRAP_ON_RECON_FAIL=0`)
2. Investigate root cause in Alpaca activity feed
3. Re-enable after root cause is resolved

---

## GitHub Actions vs Local Runs

### When to Use GitHub Actions

- All production execution (paper trading orders must go through the scheduled workflow)
- Bootstrap operations to sync canonical snapshot
- Audit trail — each Actions run produces a timestamped, versioned artifact
- Research digest (requires ANTHROPIC_API_KEY and FRED_API_KEY in Secrets)

### When to Use Local Runs

- Alpha report generation and local inspection
- Backtests and walk-forward analysis
- Debugging signal generation before a strategy change
- Smoke testing after a code change

### Key differences

| Concern | GitHub Actions | Local |
|---|---|---|
| Canonical snapshot | Restored from Actions cache; saved back after run | Read from / written to `outputs/paper_state/` directly |
| Credentials | Loaded from GitHub Secrets | Set as environment variables manually |
| Python environment | Freshly created venv per run | Local `.venv` (macOS-only binary) |
| Artifact retention | 30–90 days in Actions; can download | Local disk only |
| Email sending | Controlled by `EMAIL_DRY_RUN` variable | Set `EMAIL_DRY_RUN=1` to prevent accidental sends |

### Local shadow run (no orders, no email)

```bash
source .venv/bin/activate
REPORT_DATE=$(TZ=America/New_York date +%F) \
  MODE=shadow TRADING_MODE=shadow \
  EMAIL_DRY_RUN=1 \
  python3 daily_quant_report.py
```

### Checking what the model would do today

```bash
source .venv/bin/activate
python3 scripts/alpha_report.py --apply-costs --cost-bps 25
```

This generates the alpha attribution report in `outputs/alpha_report/` without touching the broker or canonical snapshot.

---

## Escalation and Manual Intervention Points

### Situations requiring manual action

| Situation | Action |
|---|---|
| First-ever run | Run manual bootstrap via workflow_dispatch before first scheduled run |
| Actions cache evicted (snapshot missing) | Run manual bootstrap before next scheduled run |
| Alpaca credentials rotated | Update GitHub Secrets immediately; verify smoke test passes |
| Recon fails 2+ consecutive days | Disable auto-bootstrap, investigate root cause, manually reconcile |
| Model/broker drift of > 5 positions | Do not rely on auto-bootstrap — manually review Alpaca activity, then bootstrap |
| yfinance fails for 2+ consecutive days | Check if yfinance version needs updating; consider running delayed |
| VIX data unavailable | System falls back to VIX = 25.0 (ELEVATED), 75% scale — acceptable short-term |
| Run produces 0 positions (empty signals) | Review `ci_alpaca_run.log` for gate-filter messages; may indicate data quality issue or market conditions where no tickers pass all gates |
| Strategy change deployed | Run manual bootstrap after deployment to reset canonical snapshot to current broker state before next scheduled run |

### Things you should NOT do manually

- Do **not** place trades directly in the Alpaca dashboard while the automated workflow is active — this creates drift.
- Do **not** delete or manually edit `outputs/paper_state/canonical_positions.json` — use the bootstrap procedure instead.
- Do **not** force-push to `main` during market hours without bootstrapping afterward.
- Do **not** change `ALPACA_API_KEY_ID` or `ALPACA_API_SECRET_KEY` without verifying the new credentials via smoke test first.

### Monitoring

There is no active monitoring dashboard at this time. Monitoring is done via:
- GitHub Actions run status (email notifications from GitHub if runs fail)
- Execution emails sent to `EMAIL_RECIPIENT`
- Reviewing `outputs/ic_monitor/ic_summary.json` for signal health alerts
- Reviewing `outputs/vix_regime/regime_current.json` for current regime
