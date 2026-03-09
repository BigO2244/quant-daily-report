# Alpha Stack Sleeve Specifications (v1 Baseline)

Purpose
- Define sleeve-level formulas, thresholds, state gates, and promotion standards before implementation.

Scope
- Covers v1 sleeves: Trend/Momentum, Value, Quality, Mean Reversion.
- Defines data dependencies, signal construction, sizing baseline, and acceptance gates.
- Applies to Alpha Stack research/shadow only.

Assumptions
- Point-in-time (PIT) data is mandatory for fundamental sleeves.
- Transaction costs are included in validation (see research spec).
- Production model remains unchanged during sleeve build-out.

Status
- Baseline spec approved for implementation planning.

Future Work
- Add parameter calibration bands from attribution lab.
- Add sector/industry neutralization variants by sleeve.

## Common Sleeve Contract

Each sleeve must emit per symbol/date
- `raw_signals`: primitive indicators used in scoring.
- `score`: normalized score in `[0, 100]`.
- `rank`: descending rank by score.
- `candidate_flag`: boolean after eligibility filters.
- `provisional_weight`: sleeve-local target weight before allocator merge.
- `hold_state`: `enter`, `hold`, `trim`, `exit`.
- `diagnostics`: reason codes and gating outcomes.

Common eligibility gates
- Price >= 5.00 USD
- 20-day ADV >= 100,000 shares
- Tradable universe only
- Data freshness and completeness checks pass

Common weight normalization
- For candidate set `C` with per-name nonnegative score `s_i`:
  - `w_i_raw = s_i / sum_{j in C}(s_j)`
  - `w_i = min(w_i_raw, sleeve_position_cap)`
  - Re-normalize after clipping

## 1. Trend / Momentum Sleeve

Role
- Primary return engine in favorable and neutral regimes.

Core formula
- Returns windows:
  - `r_12_1 = P(t-21)/P(t-252) - 1`
  - `r_6_1 = P(t-21)/P(t-126) - 1`
  - `r_3_1 = P(t-21)/P(t-63) - 1`
- Trend strength:
  - `trend_flag = 1` if `EMA50 > EMA200`, else `0`
- Composite raw score:
  - `S_trend_raw = 0.45 * z(r_12_1) + 0.30 * z(r_6_1) + 0.15 * z(r_3_1) + 0.10 * trend_flag`
- Volatility adjustment:
  - `S_trend_adj = S_trend_raw / max(ATR20_pct, 0.01)`
- Normalize cross-section to `[0, 100]` percentile rank.

Entry/exit thresholds
- Enter candidate if `score >= 70` and `EMA50 > EMA200`.
- Hold if `score >= 55`.
- Exit if `score < 45` or `EMA50 <= EMA200`.

Sizing baseline
- Risk-aware: inverse volatility with floor/ceiling.
- `w_i proportional to 1 / clip(vol_20d, 0.10, 0.60)`.

State definitions
- `enter`: newly passes enter threshold.
- `hold`: remains above hold threshold.
- `trim`: falls from enter zone but still holdable.
- `exit`: breaks exit threshold.

Promotion criteria
- 252d rolling IC mean >= 0.03
- IC t-stat >= 2.0
- Cost-adjusted Sharpe >= 0.8 in backtest and >= 0.6 in shadow
- Turnover <= 20% average daily sleeve turnover

## 2. Value Sleeve

Role
- Style diversification and recovery capture.

Status
- **ACTIVE**: PIT-safe SEC EDGAR XBRL fundamentals wired as of v1.0.1

PIT data requirement
- SEC EDGAR XBRL filing-date-aware fundamentals. Filing delays: 40-45 days (10-Q), 60-75 days (10-K).

Core factors (sector-relative z-score)
- Earnings yield: `EY = NetIncome_TTM / MarketCap`
- Free cash flow yield: `FCFY = (OpCF - CapEx)_TTM / MarketCap`
- Book-to-price: `B/P = Equity / MarketCap`
- **Note**: Shareholder yield (`SHY = BuybackYield + DividendYield`) is unavailable in SEC EDGAR for comprehensive coverage; removed from v1.0 implementation.
- Composite:
  - `S_value_raw = 0.40*z(EY) + 0.30*z(FCFY) + 0.30*z(B/P)`
  - Sector-relative z-scores (group-standardize within sector if sector_map available; else cross-sectional)

Thresholds
- Enter: `score >= 75`
- Hold: `score >= 60`
- Exit: `score < 50`

Sizing baseline
- Equal weight within top-decile candidates, then cap per-name.

Promotion criteria
- PIT audit pass rate = 100%
- No look-ahead violations in random date audits
- Cost-adjusted excess return positive in >= 65% rolling 6-month windows
- Max sleeve drawdown not worse than trend sleeve by > 1.5x

## 3. Quality Sleeve

Role
- Durability and downside resilience.

Core factors
- Profitability: ROE, ROIC
- Balance sheet quality: net leverage (lower is better)
- Margin stability: 3-year margin volatility (lower better)
- Accrual quality: lower accrual ratio preferred

Composite
- `S_quality_raw = 0.30*z(ROE) + 0.25*z(ROIC) - 0.20*z(NetLeverage) - 0.15*z(MarginVol) - 0.10*z(Accruals)`
- Normalize to `[0, 100]` percentile.

Thresholds
- Enter: `score >= 70`
- Hold: `score >= 55`
- Exit: `score < 45`

Sizing baseline
- Equal weight, with optional +25% overweight for top quintile conviction bucket.

Promotion criteria
- Down-capture ratio <= 0.9 vs benchmark in weak/strong-down regimes
- Positive information ratio in both backtest and shadow
- Correlation to trend sleeve <= 0.75 rolling 126d median

## 4. Mean Reversion Sleeve (Conservative)

Role
- Capture short-term dislocations with strict risk controls.

Core factors
- RSI(2), Bollinger z-score (20,2), 5-day reversal, volume shock.

Composite
- `S_mr_raw = -0.35*z(RSI2) - 0.30*z(BB_z) - 0.20*z(r_5d) + 0.15*z(VolumeShock)`
- High score means stronger mean-reversion candidate.

Regime gate (required)
- Enabled only when trend regime in `{weak_up, neutral}` and volatility regime in `{calm, normal}`.

Thresholds
- Enter: `score >= 80`
- Hold: `score >= 65`
- Exit: `score < 55` or regime gate off

Sizing baseline
- Small equal weights; sleeve budget capped at 10% initial allocator budget.

Promotion criteria
- Positive expectancy after costs with 95% confidence on trade-level distribution
- No tail-loss event beyond pre-defined stop-loss envelope in shadow window
- Turnover and slippage within budgeted limits

## Cross-Sleeve Constraints (v1)

- Per-name cap: 8-10% at portfolio layer (configured in portfolio spec)
- Sector cap: 30% at portfolio layer
- Net long cap: 95% with cash reserve >= 5%
- No leverage in v1

## Explicit Deferrals

Future phase only
- Options overlays (covered calls, cash-secured puts, protective hedges)
- Event/news sleeves
- ML-optimized sleeve weighting
