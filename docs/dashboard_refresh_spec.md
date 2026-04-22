# Dashboard Refresh Spec

## Goal

Refresh the operator dashboard so the first screen answers the questions that matter for this system:

1. Are we beating SPY?
2. Are we expressing the portfolio the way the backtest intended?
3. Are the trades making money?
4. Is the system healthy enough to trust today's numbers?

The attached reference image is directionally useful for layout density, but the KPI mix needs to be quant-operator specific rather than retail portfolio specific.

Implementation-grade contract, source mapping, and VM hosting details live in [dashboard_v2_spec.md](/Users/brettolson/Documents/Caerus/quant-daily-report-main/docs/dashboard_v2_spec.md).

For the restart, the minimum truthful foundation now lives in [dashboard_v1_spec.md](/Users/brettolson/Documents/Caerus/quant-daily-report-main/docs/dashboard_v1_spec.md). V1 intentionally narrows scope to positions, NAV, today's trades, and historical performance plus automated validation gates.

## Current Artifact Reality

The repo already has a usable measurement base:

- `outputs/trading_day_summary.json`
- `outputs/runs/<run_id>/operator_summary.json`
- `outputs/benchmark/benchmark_vs_spy.json`
- `outputs/alpha_assessment/canonical_performance.csv`
- `outputs/perf/nav_timeseries.csv`
- `outputs/perf/contribution_tickers_<date>.csv`
- `outputs/perf/contribution_sleeves_<date>.csv`
- `outputs/broker/broker_snapshot_latest.json`
- `outputs/runs/<run_id>/broker/pretrade_positions.json`

The repo does not yet consistently surface the most important operator view:

- trade-level realized P&L on sells
- trade-level open P&L on same-day buys
- construction parity vs intended live policy
- reliable benchmark-relative history over enough days to be decision-useful

## Page-One KPI Set

Page one should be a single-screen operator summary with five blocks.

### 1. Headline Performance

These are the top-line cards.

- Portfolio NAV
- Daily P&L dollars
- Daily return %
- Since-inception return %
- SPY since-inception return %
- Excess return vs SPY since inception
- WTD excess return vs SPY
- MTD excess return vs SPY

These are the first KPIs because "are we beating the market?" is the primary business question.

### 2. Portfolio Expression

This block exists because recent underperformance was contaminated by bad live construction.

- Cash weight
- Gross exposure
- Holdings count
- Median position weight
- Largest position weight
- Top 5 concentration %
- Turnover %
- New positions count
- Full exits count

These tell us whether the live book is concentrated, deployed, and changing in a way that resembles the intended strategy.

### 3. Trade Quality

This block answers "did today's turnover actually make money?"

- Realized exit P&L today
- Open P&L on today's new buys
- Winning exits / losing exits
- Median realized P&L per exit
- Best exit
- Worst exit
- Average holding period on exits
- Average slippage vs decision price

This should also include a compact trade table:

- `ticker`
- `side`
- `qty`
- `fill price`
- `cost basis`
- `realized P&L` for sells
- `current mark`
- `open P&L` for today's buys

### 4. Model / Attribution

This block answers whether alpha is coming from the right places.

- Sleeve contribution today
- Sleeve contribution since inception
- Top positive ticker contributor today
- Top negative ticker contributor today
- Allocation effect vs selection effect
- Beta / alpha estimate vs SPY
- Information ratio
- Capture ratio up/down if available

The first usable version can start with sleeve and ticker contribution only. Allocation/selection can follow once the canonical performance layer is filled in reliably.

### 5. System Health

This block determines whether to trust the other four blocks.

- Precompute status
- Execution status
- Confirmation status
- Broker snapshot freshness
- Benchmark freshness
- Reconciliation status
- Duplicate guard status
- Timing status
- Regime state
- Any active risk breaker / capital constraint

## Drill-Down Pages

Do not overload page one. Add drill-down pages instead.

### Performance Page

- NAV vs SPY indexed chart
- Daily excess return series
- Drawdown chart
- Rolling 5-day / 10-day excess return
- Realized vs unrealized P&L over time

### Portfolio Construction Page

- Holdings table with weight, sleeve, market value, unrealized P&L
- Cash / exposure history
- Position count history
- Concentration history
- Sector / industry exposure

### Trades Page

- Same-day fills table
- Exit P&L table
- Open P&L on same-day buys
- Holding period distribution
- Win rate / payoff ratio / expectancy

### Attribution Page

- Sleeve contribution table
- Ticker contribution table
- Benchmark-relative decomposition
- Regime-conditioned results

### Ops Page

- Workflow timeline
- Latest exception/halt reason
- Freshness checks
- Remote VM/build health

## KPI Priorities

Not all KPIs are equally important.

### Must Have For V1

- NAV
- Daily P&L
- Since-inception return
- SPY return
- Excess return vs SPY
- Cash weight
- Gross exposure
- Holdings count
- Turnover
- Realized exit P&L today
- Open P&L on today's buys
- Winning vs losing exits
- Sleeve contribution today
- Execution / reconciliation / freshness status

### Should Have Soon After

- Top 5 concentration
- Median position weight
- Holding period on exits
- Slippage vs decision price
- Ticker contribution today
- Rolling excess return
- Drawdown

### Later

- Allocation vs selection attribution
- Rolling beta / alpha
- Capture ratios
- Factor-level diagnostics
- Regime-conditioned trade expectancy

## Layout Recommendation

Use the reference image's density, but change the content hierarchy.

### Top strip

Eight compact KPI cards:

- NAV
- Day P&L
- SI excess vs SPY
- Cash %
- Gross exposure %
- Holdings count
- Realized exit P&L
- System status

### Middle row

Two main charts:

- Indexed NAV vs SPY
- Excess return and drawdown

### Lower row

Three compact tables:

- Today's trades
- Sleeve contribution
- Top holdings / top changes

### Right rail or footer

- execution health
- regime state
- warnings / blockers

## Data Gaps To Close Before Build

The dashboard should not be built as a polished shell over incomplete data. These gaps matter:

1. `benchmark_vs_spy.json` is still too sparse for a trustworthy performance panel.
2. `canonical_performance.json` still has many missing required fields.
3. Trade-level P&L is not yet a canonical artifact; it currently needs reconstruction.
4. Construction-parity metrics are not yet persisted as first-class dashboard inputs.
5. Same-day fills currently require retaining `filled_avg_price` in broker/order artifacts.

## Recommended Build Sequence

### Phase 1. KPI data contract

Define a single dashboard data contract with these sections:

- `headline_performance`
- `portfolio_expression`
- `trade_quality`
- `attribution`
- `system_health`
- `series`
- `tables`

### Phase 2. Measurement completion

Before UI polish:

- persist trade-level realized/open P&L
- persist concentration/deployment metrics
- backfill benchmark-relative history
- fill the canonical performance layer gaps

### Phase 3. Static dashboard

Build as a static artifact generated on the VM:

- HTML/CSS/JS only
- no backend required for V1
- generated JSON written alongside the page
- served from the GCP VM via nginx or a simple static file path

### Phase 4. Hosting

Preferred VM deployment shape:

- generate dashboard data after confirmation phase
- write static assets to a stable directory such as `/var/www/caerus-dashboard/`
- serve via nginx
- optionally protect with basic auth or IP allowlist

## Recommendation

For this system, the homepage should optimize for operator truth, not aesthetics:

- benchmark-relative performance first
- portfolio expression second
- trade quality third
- ops health always visible

If we do that correctly, the dashboard will answer the real morning questions in under 30 seconds instead of forcing an email-and-artifact hunt.
