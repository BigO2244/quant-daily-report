# GitHub Actions And Email Audit

Generated from the repo state on 2026-03-16.

## Workflow Inventory

No workflow in `.github/workflows/` uses `workflow_call`, `repository_dispatch`, `push`, or `pull_request`.
All current entrypoints are `schedule` and/or `workflow_dispatch`.

| File | Workflow Name | Triggers | Schedule Detail | Main Jobs | Email-Capable |
|---|---|---|---|---|---|
| `.github/workflows/daily-alpaca-paper.yml` | `Daily Alpaca Paper Run` | `workflow_dispatch`, `schedule` | `35 14 * 1,2,12 1-5`, `35 14 1-7 3 1-5`, `35 13 8-31 3 1-5`, `35 13 * 4-10 1-5`, `35 13 1-7 11 1-5`, `35 14 8-30 11 1-5` => intended `9:35 AM America/New_York` weekdays | `engine_run`, `execute_orders`, `email` | Yes |
| `.github/workflows/alpha_daily.yml` | `Alpha Daily Run` | `workflow_dispatch`, `schedule` | `15 11 * * 1-5` => `6:15 AM ET` in EST, `7:15 AM ET` in EDT | `alpha` | Yes |
| `.github/workflows/research-digest.yml` | `Research Digest — Nightly` | `workflow_dispatch`, `schedule` | `0 12 * * 1-5`, `0 11 * * 1-5` => both weekday entries target `7:00 AM ET` across EST/EDT | `digest` | Yes |
| `.github/workflows/export-broker-snapshot.yml` | `Export Alpaca Broker Snapshot` | `workflow_dispatch` | None | `export_snapshot` | No |
| `.github/workflows/_archived_backtest_sleeve1.yml` | `Run Backtest (Sleeve 1)` | `workflow_dispatch` | None | `backtest` | No |
| `.github/workflows/_archived_backtest_sleeve1_robustness.yml` | `Sleeve1 Robustness Backtest` | `workflow_dispatch` | None | `robustness` | No |
| `.github/workflows/_archived_backtest_sleeve2.yml` | `Run Backtest (Sleeve 2)` | `workflow_dispatch` | None | `backtest` | No |

## Daily Alpaca Run Paths

Direct daily Alpaca execution path:

1. `daily-alpaca-paper.yml` `engine_run`
2. `python3 daily_quant_report.py`
3. artifacts uploaded to `alpaca-paper`
4. `daily-alpaca-paper.yml` `execute_orders`
5. `python3 scripts/execute_alpaca_orders.py`
6. artifacts uploaded to `alpaca-execution`
7. `daily-alpaca-paper.yml` `email`
8. persisted artifacts downloaded and email steps executed

There is no second workflow that invokes the daily Alpaca paper flow directly or indirectly.
The duplicate notification problem is intra-workflow, not cross-workflow.

## Overlap Window Audit

- `alpha_daily.yml` can email the same inbox around `6:15/7:15 AM ET`.
- `research-digest.yml` can email the same inbox around `7:00 AM ET`.
- `daily-alpaca-paper.yml` targets `9:35 AM ET`.
- These other workflows share the SMTP rail and recipient, but they do not call the daily Alpaca trading path.

## Email Path Inventory

| Path | Function / Step | Trigger Condition | Email Type / Purpose | Expected Daily | Invoked By |
|---|---|---|---|---|---|
| `daily_quant_report.py` | `_send_report_emails()` via `_deliver_inline_report_emails()` | End of planner/orchestrator run after artifacts are written | Inline execution/pre-trade style email and snapshot email | Yes on local runs; now disabled in daily Alpaca workflow | `daily_quant_report.py` in `engine_run` |
| `daily_trade_execution_email.py` | `main()` -> `send_execution_email()` | Email job step, when execution payload exists and governance allows | Pre-trade execution status / pre-trade analysis | Conditional | `daily-alpaca-paper.yml` `email` job |
| `scripts/send_trading_confirmation_email.py` | `main()` -> `send_email()` | Email job step, when `execution_results.json` exists and governance allows | Trading confirmation | Conditional on execution results | `daily-alpaca-paper.yml` `email` job |
| `.github/workflows/daily-alpaca-paper.yml` | inline Python snapshot step | Email job step, when snapshot text + HTML artifacts exist | Snapshot / market-conditions style report | Conditional | `daily-alpaca-paper.yml` `email` job |
| `scripts/generate_bootstrap_email_payload.py` | payload writer only | Auto-bootstrap recovery path | Writes execution payload artifact, does not send | Conditional | `daily-alpaca-paper.yml` `engine_run` auto-bootstrap step |
| `scripts/email_alpha_report.py` | `main()` | Alpha workflow scheduled/manual run | Alpha report email | Expected for alpha workflow | `alpha_daily.yml` |
| `quant_research_agent/main.py` with `quant_research_agent/delivery/smtp_email.py` | digest delivery | Research digest scheduled/manual run | Research digest email | Expected for digest workflow | `research-digest.yml` |
| `core/quant_report.py` | `send_email()` | Shared SMTP transport helper | SMTP transport used by multiple paths above | N/A | Shared helper |

## Concrete Duplicate Root Cause

The daily Alpaca workflow had two authorities sending the same operator email families:

1. `engine_run` called `daily_quant_report.py`, which SMTP-sent:
   - execution/pre-trade style email
   - snapshot email
2. The downstream `email` job then sent again from persisted artifacts:
   - pre-trade execution status email
   - trading confirmation email
   - snapshot email

Repo-grounded conclusion:

- The duplicate sender is inside `daily-alpaca-paper.yml`, not from a second daily Alpaca workflow.
- Snapshot duplication was definite on successful planner runs.
- Pre-trade/execution duplication was also possible because both `daily_quant_report.py` and `daily_trade_execution_email.py` can send that family.
- Alpha and research workflows add other inbox traffic, but they are separate workflows and separate email families.

## Narrow Fix Implemented

Authoritative sender for the daily Alpaca workflow is now the downstream `email` job.

- `daily_quant_report.py` now honors `EMAIL_INLINE_REPORTS`.
- `daily-alpaca-paper.yml` sets `EMAIL_INLINE_REPORTS: "0"` in `engine_run`.
- Result: `engine_run` still writes all report artifacts, but it no longer SMTP-sends execution/snapshot emails during the GitHub Actions daily Alpaca run.
- The `email` job still owns:
  - pre-trade execution status email
  - trading confirmation email
  - snapshot email

This removes the overlapping sender without changing trading logic, execution hardening, reconciliation, or `workflow_dispatch`.

