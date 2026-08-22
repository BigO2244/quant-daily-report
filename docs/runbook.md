# Operator Runbook

**System:** Caerus Quant — Daily Execution & Research System
**Author:** Brett Olson
**Last reviewed:** 2026-05-15

This runbook covers day-to-day operation of the Caerus trading system. It assumes you have access to the GitHub repository (Actions tab, Secrets/Variables), and optionally a local checkout.

Current deployment governance:

- `origin/main` is canonical deployable source.
- The scheduler VM is a deploy target and the production scheduler for daily
  paper execution through cron.
- GitHub daily precompute/live workflows are dispatch-only safety paths, not the
  normal scheduler.
- Standard source deployment is `commit -> push -> scripts/deploy.sh on VM -> attest`.
- Rollback is by git revert, push, then the same attested VM deployment command.
- Wave deployments are validated locally, deployed through git, then observed
  through runtime status artifacts before being considered fully settled.
- SCP is exception-only and must be reconciled back through git.
- See `docs/deployment_workflow.md` before changing deployment, cron, or VM
  source state.
- The next planned hardening phase is Phase 4: Artifact Governance +
  Operational Telemetry. It is backlog/planning only until individual FRs are
  promoted, and it must remain non-trading and non-execution by default.
- FR planning, history, and methodology are separated under `docs/governance/`:
  active work in `fr_active_backlog.md`, deployed/deferred history in
  `fr_registry.md`, and methodology in `fr_governance_model.md`.

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
9. [VM Cron, GitHub Actions, and Local Runs](#vm-cron-github-actions-and-local-runs)
10. [Escalation and Manual Intervention Points](#escalation-and-manual-intervention-points)

---

## Pre-Market Checklist

Review this before 9:35 AM ET each trading day.

### Strategy Naming

- `Caerus Polaris` = historical research baseline / operational comparison control
- `Caerus Orion` = sole PAPER execution authority and shadow-observed strategy
- `Caerus Lyra` = shadow-observed strategy plus the separately governed Live portfolio
- `SPY` = benchmark

Lyra Live uses independent owner authority, gates, state, and scheduling. It is
not the legacy FR-104 Live pilot and must not be diagnosed from Orion's PAPER
registry fields. See `docs/CURRENT_OPERATING_STATE.md` before making an
operating-status claim.

### 1. Confirm VM precompute completed (~7:00 AM ET)

- SSH to the VM and inspect `logs/precompute_<DATE>.log`.
- Confirm `outputs/precompute/<DATE>/contract.json` and companion bundle files exist.
- Confirm `outputs/workflow/<DATE>/precompute_bundle_validation.json` reports `status: OK`.
- Confirm `contract.json` is schema 2 and its `approved_target_hash` matches
  `paper_target_package.json`, `signals.json`, and
  `planned_execution_payload.json`.

### 2. Check research digest (~7:00 AM ET)

- Confirm `logs/research_<DATE>.log` and the latest digest artifact are present.
- Email digest failures are isolated and do not affect trading.

### 3. Verify execution recovery state is clean

- Inspect `outputs/workflow/<DATE>/execution_bundle_validation.json` after the
  9:35 AM execution phase.
- If `execution_self_heal.json` exists, confirm whether `execution_continued`
  is `true` or `false` and review `validation_failures`.

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

| Time (ET) | Scheduler | Phase | Key Steps |
|---|---|---|---|
| 1:00 AM | VM cron | Overnight agents | `scripts/cron_overnight.sh` writes overnight signals. |
| 6:30 AM | VM cron | Research digest | `scripts/cron_research.sh` writes research digest. |
| 7:00 AM | VM cron | Precompute | `scripts/cron_precompute.sh` evaluates all sleeves, seals one Orion Decision target, quarantines the legacy research frame, and then writes non-blocking shadow artifacts. |
| 9:35 AM weekdays | VM cron | Orion PAPER execution | `scripts/cron_execute.sh` validates the sealed target, self-heals if needed, applies fresh Risk/broker state, and creates exact orders from the same Decision hash. |
| 9:35 AM Tuesdays | VM managed cron | Lyra Live execution | `scripts/cron_lyra_live_portfolio.sh recurring` consumes the completed Monday-close Lyra target under its separate owner decision and fail-closed Live gates. |
| 10:00 AM | VM cron | Confirmation | `scripts/cron_confirm.sh` sends confirmation/reporting email. |
| 7:15 PM | VM cron | Broker truth | `scripts/cron_broker_ledger.sh` pulls the sole actual-PAPER NAV authority from Alpaca. |
| 7:45 PM | VM cron | Portfolio history | Canonical append-only portfolio history derives from the broker ledger and escalates freshness failures. |

GitHub daily precompute/live workflows are dispatch-only safety paths. They are
not the normal production scheduler.

---

## Expected Outputs from a Healthy Run

### After precompute shadow generation

- `outputs/shadow_candidates/<DATE>/caerus_polaris.json`
- `outputs/shadow_candidates/<DATE>/caerus_orion.json`
- `outputs/shadow_candidates/<DATE>/caerus_lyra.json`
- `outputs/shadow_candidates/<DATE>/comparison.json`
- `outputs/shadow_candidates/<DATE>/comparison.md`
- `outputs/shadow_candidates/performance/shadow_nav_series.csv`
- `outputs/shadow_candidates/performance/shadow_summary.json`
- `outputs/workflow/<DATE>/shadow_generate.json`
- `outputs/workflow/<DATE>/shadow_latest.json`
- `outputs/workflow/<DATE>/shadow_reconciliation.json`
- `outputs/workflow/<DATE>/shadow.json`
- `logs/shadow_<DATE>.log`

Operator note:
- `comparison.md` is the fastest daily shadow artifact to review
- broker context appendix, if present, is informational only
- shadow remains model-portfolio based and does not reflect broker-authoritative holdings
- shadow status artifacts are diagnostic and non-blocking

### Self-heal recovery and bundle validation

Execution validates the full precompute bundle before order execution. Required
files are:

- `outputs/precompute/<DATE>/contract.json`
- `outputs/precompute/<DATE>/daily_snapshot.json`
- `outputs/precompute/<DATE>/signals.json`
- `outputs/precompute/<DATE>/planned_execution_payload.json`
- `outputs/precompute/<DATE>/sleeve_evaluations.json`
- `outputs/precompute/<DATE>/paper_target_package.json`

The legacy allocator's proposed trades may exist only below the content-hashed
`research/growth_engine_v4/` subdirectory. They have no execution authority.

If validation fails, `scripts/cron_execute.sh` runs a self-heal precompute with
`SELF_HEAL_PRECOMPUTE_ONLY=1`. That recovery suppresses:

- precompute email
- shadow generation
- latest shadow publication
- shadow reconciliation

Recovery artifacts:

- `outputs/workflow/<DATE>/execution_bundle_validation.json`
- `outputs/workflow/<DATE>/execution_self_heal.json`
- `outputs/workflow/<DATE>/precompute_bundle_validation.json`
- `outputs/workflow/<DATE>/precompute_self_heal.json`

Operational interpretation:

- `execution_continued: true` means recovery produced a fully valid bundle.
- `execution_continued: false` means execution was intentionally halted.
- Missing required files in `validation_failures` are blocking.
- `recovery_attempt_count > 1` means repeated degraded recovery and should be reviewed.
- Shadow latest artifacts may be stale after self-heal because shadow side
  effects are intentionally suppressed; inspect the degraded-state flags rather
  than deleting latest artifacts.

FR-016 advisory semantic validation planning lives in
`docs/precompute_semantic_validation.md`. That document defines future
read-only checks for mixed-date evidence, malformed planned orders, surface
classification, suppressed side-effect visibility, and provenance. It does not
change the deployed blocking bundle validator or execution behavior.

Execution source and price-freshness semantics live in
`docs/execution_contract.md`. Cron-driven validated precompute execution should
use `planned_payload_exact`, `PREV_CLOSE`, `PRECOMPUTE_VALIDATED`, and
`precompute_bundle` provenance. The stale same-day open-price guard remains
fail-closed for explicit `rebuilt_from_signals` execution.

### Paper broker-read retry and escalation

`scripts/cron_execute.sh` is wrapped by a PAPER-only, lane-wide retry harness.
The first attempt runs immediately. A transient broker timeout, connection
failure, HTTP 408/429, or HTTP 5xx failure that occurs before any submission is
retried after 30 seconds, 1 minute, 5 minutes, and 1 hour. Each attempt reruns
the complete paper workflow so bundle validation, broker truth, market-hours,
and submission gates are evaluated from current state.

- Live-pilot execution is not retried by this harness.
- Authentication, authorization, configuration, and other permanent failures
  escalate immediately.
- Any attempt with a submitted order is never retried.
- An accepted `SUBMITTED_UNFILLED` run gets up to 24 read-only broker-status
  refreshes at five-second intervals. The original order IDs are observed; the
  submission lane is never invoked again. A fill converges the original run and
  execution pointer before confirmation, while exhaustion remains fail-closed.
- During a wait, the execution pointer remains `running` with a real run root,
  so the 10:00 confirmation does not mislabel the run as missing.
- Final exhaustion remains fail-closed, sends a direct escalation, and records
  `outputs/workflow/<DATE>/paper_execution_retry.json`.
- Any recovery after 10:00 ET invokes only the canonical trading-confirmation
  sender once; a dated idempotency artifact prevents duplicate late sends.

### After VM execution

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

### After VM confirmation

- Execution email received with trade details, position changes, and status
- Snapshot email received with daily portfolio HTML report

---

## How to Verify a Run Succeeded

### Via VM Cron

The VM cron is the normal production scheduler. Inspect these first for current
daily operations:

1. SSH to the VM and go to `~/quant-daily-report`.
2. Confirm git state if source drift is relevant:
   - `git status`
   - `git log -1 --oneline`
3. Inspect the relevant logs:
   - `logs/cron_precompute.log`
   - `logs/cron_execute.log`
   - `logs/cron_confirm.log`
   - `logs/shadow_<DATE>.log`
4. Scan for:
   - `[RECON] PRETRADE verdict: PASS` — reconciliation passed
   - `[ORDERS]` lines showing planned or sent orders
   - No `[ERROR]` lines
5. Inspect artifacts under `outputs/runs/<RUN_ID>/`, `outputs/broker/`, and
   `outputs/precompute/<DATE>/`.
6. For orchestration/recovery questions, inspect `outputs/workflow/<DATE>/`.

### Via GitHub Actions

GitHub daily precompute/live workflows are dispatch-only and should not be used
as evidence of the normal scheduled run unless an operator intentionally
triggered them.

For manually dispatched runs:

1. Go to the relevant workflow and click the most recent run.
2. Confirm jobs show green.
3. Download artifacts and inspect:
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

## VM Cron, GitHub Actions, and Local Runs

### When to Use VM Cron

- Normal production-like paper trading schedule.
- Daily precompute, execution, confirmation, shadow lane, and weekly review.
- Broker-authoritative dashboard refreshes and VM-hosted operational surfaces.

### When to Use GitHub Actions

- Manual dispatch-only recovery or diagnostics when explicitly selected.
- Bootstrap operations if the current recovery path requires workflow_dispatch.
- Audit trail for manually dispatched runs.
- Research digest or historical workflows when intentionally triggered.

### When to Use Local Runs

- Alpha report generation and local inspection
- Backtests and walk-forward analysis
- Debugging signal generation before a strategy change
- Smoke testing after a code change

### Key differences

| Concern | VM Cron | GitHub Actions | Local |
|---|---|---|---|
| Role | Normal scheduler | Dispatch-only recovery/diagnostics | Development and diagnostics |
| Canonical source | Fast-forwarded from `origin/main` | Checks out workflow commit | Current checkout |
| Credentials | VM `.env` and runtime env | GitHub Secrets | Manually exported env vars |
| Python environment | VM venv via `scripts/runtime_env.sh` | Fresh workflow venv | Local `.venv` |
| Artifact retention | VM disk and published surfaces | Actions artifacts/cache | Local disk only |
| Email sending | Cron/runtime env controlled | Workflow env controlled | Set `EMAIL_DRY_RUN=1` to prevent accidental sends |

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
