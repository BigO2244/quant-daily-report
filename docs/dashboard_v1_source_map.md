# Dashboard V1 Source Map

## Purpose

Tie every V1 dashboard field to one exact artifact and extraction path.

This document is the implementation bridge between
[dashboard_v1_spec.md](/Users/brettolson/Documents/Caerus/quant-daily-report-main/docs/dashboard_v1_spec.md)
and code changes in `scripts/research/build_quant_dashboard.py`.

The goal is to remove ambiguity. If a field does not have a single canonical
source here, it should not appear in V1.

## Scope

V1 covers only:

- `positions`
- `nav`
- `trades_today`
- `performance_history`

## Canonical Source Policy

For V1, each section must use one primary source family:

- `positions`: broker position snapshot
- `nav`: broker account snapshot
- `trades_today`: same-day filled execution artifact
- `performance_history`: governed NAV and benchmark history

Current source quality by section:

- `positions`: ready
- `nav`: ready
- `performance_history`: ready
- `trades_today`: blocked pending canonical fill-level source

## Existing Artifact Reality

Local workspace artifacts confirm the following live shapes:

- `outputs/broker/broker_snapshot_latest.json`
  - contains broker account values including `equity`, `cash`, `buying_power`,
    `last_equity`, `positions_count`, `trade_date`, `captured_at`,
    `trust_level`
- `outputs/broker/posttrade_positions.json`
  - contains `positions_count`, `trade_date`, `captured_at`, and a `positions`
    list with `symbol`, `qty`, `side`, `cost_basis`, `current_price`,
    `market_value`, `unrealized_pl`, `unrealized_plpc`
- `outputs/perf/nav_timeseries.csv`
  - contains `date`, `equity`, `cash`, `gross_exposure`, `net_exposure`,
    `return_1d`, `turnover_dollars`, `turnover_pct`
- `outputs/perf/benchmark_close_history.csv`
  - contains `date`, `spy_close`, `spy_return`
- `outputs/execution_email/<date>.json`
  - currently appears to contain planned trades on sampled local files, not
    canonical fills
- `outputs/broker/orders_<date>.csv`
  - currently appears to contain accepted order intents, not fill-level truth

## Section Map

### `sections.positions`

Purpose:

- current held positions, broker-authoritative

Primary artifact:

- `outputs/broker/posttrade_positions.json`

Fallback:

- none for V1

Extractor:

- new V1 extractor should read the file directly
- do not derive current positions from `trading_day_summary`
- do not derive current positions from `paper_state`

Field map:

| V1 field | Artifact path | Raw field(s) | Notes |
|---|---|---|---|
| `positions.as_of` | `outputs/broker/posttrade_positions.json` | `captured_at` | fallback to top-level `as_of` only if added later |
| `positions.source_type` | derived constant | `broker_positions` | constant in builder |
| `positions.trust_level` | `outputs/broker/broker_snapshot_latest.json` or constant | `trust_level` | use `authoritative`/`canonical` only when broker artifact says so |
| `positions.is_stale` | derived | compare `as_of` to build time | V1 validation should compute this |
| `positions.summary.positions_count` | `outputs/broker/posttrade_positions.json` | `positions_count` | if missing, use `len(positions)` |
| `positions.summary.gross_market_value` | `outputs/broker/posttrade_positions.json` | `sum(position.market_value)` | sum absolute market values |
| `positions.summary.net_market_value` | `outputs/broker/posttrade_positions.json` | `sum(signed market_value)` | same as gross for long-only |
| `positions.summary.cash` | `outputs/broker/broker_snapshot_latest.json` | `cash` | do not read from performance history |
| `positions.summary.largest_position_weight` | derived | max row weight | requires NAV source |
| `positions.summary.top5_concentration` | derived | top 5 row weights sum | requires NAV source |
| `positions.rows[].ticker` | `outputs/broker/posttrade_positions.json` | `positions[].symbol` | normalize to upper-case |
| `positions.rows[].side` | `outputs/broker/posttrade_positions.json` | `positions[].side` | expected `long` today |
| `positions.rows[].qty` | `outputs/broker/posttrade_positions.json` | `positions[].qty` | float-cast |
| `positions.rows[].avg_entry_price` | derived | `cost_basis / qty` | only when both present and qty nonzero |
| `positions.rows[].last_price` | `outputs/broker/posttrade_positions.json` | `positions[].current_price` | float-cast |
| `positions.rows[].market_value` | `outputs/broker/posttrade_positions.json` | `positions[].market_value` | float-cast |
| `positions.rows[].cost_basis` | `outputs/broker/posttrade_positions.json` | `positions[].cost_basis` | float-cast |
| `positions.rows[].unrealized_pnl` | `outputs/broker/posttrade_positions.json` | `positions[].unrealized_pl` | float-cast |
| `positions.rows[].unrealized_pnl_pct` | `outputs/broker/posttrade_positions.json` | `positions[].unrealized_plpc` | float-cast |
| `positions.rows[].weight` | derived | `market_value / nav.equity` | requires NAV source |

