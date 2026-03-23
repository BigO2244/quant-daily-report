# Alpha Stack Current Deliverables

This file summarizes the current documented deliverables for Alpha Stack rather than a future or completed build.

## Current Deliverables

- Daily HTML report generation via `daily_quant_report.py`
- Shared quant utilities in `core/quant_report.py`
- Portfolio allocation baseline in `core/portfolio_alloc.py`
- Sleeve 2 implementation in:
  - `sleeves/sleeve_2/config.py`
  - `sleeves/sleeve_2/valuation.py`
  - `sleeves/sleeve_2/signals.py`
  - `sleeves/sleeve_2/backtest.py`
- Universe definition in `data/universe.csv`

## Partial Deliverables

- Sleeve 1 trend/momentum architecture is partially represented, but key factor functions remain stubs in `core/quant_report.py`.
- Regime-aware architecture is defined, but the final hysteresis-driven state machine is still pending.
- Attribution is a planned layer, not yet a complete promotion-grade module.

## Planned Deliverables

1. FRED macro integration and point-in-time correct fundamentals.
2. Regime state machine with hysteresis.
3. Extended Sleeve 1 with sector-relative signals and ATR sizing.
4. PIT-safe Sleeve 2 multi-metric value refactor.
5. IC/IR attribution layer.
6. Allocator v1 with regime overrides.
7. Quality sleeve.
8. Mean Reversion sleeve.

## Promotion Ladder

`research -> backtest -> shadow -> paper -> live`

## Known Issues

1. Sleeve 2 backtests are not point-in-time safe.
2. Sleeve 2 backtests need a daily curve.
3. Sleeve 1 remains incomplete.
