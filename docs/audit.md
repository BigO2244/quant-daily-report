# Audit Export Workflows

This repo now supports deterministic audit bundles and policy/Monte Carlo workflows.

## Environment knobs

- `AUDIT_EXPORT=1|0` (default `1`)
- `AUDIT_RUN_ID=<string>` (optional prefix; used by `run_audit_2022_and_worst.py`)
- `AUDIT_OUTDIR=outputs/audit` (base directory)
- `BACKTEST_START=YYYY-MM-DD`
- `BACKTEST_END=YYYY-MM-DD`
- `BREAKER_POLICY=FULL|PARTIAL|LOCK`
- `MC_N=<int>` (default `200`)
- `MC_WINDOW_YEARS=3|5` (default `3`)
- `MC_METRIC=MAX_DD|CAGR|ULCER` (default `MAX_DD`)
- `MC_SEED=<int>` (default `42`)

## Commands

Run 2022 FULL audit + Monte Carlo worst-window audit:

```bash
python3 scripts/run_audit_2022_and_worst.py
```

Run backtest mode via main entrypoint and verify artifacts:

```bash
python3 scripts/run_backtest_audit.py --start 2022-01-01 --end 2022-12-31 --run-id 2022_full --policy FULL
python3 scripts/verify_audit_outputs.py --run-id 2022_full --start 2022-01-01 --end 2022-12-31
```

`run_backtest_audit.py` defaults to synthetic data (`BACKTEST_SYNTHETIC=1`) for deterministic CI/local verification.
Use `--live-data` to force live market-data downloads.

Run Monte Carlo windows and persist worst window:

```bash
python3 scripts/run_mc_and_pick_worst.py
```

Run policy compare (FULL/PARTIAL/LOCK) for 2022:

```bash
python3 scripts/run_policy_compare_2022.py
```

## Artifacts

Audit bundle per run id:

- `outputs/audit/<RUN_ID>/trades.csv`
- `outputs/audit/<RUN_ID>/holdings_daily.csv`
- `outputs/audit/<RUN_ID>/portfolio_daily.csv`
- `outputs/audit/<RUN_ID>/audit.xlsx` with sheets:
  - `Summary`
  - `Trades`
  - `HoldingsDaily`
  - `PortfolioDaily`

Monte Carlo outputs:

- `outputs/research/random_windows_<years>y_<policy>.csv`
- `outputs/research/random_windows_summary_<policy>.csv`
- `outputs/research/worst_window_<policy>.json`

Policy compare outputs:

- `outputs/research/policy_compare_2022.csv`
- `outputs/research/policy_compare_2022_equity_curves.csv`
- `outputs/research/policy_compare_mc_worst.csv`
- `outputs/research/policy_compare_mc_worst_equity_curves.csv`

## Daily Entrypoint Backtest Mode

`daily_quant_report.py` enters backtest mode when `BACKTEST_START`/`BACKTEST_END` (or CLI equivalents) are set.

Behavior:
- Skips daily schedule/paper execution/email flow.
- Runs deterministic backtest path and (when `AUDIT_EXPORT=1`) writes `outputs/audit/<RUN_ID>/...`.
- Emits:
  - `[BREAKER_POLICY] ...`
  - `[BACKTEST_MODE] start=... end=... policy=... audit_out=...`
