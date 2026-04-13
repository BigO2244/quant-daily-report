# Alpha Stack Sleeve Specifications

## Current Sleeve Table

| Sleeve | Role | Status | Current Notes |
|---|---|---|---|
| Sleeve 1 | Trend / Momentum | Research only | Shared factor pipeline stubs remain in `core/quant_report.py`; output discarded in live path. |
| Sleeve 2 | Value | Live | Uses snapshot yfinance P/E against industry-relative ranking and hold-day rules. |
| Sleeve 3 | Quality | Live | Included in regime-aware allocation. |
| Sleeve 4 | Mean Reversion | Live | Included in regime-aware allocation; breadth-gated. |
| Defensive ETF Sleeve | Bonds / capital preservation | Live-capable (Phase 3A confirmed) | Uses `SGOV`, `SHY`, `IEF`, `TLT`; regime-gated to `risk_off_defensive`, `high_volatility`, `breadth_washout`; freed weight routes to cash only when invalid. |

## Sleeve 1

Intended role:

- trend and momentum sleeve
- expected to expand with sector-relative signals
- ATR-based sizing planned

Current state:

- `fetch_factor_data`, `build_factor_scores`, and `compute_full_signals` in `core/quant_report.py` are still stubs
- the full Sleeve 1 signal implementation is not included in this handoff

## Sleeve 2

Current implementation:

- valuation source: yfinance `.info`
- valuation metric in use: snapshot P/E
- cross-sectional logic: industry-relative z-score behavior
- portfolio controls: score ranks, z-score thresholds, and hold-day limits
- cash proxy: `SGOV`

Critical caveat:

- snapshot P/E is not point-in-time correct for historical simulation
- historical backtests should not be trusted until the fundamental cache is rebuilt with filing-aware data

## Sleeve 3

Live — quality sleeve. Included in the regime-aware multi-sleeve allocator.

## Sleeve 4

Live — mean reversion sleeve. Included in the regime-aware multi-sleeve allocator; breadth-gated.

## Defensive ETF Sleeve

Current implementation:

- live-capable sleeve using liquid Treasury ETFs
- ETF set: `SGOV`, `SHY`, `IEF`, `TLT`
- activates in defensive regimes instead of staying entirely in cash
- uses regime state plus simple recent-return / realized-vol diagnostics to tilt duration exposure

Current caveat:

- this is an initial deterministic defensive sleeve, not a fully researched duration-rotation model
- it is meant to reduce dead cash in risk-off states while preserving auditability

## Common Promotion Ladder

`research -> backtest -> shadow -> paper -> live`

## Known Issues

1. Sleeve 2 needs point-in-time fundamental data.
2. Sleeve 2 backtests need a full equity curve.
3. Sleeve 1 implementation is incomplete.
4. No transaction cost model exists for sleeve-level backtests.
