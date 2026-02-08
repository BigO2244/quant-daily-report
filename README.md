# Quant Daily Report

## Charlie Munger Sleeve (`charlie_munger`)

This repo now includes a third sleeve focused on long-hold quality accumulation near the 200-week moving average.

### Rules
- **Universe:** S&P 500 constituents (cached at `outputs/cache/charlie_munger/sp500_universe.csv`).
- **Quality screen:** score in `[0, 100]` using market cap, profitability (ROIC/ROE), FCF consistency, and leverage.
- **Entry trigger:** weekly adjusted-close price within `±entry_band` of 200-week SMA (or optional cross-above mode).
- **Portfolio:** long-only, 10-20 holdings, equal or quality-weighted, per-name cap.
- **Rebalance cadence:** monthly or quarterly with trade thresholding.
- **Benchmark:** SPY sleeve-relative tracking (cumulative return and max drawdown).

### Configuration
Default config lives in `sleeves/charlie_munger_config.json` and is loaded by `sleeves/sleeve_charlie_munger.py`.

Key parameters:
- `entry_band`, `ma_weeks`, `rebalance_freq`
- `target_holdings`, `min_holdings`, `max_weight_per_name`, `weighting`
- `quality.min_score`, `quality.exit_min_score`, `quality.consecutive_periods`
- `benchmark` (default `SPY`)

### Integration
- Included in `daily_quant_report.py` run loop.
- Included in dynamic allocation and signals snapshots.
- Included in report output with candidate, buy/sell, and benchmark stats.
