# TP/SL Analysis: 2026-03-23 Precompute Bundle

## Scope
- Source payload: `outputs/precompute/2026-03-23/planned_execution_payload.json` fetched from the VM via SSH.
- Production files inspected for calculation logic:
  - `daily_quant_report.py`
  - `core/quant_report.py`
  - `paper/paper_broker.py`
- No production code was changed.

## Executive Summary
- The CVX take-profit / stop-loss ratio is not a one-off bug. It is the expected result of the current global configuration:
  - `STOP_ATR_MULT_DEFAULT = 2.0`
  - `TAKE_PROFIT_ATR_MULT_DEFAULT = 3.0`
- When ATR is available, `build_daily_snapshot()` computes:
  - `stop_loss = entry_price - 2.0 * ATR` for long positions
  - `take_profit = entry_price + 3.0 * ATR` for long positions
  - and the inverse for short positions
- This produces a fixed reward/risk ratio of `3 / 2 = 1.5` for every ATR-based name.
- As of `2026-03-23`, the regime snapshot was:
  - `composite_regime = risk_off_defensive`
  - `volatility_state = elevated`
  - `market_analyzer.regime = ELEVATED`
  - `market_analyzer.signal_bucket = DEFENSIVE`
- The TP/SL logic is not regime-aware. The same ATR multipliers are used regardless of whether the market is calm or elevated-vol defensive.

## Where TP/SL Is Calculated

### Primary calculation path
- `daily_quant_report.py:3146-3151`
  - Reads `STOP_ATR_MULT`, `TAKE_PROFIT_ATR_MULT`, `STOP_PCT`, and `TAKE_PROFIT_PCT`.
- `daily_quant_report.py:3272`
  - Builds the ATR map using `_build_atr_map(prices, report_date)`.
- `daily_quant_report.py:3344-3363`
  - Applies ATR-based TP/SL when ATR is present.
  - Falls back to fixed percentages only when ATR is missing.
- `core/quant_report.py:194-204`
  - `add_atr(prices, window=14)` computes ATR as a simple 14-day rolling mean of true range.

### Sanity guard
- `daily_quant_report.py:3375-3407`
  - Clips obviously invalid TP/SL levels so they remain on the correct side of entry.
  - This is a guardrail, not a regime model.

### Not used in calculation
- `paper/paper_broker.py`
  - No TP/SL calculation logic found.
  - It does not determine the levels; it only consumes planned trade artifacts.

## Formula Classification
- ATR lookback: `14`
- Stop multiplier: `2.0`
- Take-profit multiplier: `3.0`
- Fallback when ATR is unavailable:
  - stop: `8%`
  - take-profit: `12%`
- Conclusion:
  - The live March 23 bundle is ATR-based wherever TP/SL is populated.

## Per-Instrument TP/SL Review

| Ticker | Payload Side | Entry | Stop | Take | SL % | TP % | R:R | Classification |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| CB | BUY | 322.58 | 310.28 | 341.03 | 3.81% | 5.72% | 1.50 | ATR-based |
| COP | BUY | 126.92 | 120.72 | 136.22 | 4.88% | 7.33% | 1.50 | ATR-based |
| CVX | BUY | 201.73 | 193.59 | 213.94 | 4.04% | 6.05% | 1.50 | ATR-based |
| DELL | BUY | 157.67 | 143.04 | 179.61 | 9.28% | 13.92% | 1.50 | ATR-based |
| TRV | BUY | 296.60 | 283.82 | 315.78 | 4.31% | 6.47% | 1.50 | ATR-based |
| WBD | SELL | 27.42 | 26.72 | 28.47 | 2.56% | 3.83% | 1.50 | ATR-based, but levels reflect the underlying long position rather than the sell order action |
| WDC | BUY | 293.10 | 252.46 | 354.06 | 13.87% | 20.80% | 1.50 | ATR-based |
| MPC | SELL | 232.41 | null | null | n/a | n/a | n/a | removed_from_targets; no TP/SL attached |
| MU | SELL | 422.67 | null | null | n/a | n/a | n/a | removed_from_targets; no TP/SL attached |
| PSX | SELL | 175.38 | null | null | n/a | n/a | n/a | removed_from_targets; no TP/SL attached |

## CVX Specifically
- Entry: `201.73`
- Stop: `193.59`
- Take: `213.94`
- Stop distance: `8.14`
- Take distance: `12.21`
- Reward/risk ratio: `12.21 / 8.14 = 1.50`
- Implied ATR:
  - `8.14 / 2 = 4.07`
  - `12.21 / 3 = 4.07`
- Conclusion:
  - CVX is internally consistent with the current ATR formula.
  - The issue is not a miscalculation unique to CVX.

## Important Consistency Notes

### 1. The ratio is globally fixed at 1.5 for ATR-based names
- This was true for every populated TP/SL row in the March 23 bundle.
- That is a design choice, not an anomaly.

