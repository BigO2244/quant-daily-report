# Model Strategy

## Overview

Alpha Stack is a multi-sleeve regime-switching equity platform. It generates a daily HTML report and builds a portfolio from sleeve-level signals rather than from a single monolithic strategy.

The current baseline portfolio targets:

- 80% Sleeve 1
- 20% Sleeve 2
- $10,000 baseline capital

## Sleeve 1: Trend / Momentum

Status: partially implemented.

Current facts:

- Factor pipeline stubs live in `core/quant_report.py`
- Stubbed functions are `fetch_factor_data`, `build_factor_scores`, and `compute_full_signals`
- The `sleeves/sleeve_1/` signal logic is not part of this handoff

Design intent:

- Trend and momentum-driven entry/exit logic
- Sector-relative extension planned
- ATR-based sizing planned

## Sleeve 2: Value

Status: fully implemented.

Current behavior:

- Value signal uses yfinance `.info` snapshot P/E
- P/E is compared relative to industry via z-score logic
- Entry/exit behavior is governed by score ranks, z-score thresholds, and hold-day limits
- `SGOV` is used as the cash proxy

Important caveat:

- This implementation is not point-in-time correct for historical backtests
- Backtest results should not be trusted for promotion decisions until fundamental timing is fixed

## Planned Sleeves

| Sleeve | Status | Notes |
|---|---|---|
| Sleeve 3: Quality | Planned | Signals not yet defined |
| Sleeve 4: Mean Reversion | Planned | Signals not yet defined |

## Regime Layer Intent

The regime layer is designed as a four-dimension classifier:

- trend
- volatility
- breadth
- macro

The intended future implementation is a state machine with explicit thresholds and hysteresis. That state-machine behavior is not yet fully implemented.

## Portfolio Construction

Current allocation baseline is configured in `core/portfolio_alloc.py`.

Near-term design goals:

- static sleeve weights first
- regime-aware overrides second
- risk controls and attribution after the initial allocator contract is stable

## Promotion Ladder

All strategy changes should follow:

`research -> backtest -> shadow -> paper -> live`

The legacy model remains frozen until Alpha Stack is validated.

## Known Issues

1. Sleeve 2 backtests contain look-ahead bias due to snapshot P/E inputs.
2. Sleeve 2 backtests do not yet produce a full daily equity curve.
3. Sleeve 1 factor pipeline functions are still stubs.
4. No transaction cost model is included in backtests.
5. No benchmark comparison is surfaced in the daily report.

## Planned Sequence

1. Add point-in-time correct fundamentals and FRED macro inputs.
2. Build the regime state machine.
3. Extend Sleeve 1.
4. Refactor Sleeve 2 to PIT-safe multi-metric value.
5. Add attribution.
6. Add allocator v1 regime overrides.
7. Implement Sleeve 3.
8. Implement Sleeve 4.
