# Performance Reporting

## Ledger schema
Trades are recorded in `outputs/ledger/trades.csv` with append-only semantics and idempotency key `(trade_date, order_id, source)`.

Columns:
`timestamp_et, run_id, source, trade_date, asof_date, order_id, ticker, sleeve, side, quantity, fill_price, notional, fees, reason, signal_hash, status`.

## Positions / cash computation
`paper/positions.py` rebuilds portfolio state from ledger with average-cost accounting:
- BUY updates weighted average cost.
- SELL realizes PnL versus average cost.
- Shares reaching zero reset avg cost to zero.

Cash updates include fees and produce:
- `outputs/ledger/positions_{asof}.csv`
- `outputs/ledger/holdings_{asof}.csv`
- `outputs/ledger/cash_{asof}.json`

## NAV time series
`paper/mark_to_market.py` marks holdings to as-of prices, writes daily NAV JSON, and updates `outputs/perf/nav_timeseries.csv` idempotently per date.

Fields: `date, equity, cash, gross_exposure, net_exposure, return_1d, turnover`.

Turnover is defined as daily ledger notional divided by prior-day equity.

## Attribution artifacts
`reporting/attribution.py` generates:
- `outputs/perf/contribution_tickers_{asof}.csv`
- `outputs/perf/contribution_sleeves_{asof}.csv`

## Rebuild / rerun
- Rebuild from ledger: `python3 scripts/rebuild_portfolio_from_ledger.py --asof YYYY-MM-DD`
- Full smoke: `bash scripts/smoke_reporting.sh`
