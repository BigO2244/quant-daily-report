# Flow Detection v2

## Purpose

Flow Detection v1 failed as an entry model. V2 follows that evidence instead of trying to rescue it with threshold-mining.

This version tests three narrower ideas:

1. slower participation may matter more than single-day spike volume
2. participation may help exits more than entries
3. participation may only matter in certain regimes

This remains DEV-only research. No production allocation, execution, cron, or promotion behavior is changed.

## What Changed From v1

- v1 used single-day abnormal volume plus short price confirmation as an entry filter
- v2 replaces that with slower participation measures:
  - `volume_z_3d_avg`
  - `volume_z_5d_avg`
  - `signed_participation = volume_z * r1`
  - `accumulation_3d`
  - `accumulation_5d`
  - persistence counts over 3d / 5d
- v2 also defines an exit-style exhaustion cohort:
  - `extended_momentum`
  - `exhaustion_flow`

## Signal Definitions

Base quantities:

- `r1 = close_t / close_{t-1} - 1`
- `r3 = close_t / close_{t-3} - 1`
- `volume_z = (volume_t - mean(volume_{t-20:t-1})) / std(volume_{t-20:t-1})`

Slower participation:

- `signed_participation_t = volume_z_t * r1_t`
- `volume_z_3d_avg = mean(volume_z over last 3 days)`
- `volume_z_5d_avg = mean(volume_z over last 5 days)`
- `accumulation_3d = sum(signed_participation over last 3 days)`
- `accumulation_5d = sum(signed_participation over last 5 days)`
- `persistent_participation_3d = at least 2 of last 3 days have positive signed participation`
- `persistent_participation_5d = at least 3 of last 5 days have positive signed participation`

Entry-style overlay:

- `participation_entry_signal = momentum_only AND (slower_participation_3d OR slower_participation_5d)`

Exit-style overlay:

- `extended_momentum = top 20% momentum rank and positive recent 5d return`
- `exhaustion_flow = extended_momentum AND elevated current/short-window participation`

Regime-conditional overlay:

- apply the participation entry overlay only in `strong_up` / `weak_up` and `normal` volatility bucket

## Regime Method

V2 uses a DEV-only hybrid regime adapter:

- primary source: existing research regime history artifacts under `outputs/regime_*`
- fallback: price-derived trend and volatility proxy from SPY history

This avoids refactoring production regime code.

## How To Run

```bash
python -m research.flow_detection.v2_run \
  --start-date 2014-01-01 \
  --end-date 2026-04-22 \
  --top-n 10 \
  --window-years 2 3 5 10 \
  --num-sims 25 \
  --output-dir outputs/research/flow_detection_v2 \
  --price-cache-path outputs/research/flow_detection_v1/price_panel.parquet
```

Wrapper:

```bash
python scripts/research/run_flow_detection_v2.py --help
```

## Artifacts

- `summary.json`
- `signals.parquet`
- `event_study.csv`
- `event_study_summary.json`
- `backtest_baseline.json`
- `backtest_participation_entry.json`
- `backtest_participation_exit.json`
- `backtest_regime_conditional_participation.json`
- `randomized_window_results.csv`
- `randomized_window_summary.json`
- `report.md`

## Promotion Criteria

No production promotion should be considered unless:

- an overlay clearly beats baseline on risk-adjusted return
- that result remains robust across randomized windows
- the benefit is not just a single-regime artifact
- turnover remains operationally acceptable

If those are not true, the correct answer is still no.
