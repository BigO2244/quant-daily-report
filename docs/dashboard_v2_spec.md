# Dashboard V2 Spec

## Purpose

Define the target data contract, KPI set, and deployment shape for the refreshed Caerus operator dashboard.

This spec is the build target for the next dashboard iteration. It does not replace the current implementation docs in [quant_dashboard.md](/Users/brettolson/Documents/Caerus/quant-daily-report-main/docs/quant_dashboard.md); it defines the intended V2 contract and migration path.

## Problem Statement

The current dashboard shape is too close to an executive status page and not close enough to a live trading operator panel.

The refreshed dashboard must answer these four questions in under 30 seconds:

1. Are we beating SPY?
2. Are we expressing the live portfolio the way the strategy intends?
3. Are today’s trades making money?
4. Is the system healthy enough to trust the numbers on screen?

## Scope

V2 covers:

- KPI contract
- source-of-truth hierarchy
- chart and table layout requirements
- freshness and trust rules
- artifact generation order
- VM hosting plan

V2 does not yet cover:

- a multi-user backend
- intraday streaming
- authentication beyond VM/web-server controls
- factor-lab or research notebook visualization

## Design Principles

- Operator-first: prioritize decision quality over aesthetics.
- Artifact-driven: prefer persisted artifacts over live broker fetches for the primary view.
- Governed by default: show latest successful trustworthy run unless clearly labeled otherwise.
- Benchmark-relative: performance is not complete without SPY context.
- Construction-aware: deployment, concentration, and turnover belong on page one.
- Trade-aware: realized exits and open buys must be visible separately.
- Trust-labeled: every major panel needs freshness and confidence context.

## Users

Primary user:

- CIO / operator reviewing the morning run and daily portfolio state

Secondary users:

- engineering / ops reviewing run health
- research reviewing whether live expression matches intended strategy

## Dashboard IA

V2 should be a static dashboard with one homepage and four drill-down pages.

Homepage:

- headline performance
- portfolio expression
- trade quality
- attribution
- system health

Drill-down pages:

- performance
- portfolio construction
- trades
- ops / integrity

## Homepage Layout

Top strip:

- NAV
- day P&L
- since-inception excess vs SPY
- cash %
- gross exposure %
- holdings count
- realized exit P&L today
- system health status

Main chart row:

- indexed NAV vs SPY
- excess return plus drawdown

Lower tables:

- today’s trades
- sleeve contribution
- top holdings / top changes

Right rail or footer:

- execution health
- regime state
- freshness warnings
- reconciliation / duplicate guard / blocker messages

## V2 Data Contract

The dashboard builder should emit a single JSON artifact with the following top-level structure:

```json
{
  "schema_version": "dashboard-v2",
  "generated_at": "2026-04-07T14:30:00Z",
  "run_meta": {},
  "headline_performance": {},
  "portfolio_expression": {},
  "trade_quality": {},
  "attribution": {},
  "system_health": {},
  "series": {},
  "tables": {},
  "sources": [],
  "builder_notes": {}
}
```

## Section Contract

### `run_meta`

Purpose:

- identify what run/date the page represents
- explain whether the view is governed or live-derived

Required fields:

```json
{
  "report_date": "YYYY-MM-DD",
  "run_id": "string",
  "mode": "PAPER|SHADOW|LIVE",
  "selected_run_type": "governed|latest_attempted|manual_override",
  "overall_status": "PASS|WARNING|HALTED|FAILED|UNKNOWN",
  "status_banner": "string",
  "benchmark": "SPY",
  "selected_governed_run": {},
  "latest_attempted_run": {},
  "regime_state": {
    "state": "LOW|ELEVATED|HIGH|UNKNOWN",
    "position_scale": 0.75,
    "max_positions": 7
  }
}
```

Primary sources:

- `outputs/runs/<run_id>/operator_summary.json`
- `outputs/runs/<run_id>/trading_day_summary.json`
- `outputs/vix_regime/regime_current.json`

### `headline_performance`

Purpose:

- answer whether the strategy is making money and beating SPY

Required fields:

