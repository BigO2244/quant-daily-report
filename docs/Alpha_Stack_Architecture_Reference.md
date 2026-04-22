# Alpha Stack Architecture Reference

This document is the current architecture reference for Alpha Stack. It reflects the present implementation state, the active design intent, and the staged promotion path for future work.

## Current Named Strategy State

- `Caerus Polaris` / `caerus_polaris`
  - current paper baseline / operational control
- `Caerus Orion` / `caerus_orion`
  - primary shadow candidate
  - derived from the Alpha Lab v2 lead combination: H2 rank-decay exit + H6 top-5 concentration
- `Caerus Lyra` / `caerus_lyra`
  - secondary shadow challenger
  - derived from the Alpha Lab v2 challenger combination: H1 weekly rebalance + H6 top-5 concentration
- `SPY` / `spy_benchmark`
  - benchmark

Current promotion state:
- Polaris = paper
- Orion = shadow only
- Lyra = shadow only
- promotion ladder remains `research -> backtest -> shadow -> paper -> live`

## Purpose and Governance

Alpha Stack is a regime-switching quantitative trading platform built as a multi-sleeve system. It is intended to replace the idea of a single strategy script with a layered platform that can explain decisions at the sleeve, regime, portfolio, execution, and attribution levels.

Non-negotiable guardrails:

- Preserve deterministic artifacts.
- Keep production-safety promotion stages explicit.
- Do not treat planned sleeves as implemented.
- Do not trust non-PIT Sleeve 2 backtests as production evidence.

## Current Architecture

Seven layers, in order:

1. Data Layer: yfinance OHLCV plus snapshot fundamentals. FRED macro integration is planned.
2. Feature Layer: derived indicators computed from raw data.
3. Signal Layer: per-sleeve entry and exit scores.
4. Regime Layer: four-dimension classifier across trend, volatility, breadth, and macro. A hysteresis-driven state machine is planned but not fully implemented.
5. Portfolio Construction Layer: sleeve weighting and position sizing.
6. Execution Layer: order generation and trade tracking.
7. Attribution Layer: IC/IR measurement and performance reporting.

## Current Sleeve Definitions

| Sleeve | Role | Status | Notes |
|---|---|---|---|
| Sleeve 1 | Trend/Momentum | Partial | Factor pipeline stubs remain in `core/quant_report.py`; signal logic under `sleeves/sleeve_1/` is outside this handoff. |
| Sleeve 2 | Value | Implemented | Uses yfinance `.info` snapshot P/E with industry-relative z-scores, hold-day limits, score ranks, and SGOV cash proxy. |
| Sleeve 3 | Quality | Planned | Signals not yet defined. |
| Sleeve 4 | Mean Reversion | Planned | Signals not yet defined. |

## Current Portfolio Construction Baseline

- Baseline notional capital: $10,000
- Static sleeve weights: 80% Sleeve 1 / 20% Sleeve 2
- Configuration location: `core/portfolio_alloc.py`

Current design intent:
- Sleeve budgets are blended in portfolio construction.
- Regime overrides are planned but not yet fully encoded as a hysteresis-driven allocator.
- Execution and trade tracking remain downstream of the target portfolio construction process.

## Key Files

| File | Role |
|---|---|
| `daily_quant_report.py` | Daily orchestrator |
| `core/quant_report.py` | Shared data, indicator, and report utilities |
| `core/portfolio_alloc.py` | Sleeve scaling and portfolio combining |
| `sleeves/sleeve_2/config.py` | Sleeve 2 parameters |
| `sleeves/sleeve_2/valuation.py` | Snapshot P/E fetch and cache |
| `sleeves/sleeve_2/signals.py` | Sleeve 2 score computation |
| `sleeves/sleeve_2/backtest.py` | Sleeve 2 backtest |
| `data/universe.csv` | 200-ticker universe with sector tags |

## Promotion Ladder

All new strategies and features follow:

`research -> backtest -> shadow -> paper -> live`

The legacy model remains frozen while Alpha Stack is validated in parallel.

## Shadow Automation

- Successful precompute now triggers a best-effort shadow run through `scripts/run_shadow_candidates_daily.sh`.
- The wrapper is called from `scripts/cron_precompute.sh`.
- Outputs land under:
  - `outputs/shadow_candidates/YYYY-MM-DD/`
  - `outputs/shadow_candidates/performance/`
- Shadow failures are logged and swallowed; production execution does not depend on shadow success.

## Known Issues / Technical Debt

1. Look-ahead bias: Sleeve 2 uses yfinance snapshot P/E rather than point-in-time historical fundamentals.
2. Sleeve 2 backtests currently return only the final equity point instead of a daily curve.
3. `fetch_factor_data`, `build_factor_scores`, and `compute_full_signals` in `core/quant_report.py` are stubs.
4. The regime layer still lacks explicit thresholds, state boundaries, and hysteresis rules in code.
5. No transaction cost model is applied to backtests.
6. No portfolio-level risk controls are yet defined for position caps, sector limits, or drawdown circuit breakers.
7. Benchmark comparison versus SPY or S&P 500 total return is missing from reports.
8. The repository contains `requirements.txt`, but dependency pinning and environment-governance expectations still need explicit review.

## Planned Implementation Sequence

1. Data foundation: FRED macro integration plus point-in-time correct fundamental caching.
2. Regime state machine: four-dimension classifier with hysteresis.
3. Trend sleeve: extend Sleeve 1 with sector-relative signals and ATR-based sizing.
4. Value sleeve: refactor Sleeve 2 with PIT-correct fundamentals and multi-metric composite.
5. Attribution module: IC/IR measurement before adding more sleeves.
6. Allocator v1: static weights with regime overrides.
7. Quality sleeve.
8. Mean Reversion sleeve.
9. Shadow mode: 60+ trading days alongside legacy.
10. Production cutover and legacy archive.
