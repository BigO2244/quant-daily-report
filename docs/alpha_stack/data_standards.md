# Alpha Stack Data Standards

## Current Data Layer

Today the data layer uses:

- yfinance OHLCV history
- yfinance snapshot fundamentals

Planned extension:

- FRED macro data integration
- point-in-time correct fundamental caching

## Current Data Risks

1. Sleeve 2 uses snapshot yfinance `.info` P/E.
2. Snapshot fundamentals are not point-in-time safe for historical backtests.
3. Historical validation should not be treated as promotion evidence until filed-date-aware fundamentals are available.

## Required Direction

Data standards for Alpha Stack should move toward:

- as-of-date semantics
- filed-date aware fundamentals
- deterministic caches
- auditable feature provenance

## Current Open Work

1. Add FRED macro data to support the macro dimension of the regime layer.
2. Replace snapshot P/E with point-in-time correct fundamentals, ideally via EDGAR XBRL or equivalent filed-date-aware cache design.
3. Ensure future caches carry enough metadata to prevent look-ahead bias in research and backtests.

## Promotion Note

Alpha Stack should remain on the promotion ladder below paper/live claims until the data layer is point-in-time trustworthy:

`research -> backtest -> shadow -> paper -> live`
