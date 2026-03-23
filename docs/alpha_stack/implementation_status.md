# Alpha Stack Implementation Status

## Current Program Status

Alpha Stack is active as a documented multi-sleeve platform baseline, but implementation is uneven across sleeves and layers.

## By Area

| Area | Status | Notes |
|---|---|---|
| Daily orchestrator | Active | `daily_quant_report.py` runs the daily process and report generation. |
| Shared quant utilities | Active | `core/quant_report.py` provides shared loaders, indicators, and report helpers. |
| Portfolio allocation baseline | Active | `core/portfolio_alloc.py` holds the 80/20 Sleeve 1/Sleeve 2 baseline. |
| Sleeve 1 | Partial | Factor functions are still stubs in `core/quant_report.py`. |
| Sleeve 2 | Implemented | Uses snapshot yfinance P/E, SGOV cash proxy, ranking thresholds, and hold-day logic. |
| Sleeve 3 | Planned | Not implemented. |
| Sleeve 4 | Planned | Not implemented. |
| Regime layer | Partial concept | Four-dimension design exists, but explicit state-machine thresholds and hysteresis are not complete. |
| Attribution layer | Planned / partial | IC/IR and performance reporting are part of the target design but not yet a full promotion-grade layer. |

## Technical Debt

1. Sleeve 2 backtests are biased by snapshot P/E.
2. Sleeve 2 backtest output needs a daily equity curve.
3. Sleeve 1 factor functions remain unimplemented.
4. Transaction costs are not modeled.
5. Portfolio-level risk controls are not yet fully encoded.
6. Benchmark comparison is still missing from reports.

## Planned Sequence

1. Data foundation.
2. Regime state machine.
3. Sleeve 1 extension.
4. Sleeve 2 PIT refactor.
5. Attribution.
6. Allocator v1 with regime overrides.
7. Quality sleeve.
8. Mean Reversion sleeve.
9. Shadow validation.
10. Production cutover.

## Promotion Ladder

`research -> backtest -> shadow -> paper -> live`
