# Model Strategy

## Overview

Alpha Stack is a multi-sleeve regime-switching equity platform. It generates a daily HTML report and builds a portfolio from sleeve-level signals rather than from a single monolithic strategy.

Current live reality:

- `sleeve_trend`, `sleeve_2` (value), `sleeve_quality`, and `sleeve_mean_reversion` feed the live allocator
- `sleeve_1` still runs as a research lane and its output is not yet part of the live book
- live sleeve budgets are conditioned on the regime layer, then filtered by sleeve activity and broker-drift thresholds
- the live posture is now explicitly aggressive: cash is kept near zero in risk-on, and only true defensive regimes may route cash to the Treasury sleeve
- legacy static weights in `core/portfolio_alloc.py` are not the live source of truth

## Sleeve 1: Research Momentum Ranker

Status: implemented as research, not currently live.

Current facts:

- Factor pipeline stubs live in `core/quant_report.py`
- Stubbed functions are `fetch_factor_data`, `build_factor_scores`, and `compute_full_signals`
- The `sleeves/sleeve_1/` signal logic is not part of this handoff

Design intent:

- Trend and momentum-driven entry/exit logic
- Sector-relative extension planned
- ATR-based sizing planned

## Sleeve 2: Value

Status: implemented and part of the live allocator.

Current behavior:

- Value signal uses yfinance `.info` snapshot P/E
- P/E is compared relative to industry via z-score logic
- Entry/exit behavior is governed by score ranks, z-score thresholds, and hold-day limits
- `SGOV` is used as the cash proxy

Important caveat:

- This implementation is not point-in-time correct for historical backtests
- Backtest results should not be trusted for promotion decisions until fundamental timing is fixed

## Additional Live Sleeves

| Sleeve | Status | Notes |
|---|---|---|
| Sleeve 3: Quality | Live | Included in regime-aware allocation |
| Sleeve 4: Mean Reversion | Live | Included in regime-aware allocation and breadth-gated |

## Regime Layer Intent

The regime layer is a four-dimension classifier:

- trend
- volatility
- breadth
- macro

It is implemented in `regime/` with explicit thresholds and EWM smoothing. Phase 1 (live regime-aware allocator) is operationally confirmed as of 2026-04-09. The main remaining gap is promotion-grade attribution and richer operator diagnostics, not basic regime classification.

### Promotion Gate

`core/live_regime_review.py` writes a `promotion_gate` block to each run's `live_regime_review.json`. The gate evaluates blocking conditions before a regime change is eligible for operator promotion to a higher trust level. Key checks:

- regime stability (hysteresis window cleared)
- sleeve drift within threshold (≥3% min before rebalance triggers)
- no active execution blockers in `operator_summary.json`

`regime_review_status` and `regime_review_blockers` are surfaced in `operator_summary.json` for each run. A clear `promotion_gate` does not auto-promote — it signals operator review is warranted.

## Portfolio Construction

Live allocation is orchestrated in `daily_quant_report.py` by:

- building sleeve outputs
- computing regime-conditioned target sleeve weights
- applying a broker-drift threshold before rebalancing
- passing active sleeve strengths into `PortfolioAllocator`

`core/portfolio_alloc.py` still provides the combining and constraint engine, but its static helper defaults are legacy-only.

## Promotion Ladder

All strategy changes should follow:

`research -> backtest -> shadow -> paper -> live`

The legacy model remains frozen until Alpha Stack is validated.

## Known Issues

1. Sleeve 2 backtests contain look-ahead bias due to snapshot P/E inputs.
2. The research `alpha_stack` allocator is richer than the live path, so production and research are not yet fully aligned.
3. Sleeve 1 remains research-only despite being part of the long-term sleeve roadmap.
4. No transaction cost model is included in most sleeve-level backtests.
5. Sleeve-level attribution and overlap diagnostics remain weaker than required for promotion-grade regime evaluation.

## Planned Sequence

1. ~~Harden the existing live regime-aware allocator with tests and operator diagnostics.~~ — **Done (Phase 1, confirmed 2026-04-09).**
2. Extend Sleeve 1 into a promotable live candidate or formally replace it.
3. Refactor Sleeve 2 to PIT-safe multi-metric value.
4. Add promotion-grade attribution and overlap diagnostics.
5. ~~Introduce options overlays after the equity allocator is stable, starting with a shadow-only SPY hedge overlay lane.~~ — **Done (Phase 2A shadow-only, confirmed 2026-04-09). No live execution path exists.**
5b. Phase 2B — paper/promotion review for options overlays is now in progress; it produces paper-ready review artifacts only and still does not submit orders.
5c. Phase 2C — live options execution lane is now scaffolded as a gated, disabled-by-default path for protective SPY puts only; daily runs submit only when `ALLOW_OPTIONS_EXECUTION=1` or `ALLOW_OPTIONS_SUBMISSION=1` is explicitly set.
6. ~~Add bond / defensive ETF sleeves, starting with a live-capable defensive Treasury ETF sleeve that can absorb defensive regime cash.~~ — **Done (Phase 3A, confirmed 2026-04-09).**
7. Bias the live allocator toward participation first: lower cash, tighter defensive routing, and stronger trend weighting in bullish regimes.
