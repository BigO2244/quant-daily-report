# Alpha Stack Regime Allocator Specification (v1 Rules-Based)

Purpose
- Specify the regime state machine, transition thresholds, hysteresis rules, and sleeve budget mapping for Alpha Stack v1.

Scope
- Defines trend, volatility, breadth, and macro state dimensions.
- Defines transition logic and sleeve allocation table.
- Applies to Alpha Stack research/shadow only.

Assumptions
- Allocator v1 is rules-based and interpretable.
- Hysteresis is mandatory to prevent state whipsaw.
- Production allocator remains unchanged until promotion.

Status
- Baseline rules spec approved for implementation planning.

Future Work
- Add confidence-weighted blending once v1 shadow is stable.
- Evaluate optimizer overlays only after rules-based v1 proves robust.

## 1. State Dimensions

Trend state (price trend strength)
- `strong_up`
- `weak_up`
- `neutral`
- `weak_down`
- `strong_down`

Volatility state (market stress)
- `calm`
- `normal`
- `elevated`
- `crisis`

Breadth state
- `healthy`
- `mixed`
- `deteriorating`
- `washed_out`

Macro state
- `supportive`
- `neutral`
- `restrictive`

Regime context object
- `trend_state`
- `vol_state`
- `breadth_state`
- `macro_state`
- `state_confidence` in `[0,1]`
- `entered_at`
- `bars_in_state`

## 2. Baseline Thresholds

Trend classification (SPY example baseline)
- Inputs:
  - `T1 = (Price / EMA200) - 1`
  - `T2 = (EMA50 / EMA200) - 1`
- Rules:
  - `strong_up` if `T1 >= 0.03` and `T2 >= 0.01`
  - `weak_up` if `T1 >= 0.00` and `T2 >= 0.00`
  - `neutral` if `-0.02 < T1 < 0.00`
  - `weak_down` if `T1 <= -0.02` and `T2 <= 0.00`
  - `strong_down` if `T1 <= -0.05` and `T2 <= -0.01`

Volatility classification (VIX)
- `calm`: `VIX < 16`
- `normal`: `16 <= VIX < 22`
- `elevated`: `22 <= VIX < 30`
- `crisis`: `VIX >= 30`

Breadth classification (example)
- Inputs: `% members above 200DMA` and A/D slope.
- `healthy`: `%>200DMA >= 65` and A/D slope positive
- `mixed`: `45-65`
- `deteriorating`: `30-45` or negative breadth slope
- `washed_out`: `< 30`

Macro classification (example)
- Inputs: policy proxy, credit spread trend, growth nowcast.
- `supportive`, `neutral`, `restrictive` from weighted score buckets.

## 3. Hysteresis and Transition Rules

General rules
- Minimum dwell time: 5 trading days before any state downgrade/upgrade confirmation.
- Two-close confirmation: threshold breach must persist for 2 consecutive closes.
- Reversion buffer: state exit threshold differs from entry by buffer `b`.

Example hysteresis buffers
- Trend buffer: 0.5% on `T1` and `T2`.
- Volatility buffer: 1.5 VIX points around boundaries.
- Breadth buffer: 3 percentage points around each bucket boundary.

Transition constraints
- No direct jump from `strong_up` to `strong_down` in one day; must pass intermediate state.
- `crisis` volatility can force immediate risk reduction even if dwell time not met.

## 4. Sleeve Budget Mapping

Budgets are target sleeve allocations before portfolio construction constraints.

Base budgets by trend state
- `strong_up`: trend 0.55, value 0.20, quality 0.15, mean_reversion 0.10
- `weak_up`: trend 0.45, value 0.25, quality 0.20, mean_reversion 0.10
- `neutral`: trend 0.35, value 0.25, quality 0.30, mean_reversion 0.10
- `weak_down`: trend 0.20, value 0.20, quality 0.50, mean_reversion 0.10
- `strong_down`: trend 0.10, value 0.15, quality 0.65, mean_reversion 0.10

Volatility modifiers
- `calm`: no modifier
- `normal`: no modifier
- `elevated`: multiply mean_reversion by 0.5; add released budget to quality
- `crisis`: set mean_reversion to 0.0; reduce trend by 30%; move released budget to cash

Breadth modifiers
- `healthy`: +5% to trend, funded pro-rata from value/quality
- `deteriorating`: -5% trend, +5% quality
- `washed_out`: disable new trend entries; allow only hold/trim

Macro modifiers
- `supportive`: +5% value, -5% cash reserve
- `restrictive`: +5% cash reserve, reduce value by 5%

Normalization
- Sleeve budgets are re-normalized after modifiers.
- Net long cap: 95% max; residual to cash.

## 5. State-Driven Execution Intent

Allocator emits
- Sleeve budgets
- Position change intensity (`low`, `medium`, `high`)
- Turnover throttle scalar in `[0,1]`

Turnover smoothing
- Daily budget change cap: absolute 5% per sleeve per day.
- Any larger shift is phased over multiple days unless `crisis` trigger.

## 6. Validation and Promotion Gates

Backtest gates
- Regime transition count not excessive (no whipsaw bursts beyond threshold)
- Cost-adjusted Sharpe improvement over static sleeve blend
- Max drawdown not worse than baseline by > 10%

Shadow gates (minimum 60 trading days)
- Regime decisions reproducible and explainable daily
- Sleeve budget drift follows documented transition logic
- No undocumented overrides

Fail conditions
- Frequent boundary oscillation violating hysteresis target
- Unexplained budget jumps > 5% per sleeve/day without crisis event
- Regime logic requiring manual interpretation to explain behavior

## 7. Explicit Deferrals

Future phase only
- ML regime classifier replacement
- Optimizer-driven dynamic budgets
- Options overlays as regime response
