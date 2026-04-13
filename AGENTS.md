# AGENTS.md

This is the single agent-facing handoff for this repository. Operational,
architecture, scheduler, and workflow guidance should live here. The older
`CLAUDE.md` files are retired.

## System Snapshot

- Project: Caerus Quant / Alpha Stack quantitative trading platform
- Scope: US long-only equities, paper trading through Alpaca, deterministic
  run artifacts, operator-facing email/reporting
- Production posture: paper only, no shorting, no leverage, no live trading
- Promotion ladder: research -> backtest -> shadow -> paper -> live
- Hard rule: do not change production trading behavior casually; bias toward
  safety, deterministic artifacts, and explicit verification

## Current Operating Environments

- Local development:
  - host: `brettolson@BDO-Macbook`
  - use for coding, tests, diagnostics, dashboard generation, and artifact
    review
- Scheduler VM:
  - host: `brettolson@34.61.147.38`
  - path: `~/quant-daily-report`
  - venv: `source venv/bin/activate`
  - secrets: `~/quant-daily-report/.env`
- GitHub Actions:
  - historically used for scheduled paper execution, alpha reporting, and
    nightly research digest delivery
  - current checkout does not contain the full trading workflow files, so this
    document distinguishes between materialized workflow files and the last
    audited workflow inventory

## Deployment And Verification Rules

- Local commits do not deploy the VM.
- Prefer local development first, then explicit deploy.
- If files are copied to the VM, verify the remote content with `md5sum`,
  `grep`, or a direct read. Never assume SCP succeeded.
- Do not edit the VM directly unless the situation is an explicit hotfix.
- For scheduler incidents, inspect these first:
  - `outputs/latest_run.json`
  - `logs/execute_<date>.log`
  - `outputs/broker/recon_pretrade_<date>.json`
  - `outputs/precompute/<date>/contract.json`

## Scheduled Automation

- Scheduler cron cadence on the VM:
  - `7:00 AM ET`: `scripts/cron_precompute.sh`
  - `9:35 AM ET`: `scripts/cron_execute.sh`
  - `10:00 AM ET`: `scripts/cron_confirm.sh`
- Cron wrappers for scheduler-host paper flow must force:
  - `MODE=alpaca`
  - `TRADING_MODE=alpaca`
  - `ALPACA_PAPER=1`
- Audited GitHub Actions cadence from [`repo_workflow_audit.md`](/Users/brettolson/Documents/Caerus/quant-daily-report-main/repo_workflow_audit.md):
  - `daily-alpaca-paper.yml`: `9:35 AM ET` weekdays
  - `alpha_daily.yml`: `6:15 AM ET` in EST, `7:15 AM ET` in EDT
  - `research-digest.yml`: `7:00 AM ET` weekdays
- Materialized in this checkout:
  - `research-digest.yml`: scheduled nightly producer for dashboard data,
    `reports/agents/nightly_findings.json`, and `AGENTS.md` refreshes
  - `nightly-agents-refresh.yml`: manual fallback to rebuild the
    auto-generated sections in this file from the latest findings

## Architecture Focus

- Main orchestrator: [`daily_quant_report.py`](/Users/brettolson/Documents/Caerus/quant-daily-report-main/daily_quant_report.py)
- Broker-authoritative pre/post-trade reconciliation:
  [`reconciliation.py`](/Users/brettolson/Documents/Caerus/quant-daily-report-main/reconciliation.py)
- Execution status email reconstruction:
  [`daily_trade_execution_email.py`](/Users/brettolson/Documents/Caerus/quant-daily-report-main/daily_trade_execution_email.py)
- Dashboard builder:
  [`scripts/research/build_quant_dashboard.py`](/Users/brettolson/Documents/Caerus/quant-daily-report-main/scripts/research/build_quant_dashboard.py)
- Static dashboard surface:
  [`web/dashboard/index.html`](/Users/brettolson/Documents/Caerus/quant-daily-report-main/web/dashboard/index.html)
- Agent-doc updater:
  [`scripts/update_agents_md.py`](/Users/brettolson/Documents/Caerus/quant-daily-report-main/scripts/update_agents_md.py)

