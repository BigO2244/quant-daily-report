# Alpaca Workflow Close-Out

Last updated: 2026-03-16

## Scope of Fixes Completed

- Daily Alpaca GitHub Actions schedule now targets the intended `9:35 AM America/New_York` window using a DST-aware UTC cron split.
- Broker preflight account status normalization now treats `ACTIVE` as healthy and no longer emits a false `account_status_not_active` warning.
- PDT-aware broker pretrade surfacing is now additive and conservative:
  - PDT risk fields are written into operator-facing summaries.
  - Affected sells may be deferred pre-submit when explicit broker fields indicate strong PDT pressure.
- Capital-budget clipping remains in place.
- Sell-before-buy sequencing remains in place.
- Broker reject classification remains in place.
- Duplicate submission guards remain in place.
- GitHub workflow run paths and email-producing paths were audited repo-wide.
- Dual-authority email sending in the daily Alpaca GitHub Actions cycle was removed.
- Email boolean governance now respects explicit false tokens deterministically.

## Daily Alpaca Workflow Authority Model

- The authoritative trading workflow is `.github/workflows/daily-alpaca-paper.yml`.
- Within that workflow:
  - `engine_run` is the authoritative artifact producer.
  - `execute_orders` is the authoritative order submission stage.
  - `email` is the authoritative sender for the GitHub Actions daily Alpaca cycle.
- In GitHub Actions, `engine_run` should generate artifacts and logs, not duplicate-send inline report emails.
- `EMAIL_INLINE_REPORTS=0` is part of that authority model for GitHub Actions.

## Current GitHub Workflow Inventory Relevant to Trading/Emails

- `daily-alpaca-paper.yml`
  - Trading workflow for Alpaca paper execution.
  - Scheduled for the intended `9:35 AM ET` window.
- `alpha_daily.yml`
  - Separate research/alpha workflow.
  - Can send alpha report email.
- `research-digest.yml`
  - Separate research digest workflow.
  - Can send research digest email.
- `export-broker-snapshot.yml`
  - Manual-only broker snapshot export.
  - No trading email role.

The full repo audit is captured in [repo_workflow_audit.md](/Users/brettolson/Documents/Caerus/quant-daily-report-main/repo_workflow_audit.md).

## Current Email Authority Model

- For the GitHub Actions daily Alpaca cycle, the downstream `email` job is the authoritative sender.
- `engine_run` persists the artifacts consumed by that job.
- The recent duplicate-email path has been removed.
- The daily Alpaca cycle should no longer have dual-authority overlap between `engine_run` and the downstream `email` job.

## Supported Email Flags And Boolean Semantics

Relevant flags:

- `EMAIL_PRETRADE`
- `EMAIL_MARKET_CONDITIONS`
- `EMAIL_TRADING_CONFIRMATION`
- `EMAIL_INTERNAL_DEBUG`
- `ENABLE_EMAIL`

Boolean parsing semantics:

- True tokens: `1`, `true`, `yes`, `y`, `on`
- False tokens: `0`, `false`, `no`, `n`, `off`
- Unset or empty: use the documented default

Operational consequence:

- `EMAIL_PRETRADE=0` now disables pretrade email generation/sending where governed by `core/email_governance.py`.
- The same explicit false behavior applies consistently to the related email flags above.

## Expected Daily Alpaca Email Behavior

- The accidental extra-email duplication should now be gone.
- The daily Alpaca GitHub Actions cycle should no longer produce overlap from both inline `engine_run` sends and the downstream `email` job.
- The exact number of emails can still depend on:
  - artifact availability
  - execution results availability
  - governance flags
  - recovery/failure conditions
- In normal operation, the workflow should now only send from the downstream `email` job’s intended categories rather than from multiple authorities.

## Expected Artifacts To Inspect After A Live Run

Check these first:

- `outputs/latest_run.json`
- `outputs/runs/<RUN_ID>/operator_summary.json`
- `outputs/runs/<RUN_ID>/trading_day_summary.json`
- `outputs/runs/<RUN_ID>/broker/intended_orders_<DATE>.json`
- `outputs/runs/<RUN_ID>/logs/ci_alpaca_run.log`

Useful follow-ons:

- `outputs/runs/<RUN_ID>/execution_results.json`
- `outputs/runs/<RUN_ID>/execution_payload.json`
- `outputs/execution_email/<DATE>.json`

What to verify in those artifacts:

- Correct `run_id` and `trade_date`
- Pretrade broker status is healthy when account is `ACTIVE`
- PDT risk fields are present when broker returns them
- Intended orders reflect capital-budget clipping and sell-before-buy sequencing
- No duplicate submission indication unless a replay/guard condition actually occurred

## Known Non-Goals / Intentionally Unchanged Areas

- No strategy ranking changes
- No asset-selection changes
- No redesign of trade planning or execution architecture
- No removal of broker reject classification
- No removal of capital-budget clipping
- No removal of duplicate submission guards
- No removal of reconciliation safety behavior
- No change to the separate alpha or research digest workflows beyond audit visibility

## Quick Operator Checklist For Next Live Verification

1. Confirm the `daily-alpaca-paper` workflow starts in the intended `9:35 AM ET` window.
2. Confirm `engine_run`, `execute_orders`, and `email` all complete with expected status.
3. Inspect `outputs/latest_run.json` and note the canonical `run_id`.
4. Inspect `outputs/runs/<RUN_ID>/operator_summary.json` for:
   - pretrade status
   - broker preflight status
   - PDT risk fields
   - confirmation email status
5. Inspect `outputs/runs/<RUN_ID>/trading_day_summary.json` for the final operator-facing summary.
6. Inspect `outputs/runs/<RUN_ID>/broker/intended_orders_<DATE>.json` to confirm the planned order set.
7. Inspect `outputs/runs/<RUN_ID>/logs/ci_alpaca_run.log` for:
   - broker preflight banner
   - PDT warning lines if present
   - execution summary lines
   - absence of duplicate inline-send behavior
8. Confirm operator emails match the downstream `email` job outputs only, without accidental duplicates.
