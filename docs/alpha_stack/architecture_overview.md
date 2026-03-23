# Alpha Stack Architecture Overview

## Purpose

This document describes the current Alpha Stack platform layout and the intended next steps for the architecture.

## Seven Layers

1. Data Layer
   yfinance OHLCV plus snapshot fundamentals today; FRED macro integration is planned.
2. Feature Layer
   Derived indicators built from raw price and fundamental inputs.
3. Signal Layer
   Per-sleeve entry/exit scores and ranking outputs.
4. Regime Layer
   Trend, volatility, breadth, and macro classification. The final state machine and hysteresis rules are still planned work.
5. Portfolio Construction Layer
   Sleeve weighting and symbol-level position sizing.
6. Execution Layer
   Order generation, order tracking, and downstream trade handling.
7. Attribution Layer
   IC/IR measurement and performance reporting.

## Current Operating Baseline

- Baseline capital: $10,000
- Baseline sleeve mix: 80% Sleeve 1 / 20% Sleeve 2
- Report builder and orchestrator: `daily_quant_report.py`
- Shared utilities: `core/quant_report.py`
- Allocator baseline: `core/portfolio_alloc.py`

## Sleeve Status

| Sleeve | Status | Current State |
|---|---|---|
| Sleeve 1 | Partial | Factor pipeline stubs remain in `core/quant_report.py`; handoff does not include the complete sleeve signal implementation. |
| Sleeve 2 | Implemented | yfinance snapshot P/E versus industry, rank- and threshold-driven holds/exits, SGOV cash proxy. |
| Sleeve 3 | Planned | Not implemented. |
| Sleeve 4 | Planned | Not implemented. |

## Architecture Notes

- The platform should be described as multi-sleeve, not as a single script.
- The current regime layer is conceptual plus partial scaffolding, not a completed state machine.
- Point-in-time correctness is the main blocker for trusting Sleeve 2 historical results.
- Attribution is part of the target architecture, but not yet complete enough to serve as a promotion gate.

## Promotion Ladder

`research -> backtest -> shadow -> paper -> live`

## Planned Build Sequence

1. Data foundation: FRED plus point-in-time fundamentals.
2. Regime state machine with hysteresis.
3. Sleeve 1 extension.
4. Sleeve 2 PIT-safe refactor.
5. Attribution build-out.
6. Allocator v1 regime overrides.
7. Quality sleeve.
8. Mean Reversion sleeve.
