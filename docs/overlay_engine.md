# Overlay Engine

## Build

```bash
python -m research.overlay_engine.build_overlay_engine
```

Output:

- `outputs/overlay_engine/overlay_backtest.csv`

## Data Inputs

Preferred canonical source order:

1. Explicit `--canonical-csv` if provided
2. `outputs/alpha_assessment/canonical_performance.csv`
3. Rebuild from canonical source artifacts via alpha assessment performance layer

Explicit local source overrides:

- `--strategy-csv`
- `--benchmark-csv`

Synthetic fallback is opt-in only with `--allow-synthetic`.

Builder logs explicitly indicate `MODE=REAL DATA MODE` or `MODE=EXPLICIT SYNTHETIC MODE`.

## No-Lookahead Rule

`run_overlay_backtest(..., enforce_lag=True)` shifts overlay multiplier by one day before return application.

- Trade-date returns use prior signal date overlay state.
- Disable only for diagnostics via `--no-lag`.
- Builder logs warn when `--no-lag` is used.