## Broker-Authoritative Dashboard Contract

Phase 5 dashboard/reporting work should surface these fields when present:

- `broker_pretrade_snapshot_ok`
- `broker_posttrade_snapshot_ok`
- `broker_authoritative_state`
- `broker_preflight_cash`
- `broker_preflight_equity`
- `broker_preflight_buying_power`
- `post_execution_recon_status`
- pretrade and posttrade position counts
- pretrade and posttrade cash/equity deltas
- broker trust level derived from the snapshot/reconciliation state

Dashboard implementation notes:

- Build fresh data with:
  - `python3 scripts/research/build_quant_dashboard.py`
- Generated files:
  - [`web/dashboard/dashboard-data.json`](/Users/brettolson/Documents/Caerus/quant-daily-report-main/web/dashboard/dashboard-data.json)
  - [`web/dashboard/dashboard-data.js`](/Users/brettolson/Documents/Caerus/quant-daily-report-main/web/dashboard/dashboard-data.js)
- Browser entrypoint:
  - [`web/dashboard/index.html`](/Users/brettolson/Documents/Caerus/quant-daily-report-main/web/dashboard/index.html)

## Nightly Findings Contract

The nightly AGENTS refresh looks for the latest findings in this order:

- `reports/agents/nightly_findings.json`
- `reports/agents/nightly_findings.md`
- latest file under `reports/agents/`
- latest file under `reports/ai_runs/`
- latest file under `reports/incidents/`

Preferred JSON shape:

```json
{
  "generated_at": "2026-04-08T11:00:00Z",
  "headline": "Risk posture remains elevated",
  "summary": [
    "Breadth improved but remains below neutral threshold.",
    "No operator action required before next scheduled run."
  ],
  "risks": [
    "PDT warning still near threshold."
  ],
  "actions": [
    "Review post-trade reconciliation on next execution."
  ]
}
```

The updater only rewrites the auto-generated sections below. Human-edited
guidance outside those markers should remain stable.

Authoritative nightly path:

- `research-digest.yml` builds the dashboard payload, writes
  `reports/agents/nightly_findings.json`, then refreshes `AGENTS.md`.
- `nightly-agents-refresh.yml` is a manual recovery tool if the findings file
  already exists but `AGENTS.md` needs to be regenerated.

## High-Risk Areas

Changes in these areas require explicit caution and validation:

- reconciliation and broker state repair paths
- paper execution and order-submission flow
- canonical state under `outputs/paper_state/`
- orchestration in `daily_quant_report.py`
- workflow schedules and email routing
- artifact schemas and JSON / CSV output contracts
- dashboard/reporting code that operator decisions may rely on

## Agent Working Rules

- Inspect the current implementation before making broad changes.
- Prefer minimal, surgical edits unless the task explicitly calls for
  restructuring.
- If you touch execution, reconciliation, or reporting contracts, run the
  narrowest relevant validation first and then a broader check if warranted.
- Report exact commands run and whether they passed.
- Keep research work separate from production behavior unless the task is an
  explicit promotion.

## Phase Implementation Status

| Phase | Description | Status | Confirmed |
|---|---|---|---|
| Phase 1 | Live regime-aware multi-sleeve allocator + `live_regime_review` artifacts | **Operationally confirmed** | 2026-04-09 |
| Phase 2A | Shadow-only SPY options overlay (`options_overlay_shadow`) | **Operationally confirmed** | 2026-04-09 |
| Phase 3A | Defensive ETF sleeve (`SGOV`, `SHY`, `IEF`, `TLT`) regime-gated | **Operationally confirmed** | 2026-04-09 |

Confirmation evidence: `outputs/audits/phase_1_2_3_finalization_audit_2026-04-09.md`
— 22/22 pytest passed; offline fixture + live-data plan-only smoke both PASS.

## Immediate Open Decisions

- Decide whether `quality_enhanced` becomes the intended quality sleeve spec.
- Decide whether mean reversion should gain the missing healthy-breadth gate
  before stronger promotion confidence.
- Decide whether regime-aware TP/SL should be applied at the allocator/notional
  layer before share rounding.
