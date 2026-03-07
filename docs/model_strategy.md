# Model & Strategy Documentation

**System:** Caerus Quant — Daily Execution & Research System
**Author:** Brett Olson
**Last reviewed:** March 2026

---

## Table of Contents

1. [Strategy Intent](#strategy-intent)
2. [Sleeve Architecture](#sleeve-architecture)
3. [Active Sleeve: Sleeve Trend](#active-sleeve-sleeve-trend)
4. [Research Sleeve: Sleeve 1](#research-sleeve-sleeve-1)
5. [Alpha Hypotheses](#alpha-hypotheses)
6. [Selection Framework](#selection-framework)
7. [Ranking and Scoring Logic](#ranking-and-scoring-logic)
8. [Portfolio Construction](#portfolio-construction)
9. [VIX Regime Overlay](#vix-regime-overlay)
10. [Rebalance and Turnover](#rebalance-and-turnover)
11. [Risk Overlays and Guardrails](#risk-overlays-and-guardrails)
12. [Execution Assumptions](#execution-assumptions)
13. [What This Strategy Is NOT](#what-this-strategy-is-not)
14. [Archived / Inactive Sleeves](#archived--inactive-sleeves)
15. [Current Uncertainties and Open Questions](#current-uncertainties-and-open-questions)

---

## Strategy Intent

The system is designed to capture **intermediate-term equity price trends** in US large and mid-cap stocks using a systematic, rules-based process. It does not predict earnings, fundamental value, or macro regimes. The edge hypothesis is that price trends persist over days to weeks, and that a disciplined mechanical process — applied consistently with strict risk controls — outperforms passive benchmarks on a risk-adjusted basis.

The system operates in **paper trading mode** on Alpaca and is not currently managing live money.

---

## Sleeve Architecture

The portfolio is composed of independent **sleeves**, each with its own signal generation, selection logic, and weight in the overall allocator.

**Current production state:**

| Sleeve | Status | Allocator Weight | Description |
|---|---|---|---|
| `sleeve_trend` | **Active — drives all live orders** | 100% | EMA crossover + ADX filter + VIX regime scaling |
| `sleeve_1` | Research-only — output discarded | 0% | Cross-sectional momentum + multi-factor scoring |
| `sleeve_2` | Disabled (stub only) | 0% | Not implemented |
| `charlie_munger` | Disabled (stub only) | 0% | Not implemented |

The `DEFAULT_SLEEVE_WEIGHTS` in `core/portfolio_alloc.py` reflects this:
```python
DEFAULT_SLEEVE_WEIGHTS = {"sleeve_trend": 1.00}
```

`STASH_SLEEVE_NAME` is `"CASH"` — any undeployed capital routes there, not to a secondary sleeve.

---

## Active Sleeve: Sleeve Trend

### Files
- `sleeves/sleeve_trend/selection.py` — Entry point: `select_and_weight()`
- `sleeves/sleeve_trend/indicators.py` — Pure indicator functions
- `sleeves/sleeve_trend/backtest.py` — Data enrichment pipeline: `prepare_data()`
- `sleeves/sleeve_trend/build_sleeve_output.py` — Bridge to `SleeveOutput`
- `sleeves/sleeve_trend/config.py` — All tunable parameters

### Data Pipeline

On each run, `prepare_data()` downloads approximately 1 year of daily OHLCV history for all tickers in the configured universe (via Yahoo Finance). It enriches this data with:

- EMA(20), EMA(50), EMA(200)
- ATR(14)
- ADX(14) with +DI and -DI components
- 20-day realized volatility (annualized)
- 20-day and 60-day momentum (cumulative log return)
- 20-day average volume
- Volume ratio (current vs 20-day average)
- Liquidity pass/fail flag (price ≥ $5 and volume ≥ 100K shares)
- Sector mapping

### Entry Gates (Hard Filters)

All conditions must pass for a ticker to be considered:

1. **Trend filter:** Close price > EMA(200) — ensures only uptrending names enter
2. **Crossover filter:** EMA(20) > EMA(50) — short-term above medium-term (momentum confirmation)
3. **ADX filter:** ADX ≥ 20 — market is trending, not ranging (configurable via `ADX_THRESHOLD`)
4. **Liquidity gate:** Price ≥ $5 and 20-day average volume ≥ 100K shares

Tickers failing any gate are excluded entirely from selection.

---

## Research Sleeve: Sleeve 1

### Files
- `sleeves/sleeve_1/backtest.py` — Signal generation and backtest harness
- `sleeves/sleeve_1/config.py` — Configuration (extracted from hardcoded values, Tier 1 enhancement)

### What It Does

Sleeve 1 is a cross-sectional momentum and multi-factor scorer. It computes a composite score for each ticker based on 20-day momentum (50% weight), 60-day momentum (35% weight), and 20-day relative volume (15% weight). It applies entry thresholds, hold-period limits, and ATR-based trailing stops.

**Current production status:** Sleeve 1 runs daily as part of `daily_quant_report.py` (`run_sleeve_1()` is called), but its output is explicitly discarded:
```python
_, _ = run_sleeve_1()   # research only — output not passed to allocator
```

### Why It Is Not Live

The architectural decision to run only Sleeve Trend in production is documented but the formal status of Sleeve 1 — whether to promote it to the allocator or formally archive it — remains an open question. See [Current Uncertainties](#current-uncertainties-and-open-questions).

---

## Alpha Hypotheses

### Hypothesis 1 — Trend Persistence

Equity prices that are trending (confirmed by multi-timeframe EMA alignment and ADX conviction) continue to trend over intermediate horizons (days to weeks). The EMA(20) > EMA(50) > price > EMA(200) alignment captures stocks with genuine price momentum across three timeframes, reducing false signals from short-term noise.

**Academic basis:** Time-series momentum has been documented across asset classes (Moskowitz, Ooi, Pedersen, 2012). EMA-based systems are a simplified but practical implementation.

### Hypothesis 2 — Cross-Sectional Ranking Adds Precision

Among stocks passing the hard entry gates, ranking by a composite score (trend strength, momentum, ADX, volume, inverse-vol) selects for names with the strongest current evidence of persistent trend. This reduces the position count to the highest-conviction ideas, which improves the signal-to-noise ratio at the portfolio level.

### Hypothesis 3 — Inverse-Volatility Weighting Improves Risk-Adjusted Returns

Sizing positions inversely to their realized 20-day volatility — rather than equally or by conviction — naturally allocates more capital to lower-risk names. This creates a risk-parity tilt that has historically improved Sharpe ratio by reducing drawdown on individual position blowups.

**Mechanism:** `realized_vol_i` is clipped to [5%, 80%] to prevent extreme weights on near-zero-vol names. Weight = `(1 / vol_i) / sum(1 / vol_j for j in selected)`.

### Hypothesis 4 — VIX Regime Scaling Reduces Tail Risk

In high-volatility environments (VIX > 20), reducing position count and gross exposure limits correlation-driven drawdowns. The regime is classified in real time using the CBOE VIX index, allowing the strategy to mechanically de-risk without relying on subjective judgment.

### Hypothesis 5 — Strict Risk Controls Enable Compounding

Circuit breakers (drawdown-triggered) and hard position caps prevent catastrophic single-name or portfolio-level losses. The primary alpha edge is durability: surviving tail events while competitors blow up.

---

## Selection Framework

**Entry point:** `sleeves/sleeve_trend/selection.py::select_and_weight(signals, asof_date, top_n, weight_method)`

1. Filter to the latest available date (or `asof_date` if provided)
2. Apply hard entry gates (see above)
3. Score surviving tickers using a five-factor cross-sectional rank model
4. Apply sector cap (max 2 positions per sector)
5. Select top-N by composite score (default `TOP_LONGS = 3`)
6. Weight by `inverse_vol` (default), `score`, or `equal`
7. Return a `pd.DataFrame` with `ticker`, `target_weight`, `score`, `rank`, `sleeve` columns

This output is passed to `build_sleeve_output.py`, which wraps it in a `SleeveOutput` for the allocator.

---

## Ranking and Scoring Logic

Scores are computed as percentile ranks (0–100) within the passing universe and then combined:

| Factor | Weight | Metric |
|---|---|---|
| Trend Strength | 30% | Distance above EMA(200), normalized cross-sectionally |
| Momentum | 25% | Blend: 60% × 20d return + 40% × 60d return |
| ADX | 25% | Directional index level (trend conviction) |
| Relative Volume | 10% | Current volume / 20-day average volume |
| Inverse Volatility | 10% | Lower realized vol = higher score |

**Composite score** = weighted sum of percentile ranks (each factor ranked from 0 to 100 within the passing cross-section).

Higher composite score = higher rank = selected first when top-N cap is applied.

---

## Portfolio Construction

**Allocator:** `core/portfolio_alloc.py::PortfolioAllocator`

The allocator takes `SleeveOutput` from each sleeve and produces a final `{ticker: target_weight}` mapping. With Sleeve Trend at 100% weight, this is essentially a passthrough of the sleeve's weights with policy overlays applied:

- **Position cap:** Max 10% per position (`MAX_POSITION_PCT`), min 2% (`MIN_POSITION_PCT`)
- **Sector cap:** Max 2 positions in the same sector (enforced in selection, reinforced in allocator)
- **Gross exposure:** Max 50% gross (`MAX_GROSS_EXPOSURE`)
- **Cash routing:** Undeployed capital (1 − gross_exposure) goes to CASH

**VIX scaling interaction:** The `position_scale` from the current VIX regime is passed as `min_gross_exposure` to the allocator. This caps how much equity is deployed — the remainder automatically routes to CASH via `_add_cash_allocation`.

---

## VIX Regime Overlay

**Module:** `research/vix_regime.py`
**Config:** `sleeves/sleeve_trend/config.py` (VIX_* constants)

The system fetches the current CBOE VIX level via yfinance at the start of each daily run and classifies into one of four regimes:

| Regime | VIX Range | Position Scale | Max Positions |
|---|---|---|---|
| LOW | VIX < 20 | 100% | 10 |
| ELEVATED | 20 ≤ VIX < 30 | 75% | 7 |
| HIGH | 30 ≤ VIX < 40 | 50% | 4 |
| CRISIS | VIX ≥ 40 | 25% | 2 |

Two mechanisms operate simultaneously:
1. **`top_n` cap** — `build_sleeve_output.py` limits the number of positions to `regime["max_positions"]` before selection runs.
2. **Gross exposure cap** — `position_scale` is passed to the allocator, capping total deployment.

**Failure handling:** If yfinance is unavailable, fallback VIX = 25.0 (ELEVATED, 75% scale). If the entire VIX call fails, full deployment is used with a warning logged.

**Persistence:** `outputs/vix_regime/regime_current.json` and `regime_history.csv`

---

## Rebalance and Turnover

The system is designed for **daily rebalancing** — on each run, it computes the target portfolio from scratch and sends orders to bring Alpaca positions in line with the new targets.

**Turnover cap:** A 75% daily turnover cap is enforced in the paper execution layer (`paper/paper_broker.py`). Runs that would require more than 75% turnover are flagged and truncated.

**Signal hash:** Each daily signal set is hashed (`paper/ledger.py::compute_signal_hash`). The ledger stores this hash per trade row for auditability.

**Cost assumption in alpha_report runs:** 25 bps round-trip (configurable via `--cost-bps`).

---

## Risk Overlays and Guardrails

### Drawdown Circuit Breaker

**File:** `engine/breaker.py`

Three operating modes, controlled by `BREAKER_MODE` environment variable:

| Mode | `exposure_multiplier` | Behavior |
|---|---|---|
| `off` | 1.0 | No scaling — full deployment regardless of drawdown |
| `partial` (default) | `BREAKER_PARTIAL_EXPOSURE` (default 0.5) | Scale all positions by 50% |
| `lock` | 0.0 | Zero new entries — all positions remain, no new buys |

The breaker config also exposes `BREAKER_POLICY` (FULL/PARTIAL/LOCK) and `BREAKER_STATE_CAN_OVERRIDE` for state-driven overrides.

Configured soft/hard drawdown thresholds in `sleeve_trend/config.py`:
- `MAX_DRAWDOWN_SOFT = 0.10` (10% DD → reduce size)
- `MAX_DRAWDOWN_HARD = 0.15` (15% DD → stop new entries)
- `DRAWDOWN_RECOVERY_PCT = 0.05` (resume at 5% DD)

**Note:** The breaker thresholds are defined in config but the mechanism that triggers mode transitions based on live drawdown is implemented in `engine/breaker.py` and called from `daily_quant_report.py` via `apply_portfolio_exposure_overlay()`.

### Information Coefficient Monitor

**File:** `research/ic_monitor.py`

Measures the Pearson correlation between the current composite signal score and next-day forward return (Information Coefficient). Runs as a non-blocking hook after the daily report. Alerts trigger when:
- IC < 0 for 20+ consecutive days
- Rolling 60-day mean IC < 0.03

**Outputs:** `outputs/ic_monitor/ic_daily.csv`, `ic_rolling_60d.csv`, `ic_summary.json`

### Pre-Trade Reconciliation

Before any orders are sent to Alpaca, `reconciliation.py` compares the canonical model snapshot against actual broker positions. On mismatch, all orders are blocked. This prevents "phantom position" drift from causing overcounting or double-execution.

---

## Execution Assumptions

- **Market data:** Yahoo Finance (yfinance) — free, ~15-min delayed for intraday; end-of-day data used for signals.
- **Order type:** Market orders (no limit orders currently in use).
- **Fill assumption in backtests:** Next-open fill with configurable slippage (default 0.1%, `SLIPPAGE_PCT`).
- **Commissions in backtests:** $0.50 fixed per trade (`TRADE_COST`); 25 bps in alpha_report runs.
- **Execution platform:** Alpaca paper trading API.
- **Rebalance frequency:** Daily (each workflow run).
- **Universe:** Defined in `data/universe.csv`. No survivorship bias correction applied.

---

## What This Strategy Is NOT

- **NOT a fundamental strategy** — No earnings estimates, balance sheets, or valuations are considered.
- **NOT a mean-reversion strategy** — The strategy bets on trend continuation, not price reversal.
- **NOT a macro strategy** — VIX regime is used for risk scaling only, not directional macro bets.
- **NOT a high-frequency strategy** — Daily rebalance, next-open fills.
- **NOT a long-short strategy** — Sleeve Trend is long-only in production. Short parameters exist in config but no short orders are placed.
- **NOT live-money** — Paper trading only; no real capital is deployed.
- **NOT survivorship-bias corrected** — The universe is a static CSV; historical data may include names that were alive at research time.

---

## Archived / Inactive Sleeves

The following sleeves exist in the codebase but are not part of the production execution path. Per `ARCHIVING.md`, no production module imports from `archive/`.

| Sleeve | Location | Description |
|---|---|---|
| `charlie_munger` | `sleeves/charlie_munger/` | Quality/value-oriented sleeve; disabled, stubs remain in orchestrator |
| `sleeve_2` | `sleeves/sleeve_2/` | Not implemented beyond stubs |
| Legacy archived sleeves | `archive/sleeves/` | Earlier strategy iterations moved to archive |

---

## Current Uncertainties and Open Questions

| Item | Status |
|---|---|
| **Sleeve 1 fate** | Open. Runs daily but output discarded. Decision pending: integrate into allocator (what weight?) or formally archive. |
| **Walk-forward validation** | Extended WFO (2015–present, ~36 windows) in progress. Initial 4-window result (2023–2024) shows elevated OOS Sharpe — likely bull-market artifact. Full run needed before drawing conclusions. |
| **IC monitor baseline** | IC monitor is new (Tier 1 enhancement). No historical baseline established yet for alert thresholds (IC < 0.03 threshold is a heuristic). |
| **Sleeve 2 / Charlie Munger stubs** | Dead code in `daily_quant_report.py`. Should be removed in a cleanup pass. |
| **Data provider redundancy** | Single dependency on Yahoo Finance. No secondary provider for fallback. |
| **Short selling** | Short parameters defined in config (`TOP_SHORTS`, `SHORT_THRESHOLD`, etc.) but never exercised in production. No options execution path exists. |
| **SPY puts hedge** | Listed in roadmap (Tier 3) but requires options approval and new broker execution path. Not implemented. |
| **Live money transition criteria** | No formal criteria defined for when paper trading performance warrants live capital deployment. |