```json
{
  "nav": 9571.60,
  "day_pnl": -68.53,
  "day_return": -0.0071,
  "wtd_return": -0.0042,
  "mtd_return": 0.0035,
  "since_inception_return": -0.0062,
  "spy_day_return": -0.0014,
  "spy_wtd_return": 0.0021,
  "spy_mtd_return": 0.0074,
  "spy_since_inception_return": 0.0098,
  "excess_day_return": -0.0057,
  "excess_wtd_return": -0.0063,
  "excess_mtd_return": -0.0039,
  "excess_since_inception_return": -0.0160,
  "current_drawdown": -0.0215,
  "max_drawdown": -0.0340,
  "rolling_5d_excess_return": -0.0041,
  "rolling_10d_excess_return": -0.0112
}
```

Primary sources:

- `outputs/perf/nav_timeseries.csv`
- `outputs/perf/benchmark_close_history.csv`
- `outputs/benchmark/benchmark_vs_spy.json`

Fallback sources:

- `outputs/alpha_assessment/canonical_performance.csv`

Computation rules:

- `nav` = latest governed equity
- `day_pnl` = latest equity minus prior equity
- `day_return` = latest `return_1d` when present, else `equity_t / equity_t-1 - 1`
- SPY returns are computed from `spy_close`
- excess returns are portfolio return minus SPY return on the same horizon
- drawdown is computed from governed NAV, not broker mark-to-market unless explicitly labeled

### `portfolio_expression`

Purpose:

- answer whether the live book is deployed and concentrated the way the strategy intends

Required fields:

```json
{
  "cash": 2455.01,
  "cash_weight": 0.2565,
  "market_value": 7116.59,
  "gross_exposure": 0.7435,
  "net_exposure": 0.7435,
  "holdings_count": 16,
  "new_positions_count": 9,
  "full_exits_count": 13,
  "turnover": 0.3148,
  "median_position_weight": 0.0540,
  "largest_position_weight": 0.0890,
  "top_5_concentration": 0.3620,
  "target_cash_weight": 0.05,
  "cash_drift_vs_target": 0.2065,
  "construction_status": "ALIGNED|DRIFTED|UNKNOWN"
}
```

Primary sources:

- `outputs/perf/nav_timeseries.csv`
- `outputs/runs/<run_id>/broker/pretrade_positions.json`
- `outputs/broker/broker_snapshot_latest.json`
- `outputs/runs/<run_id>/operator_summary.json`

Secondary sources:

- `outputs/runs/<run_id>/execution_summary.csv`
- `outputs/research_context/<date>.json`

Computation rules:

- `cash_weight` = cash / equity
- `gross_exposure` = market value / equity when direct field missing
- `holdings_count` = current open positions count
- `new_positions_count` = count of buys where pretrade qty was zero or missing
- `full_exits_count` = count of sells where posttrade qty is zero or symbol absent
- `turnover` = use governed turnover metric when present; otherwise buy+sell notional divided by prior equity
- `construction_status` = `DRIFTED` if cash weight, holdings count, or concentration materially deviate from intended live policy

### `trade_quality`

Purpose:

- answer whether today’s trades actually made money

Required fields:

```json
{
  "trade_date": "YYYY-MM-DD",
  "orders_filled": 22,
  "buy_fills": 9,
  "sell_fills": 13,
  "realized_exit_pnl": 1.44,
  "open_buy_mark_pnl": -0.48,
  "winning_exits": 8,
  "losing_exits": 5,
  "median_exit_pnl": 1.82,
  "best_exit": {"ticker": "C", "pnl": 23.60},
  "worst_exit": {"ticker": "ABNB", "pnl": -29.67},
  "average_holding_period_days": null,
  "slippage_bps_avg": null,
  "status": "COMPLETE|PARTIAL|MISSING"
}
```

Required trade table shape:

```json
[
  {
    "ticker": "C",
    "side": "SELL",
    "qty": 4,
    "fill_price": 116.05,
    "pretrade_avg_entry": 110.15,
    "current_price": null,
    "realized_pnl": 23.60,
    "open_mark_pnl": null
  }
]
```

Primary sources:

- `outputs/runs/<run_id>/broker/pretrade_positions.json`
- `outputs/broker_snapshot/broker_snapshot_<date>.json`
- `outputs/runs/<run_id>/execution_results.json`