Implementation note:

- current builder already reads posttrade positions indirectly for counts in
  `_normalize_broker_snapshot()` and position diagnostics in `build()`, but it
  does not expose a clean V1 positions section

### `sections.nav`

Purpose:

- current broker account truth

Primary artifact:

- `outputs/broker/broker_snapshot_latest.json`

Fallback:

- none for V1

Extractor:

- reuse normalization logic conceptually from
  `DashboardBuilder._normalize_broker_snapshot()`
- but V1 should read the canonical broker snapshot directly and fail closed if
  it is missing

Field map:

| V1 field | Artifact path | Raw field(s) | Notes |
|---|---|---|---|
| `nav.as_of` | `outputs/broker/broker_snapshot_latest.json` | `captured_at` or `as_of` | prefer `captured_at` |
| `nav.source_type` | derived constant | `broker_account` | constant in builder |
| `nav.trust_level` | `outputs/broker/broker_snapshot_latest.json` | `trust_level` | expected `authoritative` today |
| `nav.is_stale` | derived | compare `as_of` to build time | computed in validation |
| `nav.equity` | `outputs/broker/broker_snapshot_latest.json` | `equity` or `account.equity` | headline NAV |
| `nav.cash` | `outputs/broker/broker_snapshot_latest.json` | `cash` or `account.cash` | float-cast |
| `nav.long_market_value` | `outputs/broker/broker_snapshot_latest.json` | `market_value` | long-only assumption for V1 |
| `nav.short_market_value` | derived constant | `0.0` | until shorts exist |
| `nav.gross_exposure` | derived | `market_value / equity` | not from perf csv |
| `nav.net_exposure` | derived | `long_market_value / equity` | long-only today |
| `nav.buying_power` | `outputs/broker/broker_snapshot_latest.json` | `buying_power` or `account.buying_power` | `null` if absent |
| `nav.day_pnl` | `outputs/broker/broker_snapshot_latest.json` | `equity - last_equity` | derived from same source family |
| `nav.day_return` | `outputs/broker/broker_snapshot_latest.json` | `(equity / last_equity) - 1` | only when `last_equity` present |

Implementation note:

- the current builder mixes governed and broker account views for different
  panels; V1 should keep `nav` strictly broker-sourced

### `sections.trades_today`

Purpose:

- actual same-day fills only

Primary artifact target:

- canonical execution fills artifact for the report date

Current state:

- blocked

Why blocked:

- `outputs/execution_email/<date>.json` currently contains planned trades on the
  sampled local files, not executed fills
- `outputs/broker/orders_<date>.csv` currently exposes accepted order intent
  fields like `status=accepted`, not fill price/time truth
- local sampled `outputs/runs/*/execution_results.json` did not expose a recent
  fill-level canonical payload

V1 rule:

- do not ship `trades_today` from planned trades or accepted orders
- if canonical fills are unavailable, render the section unavailable and mark
  the artifact degraded

Required canonical fill fields for V1:

| V1 field | Required raw field |
|---|---|
| `trades_today.rows[].filled_at` | fill timestamp |
| `trades_today.rows[].ticker` | symbol |
| `trades_today.rows[].side` | buy/sell |
| `trades_today.rows[].qty` | filled quantity |
| `trades_today.rows[].fill_price` | average fill price |
| `trades_today.rows[].notional` | fill price * qty |
| `trades_today.rows[].order_id` | order id |
| `trades_today.rows[].client_order_id` | client order id |
| `trades_today.rows[].source_execution_id` | fill/execution id if available |

Required implementation work before V1 trade section can be trusted:

1. identify or create a canonical same-day fills artifact
2. persist fills with explicit report date and ET timestamps
3. distinguish `planned`, `submitted`, `accepted`, and `filled` states
4. update the builder to reject non-fill trade sources for V1

Interim builder behavior:

- `trades_today.source_type = "missing"`
- `trades_today.trust_level = "missing"`
- `trades_today.rows = []`
- validation check `trades_source_present` should fail or warn according to the
  report date and execution status

### `sections.performance_history`

Purpose:

- governed historical NAV and SPY-relative history

Primary artifacts:

- `outputs/perf/nav_timeseries.csv`
- `outputs/perf/benchmark_close_history.csv`

Fallback:

- none for V1

Extractor:

- reuse core parsing ideas from `DashboardBuilder._load_performance_dataset()`
- but restrict V1 to governed series only
- do not use live overlay history in V1

Field map:

| V1 field | Artifact path | Raw field(s) | Notes |
|---|---|---|---|
| `performance_history.as_of` | `outputs/perf/nav_timeseries.csv` | last row `date` | |
| `performance_history.source_type` | derived constant | `governed_nav_history` | constant in builder |
| `performance_history.trust_level` | derived constant | `canonical` | governed persisted performance |
| `performance_history.is_stale` | derived | compare last nav date to report date | validation check |
| `performance_history.summary.inception_date` | `outputs/perf/nav_timeseries.csv` | first row `date` | after filtering invalid leading rows |
| `performance_history.summary.latest_nav` | `outputs/perf/nav_timeseries.csv` | last row `equity` | should tie to nav only when same-date |
| `performance_history.summary.since_inception_return` | derived | `(last_equity / first_equity) - 1` | |
| `performance_history.summary.spy_since_inception_return` | `outputs/perf/benchmark_close_history.csv` | aligned `spy_close` first/last | aligned date range only |
| `performance_history.summary.excess_since_inception_return` | derived | portfolio SI minus SPY SI | |
| `performance_history.summary.max_drawdown` | derived | min running drawdown from NAV | |
| `performance_history.series.nav[]` | `outputs/perf/nav_timeseries.csv` | `date`, `equity` | raw dollars |
| `performance_history.series.daily_return[]` | `outputs/perf/nav_timeseries.csv` | `return_1d`, else derived from prior `equity` | |
| `performance_history.series.spy_close[]` | `outputs/perf/benchmark_close_history.csv` | `date`, `spy_close` | |
| `performance_history.series.nav_indexed[]` | derived | rebased `nav` | base 100 |
| `performance_history.series.spy_indexed[]` | derived | rebased aligned `spy_close` | base 100 |
| `performance_history.series.excess_return_cumulative[]` | derived | aligned cumulative excess return | aligned dates only |
| `performance_history.series.drawdown[]` | derived | running NAV drawdown | |

Implementation note:

- current builder already has most of this in `_load_performance_dataset()` and
  `_build_perf_summary()`, but V1 should strip it down and remove overlay logic

## Validation Mapping

This section ties V1 validation checks to their source paths.

| Validation check | Source(s) | Rule |
|---|---|---|
| `positions_source_present` | `outputs/broker/posttrade_positions.json` | file exists, parseable, has `positions` list |
| `nav_source_present` | `outputs/broker/broker_snapshot_latest.json` | file exists, parseable, has `equity` |
| `performance_source_present` | `outputs/perf/nav_timeseries.csv`, `outputs/perf/benchmark_close_history.csv` | both exist and are parseable |
| `trades_source_present` | canonical fills artifact | currently expected to fail until source is formalized |
| `positions_sum_matches_nav` | positions + broker snapshot | sum market values + cash within tolerance of equity |
| `positions_weights_sum_reasonable` | positions + broker snapshot | sum weights approximately equals exposure |
| `history_latest_nav_matches_nav_section` | nav history + broker snapshot | only compare if dates represent same report date |
| `trades_are_report_date_only` | fills artifact | all fills must fall on report date in ET |
| `performance_series_monotonic_dates` | nav + benchmark csv | no duplicate or descending dates |
| `spy_dates_aligned` | nav + benchmark csv | derived excess/indexed series use aligned dates only |

## Code Mapping

Current relevant code paths:

- `scripts/research/build_quant_dashboard.py`
  - `DashboardBuilder._normalize_broker_snapshot()`
  - `DashboardBuilder._artifact_broker_snapshot()`
  - `DashboardBuilder._load_performance_dataset()`
  - `build_dashboard_payload()`
- `core/trading_day_summary.py`
  - useful for observability context, but not a primary V1 source for positions,
    NAV, or performance history

Implementation guidance:

- add dedicated V1 extractors instead of extending the current mixed legacy
  payload builder further
- let legacy dashboard code coexist during migration
- do not source V1 basics from `trading_day_summary.json` except as diagnostic
  context

## Recommended Next Code Step

Implement a new V1 builder path that:

1. reads `posttrade_positions.json`
2. reads `broker_snapshot_latest.json`
3. reads `nav_timeseries.csv` and `benchmark_close_history.csv`
4. emits a `trades_today` unavailable state until canonical fills are wired
5. runs the blocking validation checks before publish