### 2. Three names have no TP/SL at all
- `MPC`, `MU`, and `PSX` were `removed_from_targets` sell orders and carried `null` stop/take fields.
- This happens because risk levels are built from the current non-cash target weights in `build_daily_snapshot()`, not from every order row in `planned_execution_payload.json`.
- This is not necessarily wrong for planned exits, but it is inconsistent if the payload is expected to expose TP/SL uniformly for all instruments.

### 3. Order side is not always the same as position direction
- `WBD` appears as `side = SELL`, but its TP/SL still reflects a long-position geometry (`take > entry > stop`).
- That is because the payload `side` is the order action, while the risk level is attached by ticker from the held position snapshot.
- This is consistent with the current data flow, but it is easy to misread during audits.

## Regime Appropriateness
- March 23 was explicitly tagged `ELEVATED / DEFENSIVE`.
- In that environment, a static `2 ATR / 3 ATR` template is a weak default because it ignores the portfolio context:
  - If position size is unchanged, tighter stops can increase whipsaw risk.
  - If volatility is elevated, a constant ATR multiple may still be too mechanically uniform across names and regimes.
  - A defensive regime usually calls for one of:
    - wider stops with smaller size
    - unchanged stops with materially smaller size
    - lower gross exposure plus stricter entry selection
- The current system already reduces exposure at the portfolio level through regime allocation and cash weight, but TP/SL itself does not adapt.

## Recommendation
- Yes: TP/SL parameters should be regime-aware, or the position-sizing layer should explicitly absorb regime volatility so TP/SL can remain static for a principled reason.
- Preferred design:
  - keep a consistent risk budget per position
  - widen stops in elevated-vol / defensive states
  - reduce position size proportionally so dollar risk does not rise
- At minimum, add a research pass for:
  - `risk_on_trending`: current `2 ATR / 3 ATR` may be acceptable
  - `risk_off_defensive` and `high_volatility`: test wider stop multipliers and/or lower size

## Executed Regime-Aware Scenario
- Research script run: `research/regime_aware_tpsl_2026-03-23.py`
- Output artifact: `research/regime_aware_tpsl_results_2026-03-23.json`
- For the actual March 23 `ELEVATED / DEFENSIVE` snapshot, the recommended research scenario is:
  - stop: `2.5 ATR`
  - take-profit: `3.75 ATR`
  - size scale: `0.80x`
- Why this setting:
  - it keeps the reward/risk ratio at `1.5`
  - it widens the stop by `25%`
  - it reduces notional by `20%`
  - it preserves aggregate dollar stop risk almost exactly

### Portfolio impact of the executed scenario
- Current ATR-based names in the bundle:
  - total notional: `$2,313.46`
  - aggregate stop-risk dollars: `$137.56`
- Recommended `2.5 / 3.75 ATR` scenario:
  - adjusted notional: `$1,850.77`
  - aggregate stop-risk dollars: `$137.56`
- Stress `3.0 / 4.5 ATR` scenario for comparison:
  - adjusted notional: `$1,542.31`
  - aggregate stop-risk dollars: `$137.56`

### Per-name effects under the recommended scenario
| Ticker | Current Shares | Adjusted Shares (float) | Adjusted Shares (floor) | New SL % | New TP % | Adjusted Notional |
|---|---:|---:|---:|---:|---:|---:|
| CB | 1 | 0.80 | 0 | 4.77% | 7.15% | $258.06 |
| COP | 3 | 2.40 | 2 | 6.10% | 9.16% | $304.61 |
| CVX | 2 | 1.60 | 1 | 5.05% | 7.57% | $322.77 |
| DELL | 2 | 1.60 | 1 | 11.60% | 17.39% | $252.27 |
| TRV | 1 | 0.80 | 0 | 5.39% | 8.08% | $237.28 |
| WBD | 11 | 8.80 | 8 | 3.19% | 4.79% | $241.30 |
| WDC | 1 | 0.80 | 0 | 17.33% | 26.00% | $234.48 |

### Practical implication
- The regime-aware ATR math is internally clean, but integer-share constraints matter.
- Several 1-share names (`CB`, `TRV`, `WDC`) round down to `0` shares if size is reduced purely by a `0.80x` scalar.
- That means a production rollout should not just shrink shares blindly; it should:
  - either rebalance notional at the portfolio-construction layer before order generation
  - or apply a minimum-position rule so regime-aware sizing does not zero out otherwise valid names

## Bug Assessment
- No direct TP/SL arithmetic bug was found in the March 23 CVX case.
- The values in the payload match the implemented formula exactly.
- Potential next-session issues to review, but not fix today:
  - whether removed-from-target sells should also carry normalized risk metadata
  - whether the payload should distinguish order action from position direction more clearly
  - how regime-aware TP/SL should interact with integer-share sizing and minimum position thresholds
