# Flow Detection v1

## Scope

This is a DEV-only research model. It does not modify production trading behavior, cron schedules, allocator outputs, or live/paper execution paths.

The hypothesis is that unusual price-confirmed volume can identify institutional flow and improve a momentum portfolio.

## Signal Definition

For each ticker and trading day:

- `r1 = close_t / close_{t-1} - 1`
- `r3 = close_t / close_{t-3} - 1`
- `vol_mean_20 = mean(volume_{t-20:t-1})`
- `vol_std_20 = std(volume_{t-20:t-1})`
- `volume_z = (volume_t - vol_mean_20) / vol_std_20`

`flow_active = TRUE` when all are true:

- `volume_z > 1.5`
- `r1 > 0.005`
- `r3 > 0.015`

Optional v1.1:

- `efficiency = r1 / max(volume_z, floor)`
- `flow_active_v1_1 = flow_active AND efficiency > cross-sectional median efficiency on that date`

## Methodology

The research harness produces three outputs:

1. Event study
   - forward 1d / 3d / 5d / 10d returns
   - max adverse move over the next 5d / 10d
   - comparison cohorts: unconditional, momentum-only, flow-active, flow-active-v1.1

2. Strategy comparison
   - baseline: equal-weight top-N momentum score
   - flow-filtered: equal-weight top-N momentum score with flow-active priority
   - momentum score uses a research-only proxy consistent with existing trend logic:
     - skip-adjusted `r12_1`, `r6_1`, `r3_1`
     - trend flag from `EMA50 > EMA200`
     - ATR-normalized composite score

3. Randomized historical window robustness testing
   - 2y / 3y / 5y / 10y window sampling
   - repeated backtests over sampled historical windows
   - metrics summarized across windows

## Data Assumptions

- Price and volume only.
- No fundamentals.
- PIT-safe to the extent supported by the historical OHLCV dataset.
- Feature windows use only information available on or before each signal date.
- Forward returns are aligned strictly after the signal date.

## How To Run

Preferred module entrypoint:

```bash
python -m research.flow_detection.run \
  --start-date 2014-01-01 \
  --end-date 2026-04-22 \
  --top-n 10 \
  --window-years 2 3 5 10 \
  --num-sims 25 \
  --output-dir outputs/research/flow_detection_v1
```

Wrapper script:

```bash
python scripts/research/run_flow_detection_v1.py --help
```

## Artifacts

Outputs are written under `outputs/research/flow_detection_v1/`:

- `summary.json`
- `signals.parquet`
- `event_study.csv`
- `event_study_summary.json`
- `backtest_baseline.json`
- `backtest_flow_filtered.json`
- `backtest_baseline_nav.csv`
- `backtest_flow_filtered_nav.csv`
- `randomized_window_results.csv`
- `randomized_window_summary.json`
- `report.md`

## Limitations

- Research-only loader may fetch missing history with yfinance. This is intentionally isolated from production datastore behavior.
- The current fallback behavior for the flow-filtered portfolio is documented and explicit; it is not a production execution rule.
- Randomized windows are historical window sampling, not a synthetic Monte Carlo generator.
- Transaction costs are simplified and applied as turnover-based drag in the research backtest only.
- The CLI defaults to `25` simulations per horizon for reasonable local runtime; increase `--num-sims` when you want a deeper robustness sweep.

## Promotion Criteria

This model should only be considered for future shadow testing if all are true:

- flow-filtered momentum beats momentum-only on risk-adjusted return
- results remain robust across randomized windows
- there is no obvious single-regime fragility driving the effect
- turnover remains operationally acceptable
- event sample size is large enough to support confidence in the result

No production integration should happen before a separate explicit promotion review.
