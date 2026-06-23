# Portfolio Command Dashboard Lineage Audit

Generated: 2026-06-23

## Scope

This audit covers the static Portfolio Command dashboard served from `web/dashboard/` and built by `scripts/research/build_dashboard_v1.py`.

The dashboard is reporting-only. It does not submit broker orders, change paper trading, alter allocator behavior, promote sleeves, or change scheduler behavior.

## Builder And Published Artifacts

Primary builder:
- `scripts/research/build_dashboard_v1.py`

Refresh/deploy entry points:
- `scripts/refresh_quant_dashboard.py`
- `scripts/deploy_dashboard_vm.sh`

Published local artifacts:
- `web/dashboard/dashboard_data.json`
- `web/dashboard/dashboard-data.json`
- `web/dashboard/dashboard-data.js`
- `web/dashboard/trading_day_summary.json`
- `web/dashboard/index.html`
- `web/dashboard/quant_daily_executive.js`
- `web/dashboard/quant_daily_executive.css`

## Source Lineage

| Dashboard section | Source artifacts | Trust class | Notes |
| --- | --- | --- | --- |
| Paper account / NAV | `outputs/broker/broker_snapshot_latest.json`, `outputs/perf/live_overlay_nav_series.csv`, fallback `outputs/perf/nav_timeseries.csv` | broker/runtime plus derived performance | NAV display uses broker account values for current account state and performance CSV for history. |
| Paper positions | `outputs/broker/posttrade_positions.json` | broker/runtime | Position weights and concentration are derived from broker position market values and account equity. |
| Fills / execution tape | `outputs/broker_snapshot/broker_snapshot_<date>.json` | broker/runtime | Uses `fills_report_date`; missing or off-date fills remain validation warnings/errors. |
| Paper performance | `outputs/perf/live_overlay_nav_series.csv`, `outputs/perf/live_overlay_benchmark_close_history.csv`, fallback governed performance CSVs | derived | Used for NAV indexed chart, SPY comparison, drawdown, and rolling returns. |
| Shadow sleeve performance | `outputs/shadow_candidates/<date>/shadow_evaluation.json`, `outputs/shadow_candidates/performance/shadow_nav_series.csv` | diagnostic/shadow | Dynamically loads active shadow-tracked security-selection entries from registry. |
| Sleeve inventory | `config/research/strategy_registry.json`, `research_registry/sleeves/manifest.json`, shadow evaluation artifacts | governance plus diagnostic | Includes Polaris, Polaris_Alpha, Orion, Orion_Alpha, Lyra, and future registered sleeves automatically. Benchmarks are excluded from sleeve inventory. |
| Baseline vs Alpha | Registry baseline links plus shadow evaluation metrics | diagnostic/shadow | Compares Polaris vs Polaris_Alpha and Orion vs Orion_Alpha, including 20/60 trading-day checkpoints. |
| Live pilot status | `outputs/live_pilot/plans/live_pilot_plan_*.json`, `outputs/live_pilot/runs/*/live_pilot_*.json` | runtime/live-pilot | Read-only. Shows cap, order policy, submitted/open orders, reconciliation, fill rate, slippage, and idle cash reason when available. |
| Governance state | Hard-coded current governance interpretation plus registry/lifecycle state | governance/display | FR-068 is dependency-blocked for promotion/scaling, not pilot-blocking. FR-104 evidence collection remains separately gated. |

## Current Staleness And Warning State

Latest generated local dashboard payload currently reports:
- `history_latest_nav_matches_nav_section`: blocking dashboard validation error; latest performance NAV does not match current broker NAV.
- `shadow_nav_current`: non-blocking warning; shadow NAV latest date lags the latest shadow evaluation date.
- `decision_grade_model_quality_present`: non-blocking warning; model-quality artifacts are incomplete for the report date.
- `positions_timestamp_fresh`, `nav_timestamp_fresh`, `trades_today_timestamp_fresh`: non-blocking freshness warnings; broker/account/fill timestamps are stale relative to generation time.
- `performance_timestamp_fresh`: non-blocking warning; performance history latest date lags report date.

Impact classification:
- Blocks dashboard trust: yes, current payload correctly marks validation level `error` because account NAV and history NAV disagree.
- Blocks FR-104 pilot evidence collection: no.
- Blocks sleeve promotion/production scaling: yes, because FR-068 PIT membership and decision-grade evidence remain unresolved.
- Blocks static dashboard generation: no; payload generation succeeds and carries the warning/error state.

## Live Pilot Lineage

The live pilot dashboard section is isolated to FR-104 artifacts:
- Plans: `outputs/live_pilot/plans/live_pilot_plan_*.json`
- Runs: `outputs/live_pilot/runs/<run_id>/`
- Evidence: `live_pilot_evidence_metrics.json`
- Reconciliation: `live_pilot_reconciliation.json`
- Open-order guard: `live_pilot_open_order_check.json`
- Broker snapshots: `live_pilot_broker_snapshot_pre.json`, `live_pilot_broker_snapshot_post.json`

The dashboard does not call broker APIs or submit orders. It only reads persisted artifacts.

## Governance Interpretation

FR-104 Level 2.5 pilot evidence collection:
- Status: active/ready when approval, cap, account, market-hours, open-order, and reconciliation gates pass.
- Pilot-blocking: only live-pilot guardrail failures or missing approvals block a pilot run.

FR-068:
- Status: dependency-blocked on certified PIT date-effective large-cap membership.
- Pilot-blocking: no.
- Promotion/scaling-blocking: yes.

Alpha sleeves:
- Polaris_Alpha: SHADOW.
- Orion_Alpha: SHADOW.
- Promotion blocked until 20/60-day forward evidence and decision-grade PIT infrastructure are available.

## Remaining Gaps

1. Broker/current NAV and performance-history NAV are not aligned in the current local artifact set.
2. Live-pilot account values are unavailable until a live-pilot run writes broker snapshots under `outputs/live_pilot/runs/<run_id>/`.
3. Paper/live divergence remains unavailable because no dedicated divergence artifact is present.
4. Dashboard screenshots are not produced by the existing generation flow.
