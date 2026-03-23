# Alpha Stack Documentation

This directory tracks the current Alpha Stack design, implementation status, and promotion path.

## Current State

- Alpha Stack is a regime-switching multi-sleeve platform.
- It produces a daily HTML email report.
- Current baseline allocation is 80% Sleeve 1 and 20% Sleeve 2 on a $10,000 notional baseline.
- Sleeve 1 is partially implemented.
- Sleeve 2 is fully implemented but its historical backtests are not point-in-time safe.
- Sleeves 3 and 4 are planned only.

## Source of Truth

Primary reference:

- `docs/Alpha_Stack_Architecture_Reference.md`

Supporting documents:

- `docs/alpha_stack/architecture_overview.md`
- `docs/alpha_stack/sleeve_specifications.md`
- `docs/alpha_stack/regime_allocator_spec.md`
- `docs/alpha_stack/data_standards.md`
- `docs/alpha_stack/research_validation_spec.md`
- `docs/alpha_stack/implementation_status.md`
- `docs/alpha_stack/alpha_stack_v1_deliverables.md`

## Promotion Ladder

`research -> backtest -> shadow -> paper -> live`

Alpha Stack should continue alongside the frozen legacy model until promotion criteria are met.

## Known Issues / Technical Debt

1. Sleeve 2 uses snapshot yfinance P/E and therefore introduces look-ahead bias in backtests.
2. Sleeve 2 backtest output needs a full daily curve.
3. Sleeve 1 factor functions remain stubs in `core/quant_report.py`.
4. The regime layer is not yet a coded hysteresis-driven state machine.
5. Backtests are gross only.

## Planned Sequence

1. Data foundation.
2. Regime state machine.
3. Trend sleeve extension.
4. Value sleeve PIT refactor.
5. Attribution module.
6. Allocator v1.
7. Quality sleeve.
8. Mean Reversion sleeve.
9. Shadow mode validation.
10. Production cutover.