Target canonical artifact:

- `outputs/runs/<run_id>/trade_day_pnl.json`
- `outputs/runs/<run_id>/trade_day_pnl.csv`

Computation rules:

- realized exit P&L = `filled_avg_price - pretrade_avg_entry_price` times filled quantity
- open buy mark P&L = `current_price - filled_avg_price` times filled quantity
- buys and sells must never be merged into one undifferentiated “trade P&L” number

Implementation note:

- retaining `filled_avg_price` in broker/order artifacts is mandatory for this section

### `attribution`

Purpose:

- explain where returns came from

Required V1 fields:

```json
{
  "sleeve_contribution_today": [],
  "sleeve_contribution_since_inception": [],
  "ticker_contribution_today": [],
  "top_positive_ticker": null,
  "top_negative_ticker": null,
  "status": "COMPLETE|PARTIAL|MISSING"
}
```

Target later fields:

- allocation effect
- selection effect
- rolling beta
- rolling alpha
- information ratio
- upside / downside capture

Primary sources:

- `outputs/perf/contribution_sleeves_<date>.csv`
- `outputs/perf/contribution_tickers_<date>.csv`
- `outputs/alpha_assessment/canonical_performance.csv`

### `system_health`

Purpose:

- determine whether to trust the numbers on page one

Required fields:

```json
{
  "precompute_status": "PASS|FAIL|MISSING",
  "execution_status": "EXECUTED|HALTED|FAILED|SKIPPED_DUPLICATE|READY|UNKNOWN",
  "confirmation_status": "SENT|SKIPPED|FAILED|UNKNOWN",
  "reconciliation_status": "PASS|WARN|FAIL|UNKNOWN",
  "duplicate_guard_status": "CLEAR|SKIPPED_DUPLICATE|UNKNOWN",
  "timing_status": "ON_TIME|DEGRADED_LATE|UNKNOWN",
  "broker_snapshot_freshness": "fresh|stale|missing",
  "benchmark_freshness": "fresh|stale|missing",
  "data_alignment_status": "aligned|mismatch|missing",
  "capital_constraint_triggered": false,
  "risk_breaker_status": "OFF|PARTIAL|LOCK|UNKNOWN",
  "warnings": [],
  "exceptions": []
}
```

Primary sources:

- `outputs/runs/<run_id>/operator_summary.json`
- `outputs/runs/<run_id>/trading_day_summary.json`
- `outputs/broker/broker_snapshot_latest.json`
- `outputs/execution_email/<date>.json`

## `series` Contract

Required chart series:

```json
{
  "nav_indexed": [{"date": "YYYY-MM-DD", "value": 100.0}],
  "spy_indexed": [{"date": "YYYY-MM-DD", "value": 100.0}],
  "daily_returns": [{"date": "YYYY-MM-DD", "value": 0.0012}],
  "excess_returns": [{"date": "YYYY-MM-DD", "value": -0.0024}],
  "drawdown": [{"date": "YYYY-MM-DD", "value": -0.013}],
  "cash_weight": [{"date": "YYYY-MM-DD", "value": 0.12}],
  "gross_exposure": [{"date": "YYYY-MM-DD", "value": 0.88}],
  "holdings_count": [{"date": "YYYY-MM-DD", "value": 12}],
  "turnover": [{"date": "YYYY-MM-DD", "value": 0.14}]
}
```

Primary sources:

- `outputs/perf/nav_timeseries.csv`
- `outputs/perf/benchmark_close_history.csv`
- `outputs/alpha_assessment/canonical_performance.csv`

## `tables` Contract

Required tables:

```json
{
  "today_trades": [],
  "top_holdings": [],
  "top_changes": [],
  "sleeve_contribution": [],
  "ticker_contribution": [],
  "exceptions": [],
  "operating_checks": []
}
```

Minimum table requirements:

- `today_trades`: one row per filled order, including realized/open P&L fields
- `top_holdings`: ticker, sleeve, market value, weight, unrealized P&L
- `top_changes`: ticker, action, weight change, reason
- `sleeve_contribution`: sleeve, contribution today, contribution since inception
- `ticker_contribution`: ticker, contribution today

## Source-of-Truth Hierarchy

