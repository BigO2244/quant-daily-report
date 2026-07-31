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

The daily CIO scorecard reads the broker-derived Paper equity chain from
`outputs/perf/live_overlay_nav_series.csv`. Every populated `return_1d` must
equal `equity[t] / equity[t-1] - 1` within numeric tolerance. If that
reconciliation fails, the scorecard labels Paper performance corrupt and does
not display the reported return.

## Shadow scorecard return contract

Shadow returns are hypothetical model returns. They are not Paper account P&L,
even when a Shadow strategy is called the baseline. The scorecard must present
Paper and Shadow in separate sections and disclose differences in holdings and
cash exposure.

For each completed Shadow session:

- use the canonical `dated_same_day_close_to_close_v1` convention;
- calculate each position contribution as
  `target_weight * close_to_close_return`;
- calculate the model return as the sum of position contributions, with
  residual cash earning 0%;
- fail closed if any positively weighted security or SPY lacks a finite return;
- reconcile the displayed daily return to both dated
  `shadow_performance.json` and the canonical Shadow NAV series;
- display material-move attribution when the absolute daily return is at least
  5%; and
- source promotion language only from the dated, research-only
  `promotion_readiness.json`. Headline outperformance must never create a
  promotion signal.

A displayed `7-Day` return compounds seven completed close-to-close intervals
and therefore requires eight valid closing NAV observations. With fewer than
eight observations, the value is unavailable rather than shortened and still
labeled `7-Day`. The same convention applies to Paper and Shadow.

Post-close refresh must append the canonical NAV row before rebuilding
evaluation, longitudinal, stability, and promotion-readiness artifacts. The
complete dated bundle is then published to `latest` as one unit. If NAV append
or bundle preflight fails, `latest` is not advanced.

## Attribution artifacts
`reporting/attribution.py` generates:
- `outputs/perf/contribution_tickers_{asof}.csv`
- `outputs/perf/contribution_sleeves_{asof}.csv`

## Rebuild / rerun
- Rebuild from ledger: `python3 scripts/rebuild_portfolio_from_ledger.py --asof YYYY-MM-DD`
- Full smoke: `bash scripts/smoke_reporting.sh`
