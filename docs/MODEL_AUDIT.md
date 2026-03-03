# Model Audit Overview — Quant Daily Report

**Last Updated:** March 2026  
**Document Purpose:** Document the stock selection engines, signal generation techniques, model hypotheses, validation approach, and artifact locations for the Quant Daily Report portfolio.

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Repo Map](#repo-map)
3. [Stock Selection Engine Architecture](#stock-selection-engine-architecture)
4. [Signal Generation & Technical Indicators](#signal-generation--technical-indicators)
5. [Weighting & Portfolio Construction](#weighting--portfolio-construction)
6. [Alpha & Edge Hypotheses](#alpha--edge-hypotheses)
7. [Validation & Testing](#validation--testing)
8. [Artifact Locations](#artifact-locations)
9. [How to Run Backtests & Validations](#how-to-run-backtests--validations)
10. [Audit Checklist](#audit-checklist)

---

## Executive Summary

The portfolio uses **two independent sleeves**:

1. **Sleeve Trend (Trend Following):** Technical indicator-based trend follower using EMA crossovers + ADX filter
2. **Sleeve 1 (Momentum + Factors):** Cross-sectional momentum/factor scorer with multi-regime rules

Both sleeves feed into a **PortfolioAllocator** that applies policy overlays (exposure limits, drawdown breakers, sector caps).

The primary hypothesis is:
- **Alpha comes from:** Trend/momentum timing + cross-sectional ranking + inverse-volatility weighting + dynamic sizing
- **Edge durability:** Trend-following has been shown to persist across asset classes and regimes
- **Risk management:** ATR-based sizing, position caps, sector concentration limits, and circuit breakers

---

## Repo Map

### Selection & Signal Generation

| Component | File(s) | Purpose |
|-----------|---------|---------|
| **Sleeve Trend – Selection Engine** | [sleeves/sleeve_trend/selection.py](../sleeves/sleeve_trend/selection.py) | Main entry: `select_and_weight()` — scores universe, ranks by composite factors, weights by inverse volatility |
| **Sleeve Trend – Indicators** | [sleeves/sleeve_trend/indicators.py](../sleeves/sleeve_trend/indicators.py) | Pure functions: `ema()`, `atr()`, `adx()`, `crossover()`, `realized_volatility()` |
| **Sleeve Trend – Backtest/Signals** | [sleeves/sleeve_trend/backtest.py](../sleeves/sleeve_trend/backtest.py) | Signal prep: `prepare_data()` → enriched OHLCV with indicators; position logic with trailing stops |
| **Sleeve Trend – Config** | [sleeves/sleeve_trend/config.py](../sleeves/sleeve_trend/config.py) | All tunable params: EMA periods (20/50/200), ADX thresholds, position caps, vol targeting |
| **Sleeve Trend – Sleeve Output** | [sleeves/sleeve_trend/build_sleeve_output.py](../sleeves/sleeve_trend/build_sleeve_output.py) | Bridge: `build_trend_sleeve_output()` → converts signals into `SleeveOutput` for portfolio allocator |
| **Sleeve 1 – Backtest/Signals** | [sleeves/sleeve_1/backtest.py](../sleeves/sleeve_1/backtest.py) | Signal prep: `prepare_data()` → momentum + factor scores; multi-regime entry/exit rules |
| **Sleeve 1 – Config** | [sleeves/sleeve_1/config.py](../sleeves/sleeve_1/config.py) | *(if exists)* Position config, thresholds, hold periods |

### Portfolio Construction & Risk Overlay

| Component | File(s) | Purpose |
|-----------|---------|---------|
| **Portfolio Allocator** | [core/portfolio_alloc.py](../core/portfolio_alloc.py) | `PortfolioAllocator` class: combines sleeve outputs, applies allocation weights, enforces position/sector/exposure caps |
| **Exposure/Policy Breaker** | [engine/breaker.py](../engine/breaker.py) | `apply_portfolio_exposure_overlay()` — implements drawdown circuit breaker, gross/net exposure limits |
| **Quant Report Orchestrator** | [daily_quant_report.py](../daily_quant_report.py) | `run_sleeve_trend()`, `run_sleeve_1()` → main daily workflow; calls selection engines, aggregates outputs |

### Execution & Broker Integration

| Component | File(s) | Purpose |
|-----------|---------|---------|
| **Paper Broker** | [paper/paper_broker.py](../paper/paper_broker.py) | `run_paper_day()` → executes orders against paper ledger; position tracking, slippage sim |
| **Paper Runner** | [paper/run_paper.py](../paper/run_paper.py) | CLI interface: executes paper trading for a signal date |
| **Ledger & Nav** | [paper/ledger2.py](../paper/ledger2.py), [paper/nav2.py](../paper/nav2.py) | Trade ledger, Nav updates, signal hashing for reconciliation |

### Backtest & Analysis Harness

| Component | File(s) | Purpose |
|-----------|---------|---------|
| **Backtest Engine** | [engine/backtest_engine.py](../engine/backtest_engine.py) | `run_backtest()` — canonical weights-based backtester; computes equity curve, holdings, trades, stats |
| **Policy Backtest** | [audit/policy_backtest.py](../audit/policy_backtest.py) | `load_sleeve1_dataset()`, `build_monthly_topn_target_weights()`, `run_policy_backtest()` — Sleeve 1 audit harness with breaker overlay testing |
| **Backtest Research** | [backtests/sleeve1_robustness.py](../backtests/sleeve1_robustness.py) | Robustness testing: parameter sweeps, walk-forward, regime analysis |

### Workflow & CLI

| Component | File(s) | Purpose |
|-----------|---------|---------|
| **AIOPS CLI** | [aiops/cli.py](../aiops/cli.py) | Commands: `parse`, `verify`, `plan`, `dispatch`, `run`, `run-all` for spec-driven validation |
| **AIOPS Run** | [aiops/run.py](../aiops/run.py) | End-to-end orchestration: parse spec → plan → dispatch |
| **AIOPS Verify** | [aiops/verify.py](../aiops/verify.py) | Mode-gated execution: EXPLORE, BUILD, HARDEN validation |

---

## Stock Selection Engine Architecture

### Sleeve Trend — Technical Trend Follower

**Entry Point:** `sleeves/sleeve_trend/selection.py::select_and_weight()`

**Input:**
```python
signals: pd.DataFrame  # Output of prepare_data()
# Columns: date, ticker, close, open, high, low, volume, atr,
#          adx, ema_fast, ema_slow, ema_trend, above_trend,
#          volume_sma, daily_return, sector, passes_liquidity
```

**Process:**
1. **Filter to latest date** (`asof_date` defaults to max date in signals)
2. **Apply hard gates** (`_apply_gates()`):
   - Must be **above 200-day EMA** (trend filter)
   - EMA(fast=20) **> EMA(slow=50)** (short-term above medium-term)
   - **ADX ≥ 15.0** (market is trending, not ranging)
   - Price **≥ $5** (liquidity gate)
   - Volume **≥ 100K shares/day** (liquidity gate)
3. **Score cross-section** (`_score_cross_section()`):
   - **Factor 1 – Trend Strength** (30%): Distance above 200 EMA, normalized
   - **Factor 2 – Momentum** (25%): 20-day + 60-day returns (0.6/0.4 blend)
   - **Factor 3 – ADX** (25%): Trend conviction level
   - **Factor 4 – Volume** (10%): Relative volume (vs 20-day avg)
   - **Factor 5 – Volatility** (10%): Lower vol = higher score (stability reward)
   - **Composite:** Weighted sum of percentile ranks (0–100)
4. **Apply sector cap** (`_apply_sector_cap()`): Max 2 positions per sector
5. **Select top N** (default 3, configurable)
6. **Weight by method:**
   - **"inverse_vol"** (default): Positions sized inversely to realized volatility → lower-vol names get more capital
   - **"score"**: Positions sized by composite score
   - **"equal"**: Equal 1/N weight
7. **Output:**
```python
pd.DataFrame([
    {"ticker": "AAPL", "target_weight": 0.45, "score": 92.3, "rank": 1, "sleeve": "sleeve_trend", ...},
    {"ticker": "MSFT", "target_weight": 0.35, "score": 87.1, "rank": 2, "sleeve": "sleeve_trend", ...},
    {"ticker": "TSLA", "target_weight": 0.20, "score": 81.4, "rank": 3, "sleeve": "sleeve_trend", ...},
])
```

**Configuration:** [sleeves/sleeve_trend/config.py](../sleeves/sleeve_trend/config.py)
- `EMA_FAST = 20`, `EMA_SLOW = 50`, `EMA_TREND_FILTER = 200`
- `ADX_PERIOD = 14`, `ADX_THRESHOLD = 20`
- `TOP_LONGS = 3` (max simultaneous positions)
- `MAX_POSITIONS_PER_SECTOR = 2`
- `WEIGHT_METHOD = "inverse_vol"`

---

### Sleeve 1 — Cross-Sectional Momentum + Factors

**Entry Point:** `sleeves/sleeve_1/backtest.py::prepare_data()` → signal generation

**Input:** Price history from Yahoo Finance (default 1 year lookback)

**Process:**
1. **Fetch prices** (`download_prices()`)
2. **Compute factors** (`fetch_factor_data()`, `build_factor_scores()`):
   - **20-day returns** (`ret20`)
   - **60-day returns** (`ret60`)
   - **20-day volume** (`vol20`)
   - ATR-based sizing
3. **Score cross-section** (`compute_full_signals()`):
   - **Momentum Score** (50%): rank(`ret20`)
   - **Factor Score** (35%): rank(`ret60`)
   - **Volume Score** (15%): rank(`vol20`)
   - **Final Signal** = 0.50 × momentum + 0.35 × factor + 0.15 × volume
4. **Apply entry/exit thresholds:**
   - **Long Entry:** `final_signal > 75` (or regime-specific thresholds)
   - **Short Entry:** `final_signal < 20`
   - **Exit:** Signal drops below `LONG_FLOOR_EXIT = 65` or `SHORT_FLOOR_EXIT = 30`
5. **Position management:**
   - Hold for 3–5 days (max)
   - Max 3 concurrent longs, 1 concurrent short
   - Position size capped at 7% of equity
   - ATR-based trailing stops

**Configuration:** [sleeves/sleeve_1/backtest.py](../sleeves/sleeve_1/backtest.py) (hardcoded, marked "LOCKED")
- `LONG_THRESHOLD = 75`, `SHORT_THRESHOLD = 20`
- `MAX_HOLD_DAYS = 5`
- `TOP_LONGS = 3`, `MAX_SHORT_POSITIONS = 1`

---

## Signal Generation & Technical Indicators

### Technical Indicator Library

**File:** [sleeves/sleeve_trend/indicators.py](../sleeves/sleeve_trend/indicators.py)

Pure functions for indicator calculation:

| Function | Parameters | Output | Purpose |
|----------|-----------|--------|---------|
| `ema()` | series, period | EMA series | Exponential moving average (fast baseline) |
| `sma()` | series, period | SMA series | Simple moving average |
| `atr()` | high, low, close, period=14 | ATR series | Average true range (volatility filter) |
| `adx()` | high, low, close, period=14 | df w/ ADX, +DI, -DI | Directional index (trend conviction) |
| `crossover()` | fast, slow | bool series | True when fast crosses above slow |
| `crossunder()` | fast, slow | bool series | True when fast crosses below slow |
| `realized_volatility()` | returns, lookback=20, annualize=True | vol series | Rolling realized vol (for inverse-vol weighting) |

### Indicator Enrichment Pipeline

**File:** [sleeves/sleeve_trend/backtest.py](../sleeves/sleeve_trend/backtest.py)::prepare_data()

```python
# Pseudocode
prices = download_prices(TICKERS, period="1y", interval="1d")

# For each ticker, compute:
signals = {
    "date": ...,
    "ticker": ...,
    "open, high, low, close, volume": ...,
    
    # Moving averages
    "ema_fast": ema(close, 20),
    "ema_slow": ema(close, 50),
    "ema_trend": ema(close, 200),
    
    # Volatility & trend strength
    "atr": atr(high, low, close, 14),
    "adx": adx(...),
    "realized_vol": realized_volatility(returns, 20, annualize=True),
    
    # Momentum
    "mom_20d": returns.rolling(20).sum(),
    "mom_60d": returns.rolling(60).sum(),
    
    # Liquidity checks
    "volume_sma": volume.rolling(20).mean(),
    "volume_ratio": volume / volume_sma,
    "passes_liquidity": (volume >= 100K) & (price >= $5),
    
    # Sector
    "sector": <from ticker mapping>,
}
```

---

## Weighting & Portfolio Construction

### Inverse-Volatility Weighting

**File:** [sleeves/sleeve_trend/selection.py](../sleeves/sleeve_trend/selection.py)::_weight_by_inverse_vol()

```python
# For selected stock i:
realized_vol_i = realized_volatility(returns[i], lookback=20, annualize=True)
# Clip to [5%, 80%] to prevent blow-up on low-vol names
vol_i_clipped = clip(realized_vol_i, 0.05, 0.80)

# Weight inversely to vol (lower vol → higher weight)
inv_vol_weight_i = 1.0 / vol_i_clipped

# Normalize to sum to 1
target_weight_i = inv_vol_weight_i / sum(inv_vol_weights)
```

**Results:** Lower-volatility names automatically receive more capital, creating a natural risk-parity tilt.

### Portfolio Allocator

**File:** [core/portfolio_alloc.py](../core/portfolio_alloc.py)

`PortfolioAllocator` class:
- Takes `SleeveOutput` from each sleeve
- Applies **sleeve weights** (configured per-sleeve target allocation)
- Applies **position caps** (max 50% single position, min 2%)
- Applies **sector concentration limits** (max 2 per sector across portfolio)
- Applies **exposure limits** (max 50% gross exposure)

```python
rebalance = allocator.allocate(
    sleeve_outputs=[trend_out, sleeve1_out],
    sleeve_bounds={"sleeve_trend": (0.3, 0.5), "sleeve_1": (0.4, 0.6)},
    base_equity=10_000.0,
)
# Returns: AllocationResult with final [ticker → target_weight] mapping
```

### Policy Overlay & Risk Management

**File:** [engine/breaker.py](../engine/breaker.py)

`apply_portfolio_exposure_overlay()` applies:
1. **Drawdown Circuit Breaker:**
   - Soft mode (DD ≥ 10%): reduce position sizes by 50%
   - Hard mode (DD ≥ 15%): stop all new entries
   - Resume at 5% DD recovery
2. **Gross Exposure Cap:** Sum of |longs| + |shorts| ≤ 50%
3. **Net Exposure Limit:** (sum longs - sum shorts) range configurable

---

## Alpha & Edge Hypotheses

### Hypothesis 1: Trend Persistence

**Claim:** Trend-following (EMA-based) has persistent alpha because:
- Trends tend to persist intra-week and intra-month across equities
- Mean reversion is slower than trend continuation in typical equity universes
- Technical players create trend inertia

**Edge:**
- EMA(20) > EMA(50) > above EMA(200) = strong multiframe alignment
- ADX filter ensures we avoid choppy, ranging systems

**Historical Evidence:** Trend-following documented in academic literature (Moskowitz et al., "Time Series Momentum"); shown to work across asset classes.

---

### Hypothesis 2: Cross-Sectional Momentum

**Claim:** Stocks with high recent returns continue to outperform because:
- Information diffusion is gradual; short-term reversals are rare
- Momentum is driven by genuine fundamental changes or regimes
- Portfolio rebalancing creates natural momentum drag on winners

**Edge:**
- 20d + 60d momentum blend captures both short & medium term
- Equal-weighting or inverse-vol-weighting controls concentration risk

**Regime Flexibility:** Sleeve 1 rules vary by volatility regime (low/medium/high) to adapt to market conditions.

---

### Hypothesis 3: Volatility-Adjusted Sizing

**Claim:** Inverse-volatility weighting improves risk-adjusted returns:
- Lower-vol names have better Sharpe ratios → more capital → more alpha
- Avoids over-sizing to high-vol darlings (meme stocks)
- Creates natural risk parity across the portfolio

**Edge:** Lower realized vol = better execution + lower slippage + lower drawdowns

---

### Hypothesis 4: Risk Management Durability

**Claim:** Position caps, sector limits, and circuit breakers reduce tail risk:
- Max position (50%) prevents catastrophic single-name losses
- Sector cap (2 per sector) prevents correlation shocks
- Drawdown breaker (hard stop at 15% DD) prevents cascade losses

**Edge:** Risk management allows us to stay in the game during tails; competitors blow up and stop trading.

---

## Validation & Testing

### Backtest Harness

#### Weights-Based Backtester
**File:** [engine/backtest_engine.py](../engine/backtest_engine.py)

`run_backtest(target_weights, prices, ...)` → simulates a weights-based portfolio:
- Input: `target_weights` DataFrame (date × ticker, values = portfolio weight)
- Output: `{"equity_curve": ..., "holdings": ..., "trades": ..., "stats": {...}}`
- Handles:
  - Position drift between rebalances
  - Commission & slippage costs (configurable bps)
  - Rebalance schedules (daily, weekly, monthly)
  - Benchmark comparison & alpha calculation

```python
result = run_backtest(
    target_weights=tw,
    prices=px,
    initial_equity=10_000.0,
    commission_bps=1.0,
    slippage_bps=1.0,
    rebal_rule="D",  # daily rebalance
)
# Output: dict with equity_curve, holdings, trades, stats, alpha
```

#### Policy + Position Backtester
**File:** [audit/policy_backtest.py](../audit/policy_backtest.py)

`run_policy_backtest()` — tests Sleeve 1 signals with:
- `load_sleeve1_dataset()`: load historical signals & prices
- `build_monthly_topn_target_weights()`: convert top-N signals into target weights
- `apply_policy_overlay()`: apply breaker + exposure limits
- `run_backtest()`: compute P&L

```python
dataset = load_sleeve1_dataset(start="2023-01-01", end="2025-12-31")
target_weights = build_monthly_topn_target_weights(
    dataset.prices_wide, dataset.ranking, top_n=5
)
result = run_policy_backtest(target_weights, dataset.prices_wide, policy="FULL")
```

---

### Robustness Testing

**File:** [backtests/sleeve1_robustness.py](../backtests/sleeve1_robustness.py)

Parameter sweeps:
- Walk-forward analysis (expanding/rolling windows)
- Top-N variations (1, 3, 5, 10)
- Policy modes (FULL, PARTIAL, LOCK)
- Breaker thresholds (soft/hard DD)
- Commission impact (0 bps, 1 bps, 2 bps)

Each test produces:
- Cagr, Volatility, Sharpe, Sortino
- Max Drawdown, Recovery Time
- Turnover, Win Rate
- vs Benchmark (SP500)

---

### Paper Trading Validation

**File:** [paper/paper_broker.py](../paper/paper_broker.py), [paper/run_paper.py](../paper/run_paper.py)

Live validation:
- Daily signal generation from real market data
- Paper order execution
- Ledger tracking
- Nav updates
- Execution email with analysis

---

### CLI Verification Modes

**File:** [aiops/verify.py](../aiops/verify.py)

Three-stage verification:
1. **EXPLORE**: Run backtests on sample data; check for syntax/logic errors
2. **BUILD**: Full backtest suite with robustness tests
3. **HARDEN**: Extended period with extreme scenarios, stress tests

```bash
# Run verification
python -m aiops verify specs/_template.md --mode EXPLORE
python -m aiops verify specs/_template.md --mode BUILD
python -m aiops verify specs/_template.md --mode HARDEN
```

---

## Artifact Locations

### Daily Run Outputs

| Directory | Contents | Refresh Rate |
|-----------|----------|--------------|
| **outputs/daily/** | Daily reports (HTML, JSON) | Daily after market close |
| **outputs/latest.json** | Latest portfolio snapshot | Daily |
| **outputs/alpha_report/** | Attribution analysis (daily returns vs benchmark) | Daily |
| **outputs/perf/** | Performance curves, metrics | Daily |

### Paper Trading State

| Directory | Contents | Refresh Rate |
|-----------|----------|--------------|
| **outputs/paper_state/** | Paper ledger (accumulated trades) | Per-trade |
| **outputs/paper_state/ledger2.csv** | Canonical trade log with fills | Per-trade |
| **outputs/paper_state/nav2.csv** | Net asset value time series | Daily |

### Backtest & Runs

| Directory | Contents | Refresh Rate |
|-----------|----------|--------------|
| **outputs/runs/** | Individual backtest runs (equity curves, holdings, stats) | On-demand |
| **outputs/runs/run_<TIMESTAMP>/** | Single run directory (result.json, trades.csv, etc.) | On backtest execution |
| **reports/ai_runs/** | AIOPS spec-driven runs (with plan, spec snapshot, verification logs) | On aiops run |
| **reports/ai_runs/<RUN_ID>/plan.md** | Deterministic plan from spec | On plan command |
| **reports/ai_runs/<RUN_ID>/spec_snapshot.md** | Snapshot of spec at run time | On plan command |
| **reports/ai_runs/<RUN_ID>/verify.log** | Verification results (EXPLORE/BUILD/HARDEN) | On verify command |

### Ledger & Reconciliation

| File | Contents | Refresh Rate |
|------|----------|--------------|
| **paper/ledger2.csv** | Trade ledger (date, ticker, shares, price, PnL) | Per-trade |
| **canonical-model-snapshot/canonical_positions.json** | Canonical position state from broker | On reconciliation |

---

## How to Run Backtests & Validations

### 1. Run Daily Report (Full Pipeline)

```bash
# Activate environment
source .venv/bin/activate

# Run daily report for today (generates signals + paper trading)
python daily_quant_report.py

# Or with optional fields
python daily_quant_report.py \
    --report-date 2026-03-03 \
    --no-email \
    --write-signals-json
```

**Output:**
- Signal JSON at `signals/2026-03-03.json`
- HTML report at `outputs/daily/report_<timestamp>.html`
- Paper state updated in `outputs/paper_state/`

---

### 2. Run Sleeve-Specific Backtests

#### Sleeve Trend (in-sample backtest)

```bash
# Interactive backtest with plot
cd sleeves/sleeve_trend
python backtest.py --start 2023-01-01 --end 2025-12-31

# Output: equity_trend.json, holdings_trend.csv, trades_trend.csv
```

#### Sleeve 1 (policy backtest)

```bash
# Run policy backtest with environment config
export ALLOW_EMPTY_SLEEVES=1
export BREAKER_MODE=partial

python -c "
from audit.policy_backtest import run_policy_backtest
from audit.policy_backtest import load_sleeve1_dataset

dataset = load_sleeve1_dataset(start='2023-01-01', end='2025-12-31')
result = run_policy_backtest(
    dataset.prices_wide,
    dataset.ranking,
    top_n=5,
    policy='FULL'
)
print(result['stats'])
"
```

---

### 3. Run Robustness Tests

```bash
# Parameter sweep (varies top_n, policy, breaker settings)
python backtests/sleeve1_robustness.py \
    --start 2023-01-01 \
    --end 2025-12-31 \
    --output outputs/robustness_results.csv
```

**Output:** CSV with rows for each (top_n, policy, scenario) → (cagr, vol, sharpe, max_dd, ...)

---

### 4. Run AIOPS Verification

```bash
# EXPLORE mode: quick syntax check
python -m aiops verify specs/_template.md --mode EXPLORE

# BUILD mode: full backtest suite
python -m aiops verify specs/_template.md --mode BUILD

# HARDEN mode: extended/stress testing
python -m aiops verify specs/_template.md --mode HARDEN
```

**Output:** Test results in `reports/ai_runs/<RUN_ID>/verify.log`

---

### 5. Run Paper Trading

```bash
# Execute paper trading for a given signal date (next business day)
python paper/run_paper.py 2026-03-03 \
    --plan-only  \
    # Generate plan only, do not execute

python paper/run_paper.py 2026-03-03 \
    --force      \
    # Force re-run even if already traded (dev only)
```

**Output:**
- Orders planned in `run_dir/plan.json`
- Trades executed → recorded in `outputs/paper_state/ledger2.csv`
- Nav updated in `outputs/paper_state/nav2.csv`

---

### 6. End-to-End AIOPS Run

```bash
# Full spec-driven pipeline: parse → plan → dispatch → verify
python -m aiops run-all \
    --spec specs/_template.md \
    --mode BUILD

# Output: Full run artifacts in reports/ai_runs/<RUN_ID>/
```

---

## Audit Checklist

- [ ] **Selection Engine Paths**
  - [ ] Verify [sleeves/sleeve_trend/selection.py](../sleeves/sleeve_trend/selection.py) exists and `select_and_weight()` is callable
  - [ ] Verify [sleeves/sleeve_1/backtest.py](../sleeves/sleeve_1/backtest.py) exists and `prepare_data()` is callable
  - [ ] Verify [sleeves/sleeve_trend/backtest.py](../sleeves/sleeve_trend/backtest.py) has `prepare_data()` for trend signals

- [ ] **Signal Generation**
  - [ ] Verify [sleeves/sleeve_trend/indicators.py](../sleeves/sleeve_trend/indicators.py) has EMA, ADX, ATR, momentum functions
  - [ ] Verify [sleeves/sleeve_trend/config.py](../sleeves/sleeve_trend/config.py) tunes all parameters (EMA periods, ADX threshold, position caps)
  - [ ] Verify signals include: date, ticker, close, volume, ema_fast, ema_slow, ema_trend, adx, atr, sector, passes_liquidity

- [ ] **Weighting & Portfolio Construction**
  - [ ] Verify [sleeves/sleeve_trend/selection.py](../sleeves/sleeve_trend/selection.py) implements inverse-vol weighting (lower vol → higher weight)
  - [ ] Verify [core/portfolio_alloc.py](../core/portfolio_alloc.py) applies position caps (max 50%), sector limits (max 2), exposure limits
  - [ ] Verify [engine/breaker.py](../engine/breaker.py) applies drawdown circuit breaker (soft at 10% DD, hard at 15% DD)

- [ ] **Hypotheses Documented**
  - [ ] Trend persistence (EMA alignment, ADX filter)
  - [ ] Cross-sectional momentum (20d + 60d blend)
  - [ ] Volatility-adjusted sizing (inverse vol)
  - [ ] Risk management durability (circuit breakers, position caps)

- [ ] **Backtest Harness**
  - [ ] Verify [engine/backtest_engine.py](../engine/backtest_engine.py) exists and `run_backtest()` is callable
  - [ ] Verify input: `target_weights` (date × ticker), `prices` (date × ticker)
  - [ ] Verify output: equity_curve, holdings, trades, stats, alpha
  - [ ] Verify handles commission (bps), slippage (bps), rebalance schedules

- [ ] **Policy Backtest**
  - [ ] Verify [audit/policy_backtest.py](../audit/policy_backtest.py) has `load_sleeve1_dataset()`, `build_monthly_topn_target_weights()`, `run_policy_backtest()`
  - [ ] Verify includes breaker overlay + exposure limits
  - [ ] Verify computes stats: cagr, vol, sharpe, sortino, max_dd, turnover, win_rate

- [ ] **Robustness Testing**
  - [ ] Verify [backtests/sleeve1_robustness.py](../backtests/sleeve1_robustness.py) runs parameter sweeps (top_n, policy, breaker)
  - [ ] Verify walk-forward analysis is included
  - [ ] Verify commission impact tested (0, 1, 2 bps)

- [ ] **Paper Trading Validation**
  - [ ] Verify [paper/paper_broker.py](../paper/paper_broker.py) has `run_paper_day()` function
  - [ ] Verify daily signal generation from real market data
  - [ ] Verify order execution and ledger tracking
  - [ ] Verify nav updates and execution emails

- [ ] **Artifact Directories**
  - [ ] Verify [outputs/daily/](../outputs/daily/) contains daily reports
  - [ ] Verify [outputs/paper_state/](../outputs/paper_state/) contains ledger, nav, positions
  - [ ] Verify [outputs/runs/](../outputs/runs/) contains backtest results
  - [ ] Verify [reports/ai_runs/](../reports/ai_runs/) contains AIOPS run artifacts

- [ ] **Workflow & CLI**
  - [ ] Verify [daily_quant_report.py](../daily_quant_report.py) has `main()` entry point
  - [ ] Verify [aiops/cli.py](../aiops/cli.py) has commands: parse, verify, plan, dispatch, run, run-all
  - [ ] Verify AIOPS modes (EXPLORE, BUILD, HARDEN) are implemented in [aiops/verify.py](../aiops/verify.py)

- [ ] **Documentation & Comments**
  - [ ] Verify signal generation docstrings explain factor weights and cross-sectional logic
  - [ ] Verify weighting methods documented (inverse_vol, score, equal)
  - [ ] Verify risk management thresholds documented (ADX, vol floors, position caps)

---

## Known Limitations & TODOs

### Missing Components (as of 2026-03-03)

- [ ] **Short-selling logic in Sleeve Trend:** Currently trend-following is long-only; short logic exists in Sleeve 1 but not Sleeve Trend
- [ ] **Multi-regime adaptive parameters:** Config is static; potential to adapt EMA periods, ADX threshold by volatility regime
- [ ] **Walk-forward analysis:**  `backtests/sleeve1_robustness.py` exists but not fully automated in CI/CD
- [ ] **Live data pipeline:** Daily prices fetched via Yahoo Finance; no premium data (real-time, corporate actions, survivorship bias handling)
- [ ] **Hedge ratio optimization:** All sleeves equally weighted; could optimize sleeve allocation weights by recent Sharpe
- [ ] **Transaction cost modeling:** Commission + slippage fixed at ~2 bps; actual costs may vary by order size & liquidity

### Recommendations

1. **Automate robustness tests:** Run parameter sweeps nightly; alert on degradation
2. **Formalize regime detection:** Implement market regime classifier (VIX thresholds, rolling vol) to trigger adaptive parameters
3. **Build short sleeve:** Add systematic short selection (value-based, contrarian) to balance portfolio
4. **Premium data:** Integrate alternative data (sentiment, options flow) for alpha enhancement
5. **Walk-forward validation:** Extend [backtests/sleeve1_robustness.py](../backtests/sleeve1_robustness.py) with monthly walk-forward; check for parameter overfitting

---

**Document Approval:** 
- [ ] Selection Engine – Verified
- [ ] Signal Generation – Verified
- [ ] Weighting & Risk Management – Verified
- [ ] Backtest Harness – Verified
- [ ] Artifact Locations – Verified
- [ ] Validation Procedures – Ready

---

*Last Updated: 2026-03-03*
