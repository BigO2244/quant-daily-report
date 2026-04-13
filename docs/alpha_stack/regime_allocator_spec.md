# Alpha Stack Regime Allocator Specification

## Current Intent

The regime layer is intended to classify market state across four dimensions:

- trend
- volatility
- breadth
- macro

That regime context is intended to feed portfolio construction and sleeve weighting.

## Current Status — Phase 1 Operationally Confirmed (2026-04-09)

- The four-dimension classifier is implemented in `regime/regime_classifier.py`.
- Daily sleeve targets are implemented in `regime/regime_allocator.py` with explicit thresholds from `regime/regime_config.py` and EWM transition smoothing as the primary hysteresis mechanism.
- The live daily path in `daily_quant_report.py` maps regime targets into production sleeves (`sleeve_trend`, `sleeve_2`, `sleeve_quality`, `sleeve_mean_reversion`, `sleeve_defensive_etf`) and applies a 3% broker-drift threshold before rebalancing.
- `core/live_regime_review.py` writes 4 auditable artifacts per run: `live_regime_review.json`, dated JSON, latest JSON, and operator markdown.
- `promotion_gate` in `live_regime_review.json` contains blockers that must be clear before operator promotion; regime review status and blockers are surfaced in `operator_summary.json`.
- `core/portfolio_alloc.py` still contains legacy static helper defaults, but those defaults are no longer the source of truth for live sleeve budgets.
- The live posture is now intentionally aggressive: cash budgets are reduced in bullish regimes, and defensive cash routing is reserved for truly defensive states rather than generic washouts.

## Near-Term Goal

Build a rules-based regime state machine before any optimization-led allocator work.

Required qualities:

- explicit thresholds
- explicit state boundaries
- deterministic transitions
- hysteresis to reduce whipsaw
- operator-readable outputs

## Interaction With Portfolio Construction

The current live implementation does:

- classify the daily state
- adjust sleeve budgets
- smooth target transitions across regime changes
- preserve explicit operator-readable outputs in the daily report

Remaining production gaps:

- sleeve-level attribution is still limited relative to the target-book diagnostics in `alpha_stack`
- promotion-grade shadow-vs-live diagnostics for regime-driven reallocations are still thin
- the research `alpha_stack` allocator remains richer than the production allocator in modifier depth and turnover controls

## Planned Sequence

1. ~~Promote the current live regime path to an explicit first-class contract with tests and operator docs.~~ — **Done (Phase 1, confirmed 2026-04-09).**
2. Expand sleeve-level attribution and regime-transition diagnostics.
3. Decide whether to port selected `alpha_stack` allocator modifiers into the live path.
4. Add non-equity sleeves only after the live multi-sleeve equity allocator is promotion-grade.

## Known Gaps

1. Live sleeve routing still relies on production-specific sleeve-name mapping in `daily_quant_report.py`.
2. Overlapping holdings across sleeves need explicit attribution logic and tests.
3. Promotion-grade regime attribution outputs are still incomplete.