For each KPI family, V2 should follow a fixed hierarchy.

Performance:

1. governed performance artifacts
2. canonical performance layer
3. benchmark mirror artifacts
4. never use ad hoc browser-side inference when an artifact exists

Portfolio expression:

1. post-execution governed artifacts
2. authoritative broker snapshot
3. pretrade broker snapshot
4. derived values only with explicit confidence labels

Trade quality:

1. canonical trade-day P&L artifact
2. order artifacts with `filled_avg_price` plus pretrade positions plus current broker snapshot
3. manual reconstruction only for one-off incident review, not for dashboard runtime

System health:

1. trading-day summary
2. operator summary
3. raw logs only for drill-down links

## Freshness Rules

Every major block must carry a freshness or trust indicator.

Rules:

- governed run data is fresh when report date matches selected run date
- broker snapshot is fresh when within configured threshold and aligned to the governed run or clearly labeled newer
- benchmark data is stale if latest available close predates the selected report date by more than one trading day
- any block with derived values must expose a confidence note

Suggested classifications:

- `fresh`
- `aligned`
- `mismatch`
- `stale`
- `derived`
- `missing`

## Current Gaps Blocking Full V2

The following gaps must be closed before a polished dashboard is trustworthy:

1. `outputs/benchmark/benchmark_vs_spy.json` is too sparse for serious benchmark-relative history.
2. `outputs/alpha_assessment/canonical_performance.csv` still has missing required fields on many dates.
3. trade-level realized/open P&L is not yet persisted as a first-class artifact.
4. construction-parity metrics are not yet a formal artifact family.
5. some documentation still points to `scripts/build_quant_dashboard.py` while the actual builder is `scripts/research/build_quant_dashboard.py`.

## Builder Changes Required

### Phase 1: Contract wiring

Update dashboard builder output shape from the current broad buckets:

- `kpis`
- `perf_summary`
- `risk`
- `activity`

to the V2 blocks:

- `headline_performance`
- `portfolio_expression`
- `trade_quality`
- `attribution`
- `system_health`
- `series`
- `tables`

Migration note:

- keep legacy keys temporarily for backward compatibility with existing frontend code
- add `schema_version` and emit both shapes during the transition

### Phase 2: Missing KPI producers

Add or formalize producers for:

- trade-day P&L artifact
- construction-parity artifact
- benchmark-relative rolling performance series
- concentration history series

### Phase 3: Frontend refresh

Update static frontend to consume V2 contract and render:

- homepage
- performance page
- trades page
- ops page

### Phase 4: Remove legacy schema

Remove old `kpis` / `risk` / `activity` coupling only after frontend is fully migrated.

## VM Hosting Spec

V1 hosting should remain static-file based.

Target shape:

- generate dashboard JSON and assets on the GCP VM
- publish to a stable served directory such as `/var/www/caerus-dashboard/`
- serve through nginx
- keep the build artifact generation in the repo checkout, but publish the static site outside the repo if desired

Recommended publish structure:

```text
/var/www/caerus-dashboard/
  index.html
  quant_daily_executive.css
  quant_daily_executive.js
  dashboard_data.json
  history/
    2026-04-07/
      dashboard_data.json
```

Recommended generation timing:

- after confirmation phase completes successfully, publish final dashboard
- optional degraded publish after execution halt, clearly labeled as incomplete

Security recommendation:

- start with basic auth or IP allowlist
- do not expose raw logs directly from the web root

## Acceptance Criteria

The V2 spec is satisfied when:

1. the homepage can answer benchmark-relative performance, deployment state, trade quality, and system health from one screen
2. the dashboard can render entirely from persisted artifacts
3. every major KPI block exposes freshness / trust context
4. same-day trades show realized exit P&L separately from open buy P&L
5. the VM can publish the dashboard as a static site after the daily run

## Recommended Immediate Next Step

Implement the V2 builder contract first, before visual redesign:

1. add `schema_version`
2. emit the five homepage KPI blocks
3. persist trade-day P&L as a first-class artifact
4. patch the frontend to read V2 blocks while preserving legacy fallback

That sequence keeps the dashboard redesign anchored to trustworthy measurement instead of rebuilding the UI around partial data.
