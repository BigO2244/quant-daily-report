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

## Paper trading state

Paper broker runtime state (ledger/trades) is stored in `outputs/paper_state/` by default.
Set `PAPER_STATE_DIR` to override this location.
Legacy `paper/ledger.csv` and `paper/trades.csv` are treated only as one-time seed inputs when state files are first initialized.

## Run Archiving

Daily runs now use immutable canonical artifacts under `outputs/runs/<RUN_ID>/`.
`outputs/latest.json` is a mutable pointer to the latest run.
See `docs/run_archiving.md` for structure, integrity files, and reproduction steps.

## Alpha standalone report (local)

```bash
python3 scripts/alpha_report.py --apply-costs --cost-bps 25
python3 scripts/daily_alpha_run.py
SMTP_HOST=... SMTP_PORT=... SMTP_USER=... SMTP_PASSWORD=... REPORT_TO_EMAIL=... python3 scripts/email_alpha_report.py
```

Notes:
- Requires `matplotlib` (plus pandas/numpy).
- Uses artifacts in `outputs/research` as source inputs.

## Audit Workflows

Audit export and Monte Carlo workflows are documented in `docs/audit.md`.

## Alpaca Paper Execution

Set credentials and paper toggle:

```bash
export TRADING_MODE=alpaca
export ALPACA_PAPER=1
export ALPACA_API_KEY_ID="YOUR_KEY"
export ALPACA_API_SECRET_KEY="YOUR_SECRET"
```

Smoke-test connectivity:

```bash
python3 alpaca_smoke_test.py
# or
python3 scripts/alpaca_smoke_test.py
```

Run a daily report:

```bash
REPORT_DATE=2026-02-24 PAPER_TRADING=1 MODE=SHADOW python3 daily_quant_report.py
```

Note: `REPORT_DATE` must be a real date string (`YYYY-MM-DD`), not placeholders like `YYYY-MM-DD`.

## Local Green Loop

```bash
bash scripts/local_green_loop.sh
```
