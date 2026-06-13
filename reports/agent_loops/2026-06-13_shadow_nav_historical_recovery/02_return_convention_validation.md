# 02 Return Convention Validation

## Code-Level Findings

Two materially different return conventions are present under the same `weights_as_of_t` label.

The dated incremental performance path in `research/shadow_tracking/run.py` computes:

```text
daily_return_t = target_weights_t * close_to_close_return(previous_trading_day -> t)
nav_t = previous_nav * (1 + daily_return_t)
```

This is implemented by `compute_returns_for_trade_date()` using `pct_change()` at `trade_date`, then multiplying by the same dated strategy payload target weights.

The full historical CSV writer uses the backtest path:

```text
return_t = target_weights_t * close_to_close_return(t -> next_trading_day) - turnover_cost_t
nav_t = previous_nav * (1 + return_t)
```

This is implemented by `prepare_backtest_inputs()` using `pct_change().shift(-1)` and by `run_backtest_prepared()` applying the forward return before writing the NAV row for `dt`.

## Pre-Incident Parity

The dated `shadow_performance.json` chain is exactly reproducible from same-day target weights and point-in-time prices.

| Check | Dates | Count | Max Abs Diff | Result |
|---|---|---:|---:|---|
| same-day returns vs dated `shadow_performance.json` | 2026-06-02 through 2026-06-05 | 16 | 0.0 | pass |
| same-day returns vs active `shadow_nav_series.csv` ratios | 2026-06-02 through 2026-06-05 | 16 | 0.1828578125 | fail |
| forward returns vs active `shadow_nav_series.csv` ratios | 2026-06-02 through 2026-06-05 | 16 | 0.0060271191 | fail |

The forward convention explains some historical CSV rows, but not the `2026-06-05` row with deterministic tolerance.

| Date | Strategy | CSV Ratio | Forward Return | Difference |
|---|---|---:|---:|---:|
| 2026-06-03 | caerus_polaris | -0.0187408236 | -0.0187408188 | 0.0000000048 |
| 2026-06-03 | caerus_orion | -0.0307703003 | -0.0307702907 | 0.0000000096 |
| 2026-06-03 | caerus_lyra | -0.0295709540 | -0.0295709444 | 0.0000000096 |
| 2026-06-03 | spy_benchmark | 0.0037786867 | 0.0037786867 | 0.0000000000 |
| 2026-06-04 | caerus_polaris | -0.0913261641 | -0.0913493497 | -0.0000231856 |
| 2026-06-04 | caerus_orion | -0.1078402511 | -0.1078866221 | -0.0000463710 |
| 2026-06-04 | caerus_lyra | -0.1084981115 | -0.1085444826 | -0.0000463711 |
| 2026-06-04 | spy_benchmark | -0.0258093996 | -0.0258093996 | 0.0000000000 |
| 2026-06-05 | caerus_polaris | 0.0582178463 | 0.0559932344 | -0.0022246119 |
| 2026-06-05 | caerus_orion | 0.0749711904 | 0.0689440713 | -0.0060271191 |
| 2026-06-05 | caerus_lyra | 0.0713139924 | 0.0662008357 | -0.0051131567 |
| 2026-06-05 | spy_benchmark | 0.0066300789 | 0.0022642301 | -0.0043658488 |

## Affected-Period Parity

The affected dated `shadow_performance.json` daily returns are exactly reproducible from dated weights and point-in-time prices.

| Date | Strategy | Recorded Daily Return | Reconstructed Daily Return | Difference |
|---|---|---:|---:|---:|
| 2026-06-08 | caerus_polaris | 0.0559932344 | 0.0559932344 | 0.0 |
| 2026-06-08 | caerus_orion | 0.0689440713 | 0.0689440713 | 0.0 |
| 2026-06-08 | caerus_lyra | 0.0662008357 | 0.0662008357 | 0.0 |
| 2026-06-08 | spy_benchmark | 0.0022642301 | 0.0022642301 | 0.0 |
| 2026-06-09 | caerus_polaris | -0.0216485092 | -0.0216485092 | 0.0 |
| 2026-06-09 | caerus_orion | -0.0159252315 | -0.0159252315 | 0.0 |
| 2026-06-09 | caerus_lyra | -0.0320993089 | -0.0320993089 | 0.0 |
| 2026-06-09 | spy_benchmark | -0.0029355036 | -0.0029355036 | 0.0 |
| 2026-06-10 | caerus_polaris | -0.0323008847 | -0.0323008847 | 0.0 |
| 2026-06-10 | caerus_orion | -0.0320830411 | -0.0320830411 | 0.0 |
| 2026-06-10 | caerus_lyra | -0.0354408238 | -0.0354408238 | 0.0 |
| 2026-06-10 | spy_benchmark | -0.0157655455 | -0.0157655455 | 0.0 |
| 2026-06-11 | caerus_polaris | 0.0815312959 | 0.0815312959 | 0.0 |
| 2026-06-11 | caerus_orion | 0.0959288759 | 0.0959288759 | 0.0 |
| 2026-06-11 | caerus_lyra | 0.0805873413 | 0.0805873413 | 0.0 |
| 2026-06-11 | spy_benchmark | 0.0169968394 | 0.0169968394 | 0.0 |
| 2026-06-12 | caerus_polaris | 0.0312363251 | 0.0312363251 | 0.0 |
| 2026-06-12 | caerus_orion | 0.0397285140 | 0.0397285140 | 0.0 |
| 2026-06-12 | caerus_lyra | 0.0403637187 | 0.0403637187 | 0.0 |
| 2026-06-12 | spy_benchmark | 0.0054082495 | 0.0054082495 | 0.0 |

## Decision

Daily returns in dated artifacts are PIT-reconstructable. Active `shadow_nav_series.csv` is not recoverable safely from the declared `2026-06-05` anchor in this pass because the anchor row and surrounding CSV history cannot be proven to share a single deterministic convention with the dated recovery inputs.