- Continue building live-history evidence before trusting portfolio-level
  attribution or alpha/IR/drawdown claims.

## Ops Handoff

- Scheduler host path should be `~/quant-daily-report`.
- Expected cron source is `scripts/crontab.txt`.
- If a `SELF_HEAL` pretrade reconciliation occurs during execution, the wrapper
  should re-run reconciliation once against the refreshed canonical state and
  proceed only if that follow-up verdict is `PASS` or `WARN`.
- Same-day retry locks should block duplicate successful executions but must not
  strand genuine recovery reruns after pre-execution failure.

## Auto-Generated Nightly Findings

<!-- BEGIN AUTO-GENERATED: NIGHTLY FINDINGS -->
- Last refresh: `2026-04-08T14:14:31+00:00`
- Source: `reports/agents/nightly_findings.json`
- Findings generated at: `2026-04-08T14:14:28+00:00`
- Broker-authoritative state not confirmed
- Trade date: 2026-04-08.
- Broker trust level: LOW.
- Pretrade status: UNKNOWN; posttrade reconciliation: UNKNOWN.
- Nightly digest schedule from audit: 0 12 * * 1-5, 0 11 * * 1-5 => both weekday entries target 7:00 AM ET across EST/EDT.
- Pretrade broker snapshot was not confirmed in the latest available artifacts.
<!-- END AUTO-GENERATED: NIGHTLY FINDINGS -->

## Auto-Generated Workflow Inventory

<!-- BEGIN AUTO-GENERATED: WORKFLOW INVENTORY -->
- Materialized workflow files in this checkout:
- `nightly-agents-refresh.yml`: Nightly Agents Refresh | triggers=workflow_dispatch | cron=none
- `research-digest.yml`: Research Digest — Nightly | triggers=workflow_dispatch, schedule | cron=`0 11 * * 1-5`, `0 12 * * 1-5`
- Last audited workflow inventory from `repo_workflow_audit.md`:
- `daily-alpaca-paper.yml`: Daily Alpaca Paper Run | triggers=workflow_dispatch, schedule | schedule=35 14 * 1,2,12 1-5, 35 14 1-7 3 1-5, 35 13 8-31 3 1-5, 35 13 * 4-10 1-5, 35 13 1-7 11 1-5, 35 14 8-30 11 1-5 => intended 9:35 AM America/New_York weekdays
- `alpha_daily.yml`: Alpha Daily Run | triggers=workflow_dispatch, schedule | schedule=15 11 * * 1-5 => 6:15 AM ET in EST, 7:15 AM ET in EDT
- `research-digest.yml`: Research Digest — Nightly | triggers=workflow_dispatch, schedule | schedule=0 12 * * 1-5, 0 11 * * 1-5 => both weekday entries target 7:00 AM ET across EST/EDT
- `export-broker-snapshot.yml`: Export Alpaca Broker Snapshot | triggers=workflow_dispatch | schedule=None
- `_archived_backtest_sleeve1.yml`: Run Backtest (Sleeve 1) | triggers=workflow_dispatch | schedule=None
- `_archived_backtest_sleeve1_robustness.yml`: Sleeve1 Robustness Backtest | triggers=workflow_dispatch | schedule=None
- `_archived_backtest_sleeve2.yml`: Run Backtest (Sleeve 2) | triggers=workflow_dispatch | schedule=None
<!-- END AUTO-GENERATED: WORKFLOW INVENTORY -->

## Historical References

- [`repo_workflow_audit.md`](/Users/brettolson/Documents/Caerus/quant-daily-report-main/repo_workflow_audit.md)
- [`specs/phase_5_broker_pretrade_snapshot.md`](/Users/brettolson/Documents/Caerus/quant-daily-report-main/specs/phase_5_broker_pretrade_snapshot.md)
- [`specs/broker_authoritative_execution_model.md`](/Users/brettolson/Documents/Caerus/quant-daily-report-main/specs/broker_authoritative_execution_model.md)
- [`CHANGELOG.md`](/Users/brettolson/Documents/Caerus/quant-daily-report-main/CHANGELOG.md)
