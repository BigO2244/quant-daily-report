# Alpha Stack v1 — Implementation Status

**Date:** 2024-06-01
**Status:** Research / Shadow-Mode Only
**Author:** Claude (automated build)

---

## Overview

Alpha Stack v1 has been implemented as a fully isolated, flag-gated research namespace within the existing repository. It runs exclusively in shadow mode alongside production and does **not** interact with the live execution path in any way.

All feature flags default to `false`. Nothing activates automatically.

---

## What Was Built

### 1. Configuration (`alpha_stack/config/alpha_stack.yaml`)

Full YAML config covering:
- Feature flags (all default `false`)
- Regime thresholds (VIX levels, SPY trend thresholds, breadth percentiles)
- Sleeve parameters (eligibility, scoring weights, position caps)
- Allocator base weights per trend state + vol/breadth/macro modifiers
- Portfolio constraints (gross exposure, position caps, sector caps, drawdown levels)
- Cost model (slippage, commission)
- Research/backtest settings

### 2. DataStore Layer (`alpha_stack/datastore/`)

| Module | Status | Notes |
|--------|--------|-------|
| `base.py` | ✅ Complete | Abstract `DataStoreBase`; `DataStorePITWarning` |
| `fundamentals.py` | ✅ Complete | SEC EDGAR XBRL via EdgarClient; PIT-safe (filing-date semantics); 8 raw fields + 3 computed (EY, FCFY, B/P) |
| `prices.py` | ✅ Complete | yfinance + Parquet cache; strict PIT filtering |

**PIT Safety Note:** `FundamentalsDataStore` is now **PIT-safe** via SEC EDGAR XBRL API. All lookups filter on filing date (not period-end date), eliminating look-ahead bias. Filing lags (40-90 days) are built into the system.

### 3. Features Layer (`alpha_stack/features/`)

| Module | Status | Notes |
|--------|--------|-------|
| `trend.py` | ✅ Complete | r12_1, r6_1, r3_1 (21-day skip); EMA50/200 ratios; trend_flag; ATR20_pct; cross-sectional z-scores |
| `volatility.py` | ✅ Complete | realized_vol_20d, realized_vol_60d, ATR-14, atr_pct_14, vol_regime_label |
| `breadth.py` | ✅ Complete | Thin wrapper for regime inputs from BreadthDataStore |
| `value.py` | ✅ Complete | Computes earnings_yield (ni_TTM/mkt_cap), fcf_yield ((ocf−capex)_TTM/mkt_cap), book_to_price (equity/mkt_cap); sector-relative z-scores |
| `quality.py` | ⚠️ **STUB** | Returns empty DataFrame; awaiting Q-fundamentals (deferred to v1.1) |

### 4. Regime Engine (`alpha_stack/regime/`)

| Module | Status | Notes |
|--------|--------|-------|
| `state_machine.py` | ✅ Complete | All 4 dimensions: Trend (5 states), Vol (4), Breadth (4), Macro (3) |
| `hysteresis.py` | ✅ Complete | 5-day dwell; 2-close confirmation; max 1 state jump; crisis bypass |
| `context.py` | ✅ Complete | `RegimeContext` dataclass; `RegimeEngine` orchestrator; `classify_history()` |

**Key design decisions:**
- Hysteresis prevents regime whipsaw (most costly production failure mode for a regime-following strategy)
- Crisis vol bypass allows immediate regime recognition for tail risk
- `transition_summary()` available for attribution / diagnostics

### 5. Sleeves (`alpha_stack/sleeves/`)

| Module | Status | Active by Default | Notes |
|--------|--------|-------------------|-------|
| `trend.py` | ✅ Complete | No (flag-gated) | Score = 0.45·z(r12_1) + 0.30·z(r6_1) + 0.15·z(r3_1) + 0.10·trend_flag; volatility-adjusted; inverse-vol sizing |
| `mean_reversion.py` | ✅ Complete | No | Additional regime gate: trend ∈ {weak_up, neutral} AND vol ∈ {calm, normal} |
| `value.py` | ✅ Complete | Yes (flag: `ENABLE_VALUE_SLEEVE`) | Weights: 0.40·earnings_yield + 0.30·fcf_yield + 0.30·book_to_price; entry ≥75, hold ≥60, exit <50; top-15 candidates |
| `quality.py` | ⚠️ Stub | No | Deferred to v1.1; requires quality fundamentals (ROE, ROIC, etc.) |
| `base.py` | ✅ Complete | N/A | `SleeveBase` abstract contract; `SleeveOutput`; `HoldState`; `_iterative_cap_weights()` |
| `registry.py` | ✅ Complete | N/A | Lazy imports; `active_sleeves()` checks per-sleeve flags |

**Sleeve pipeline (each sleeve):**
1. `eligibility_filter()` — price, volume, data quality gates
2. `score_universe()` — compute composite score [0, 100]
3. `select_candidates()` — entry/hold/exit decisions via HoldState
4. `target_weights()` — inverse-vol sizing with iterative cap
5. Returns `SleeveOutput` with `provisional_weight` summing to ≤ risk_budget

### 6. Portfolio Allocator (`alpha_stack/portfolio/`)

| Module | Status | Notes |
|--------|--------|-------|
| `allocator.py` | ✅ Complete | `AlphaStackAllocator` v1; regime-aware; rules-based (no optimization) |
| `constraints.py` | ✅ Complete | `PortfolioConstraints`; position/sector caps; turnover smoothing (5%/day per sleeve) |
| `sizing.py` | ✅ Complete | `inverse_vol_weights()`, `equal_weights()`, `score_proportional_weights()` |

**Allocator pipeline:**
1. Base budgets from trend_state
2. Volatility modifiers (elevated: cut MR 50%; crisis: zero MR + cut trend 30%)
3. Breadth modifiers (healthy: +5% trend; deteriorating: -5% trend, +5% quality)
4. Macro modifiers (supportive: +5% value; restrictive: +5% cash, -5% value)
5. Normalise to `max_gross_exposure`
6. Drawdown circuit breaker (soft: halve all sleeves; hard: full cash)
7. Turnover smoothing (skip on first run; crisis bypasses smoothing)
8. Scale each sleeve's weights by its budget
9. Apply per-name (5%) and per-sector (25%) position caps

### 7. Research Harness (`alpha_stack/research/`)

| Module | Status | Notes |
|--------|--------|-------|
| `metrics.py` | ✅ Complete | Sharpe, Sortino, MaxDD, Calmar, CAGR, IC (Spearman/Pearson), turnover, `summarise_performance()` |
| `attribution.py` | ✅ Complete | `AttributionEngine`: sleeve returns, turnover, costs (25bps slippage + 1bps commission), IC series, regime attribution |
| `backtest.py` | ✅ Complete | `AlphaStackBacktest`: full PIT-safe daily loop; persistence to `outputs/alpha_stack/<start>_<end>/` |
| `shadow_runner.py` | ✅ Complete | `ShadowRunner`: single-date shadow; writes to `outputs/alpha_stack_shadow/`; CLI with `--enable` flag |

### 8. Tests (`Tests/alpha_stack/`)

| File | Coverage |
|------|----------|
| `test_config.py` | Config loading, all flags default false, is_enabled(), section presence |
| `test_datastore_pit.py` | PIT filter, FundamentalsDataStore stub, filing lags |
| `test_regime_transitions.py` | All state classifiers, hysteresis dwell/confirmation/crisis, RegimeContext properties |
| `test_sleeve_registry.py` | Registry registration, active_sleeves(), disabled sleeves return active=False |
| `test_trend_sleeve.py` | Eligibility, scoring [0,100], weight constraints, full run, empty data |
| `test_allocator.py` | Base weights, vol/breadth/macro modifiers, drawdown breaker, full allocation |
| `test_metrics.py` | Sharpe, Sortino, MaxDD, CAGR, IC, turnover, annualised_vol, summarise_performance |

---

## Deviations from Spec

| Area | Spec | Implementation | Reason |
|------|------|----------------|--------|
| Fundamentals | PIT-safe fundamentals via SEC EDGAR or vendor | SEC EDGAR XBRL EdgarClient; EPS, Equity, Shares, OpCF, CapEx, NI, Assets, Liabilities (raw) + EY, FCFY, B/P (computed) | ✅ Implemented; filing-date semantics enforced |
| Value Sleeve | Active with P/E, P/B, P/FCF scoring | Active with EY, FCFY, B/P scoring (weights 0.40/0.30/0.30) | ✅ Implemented; flag-gated `ENABLE_VALUE_SLEEVE` |
| Quality Sleeve | Active with ROE, ROIC, leverage scoring | Disabled stub (`active=False`) | Deferred to v1.1; awaits quality fundamentals |
| Macro classification | Full macro regime (yield curve, ISM, etc.) | Approx: TLT/HYG 20-day returns as proxy | Adequate for shadow mode; see remaining work |

---

## Known Limitations

1. **Shareholder Yield unavailable**: SEC EDGAR does not expose dividend/buyback data for all companies; therefore shareholder_yield metric is not computed. Only earnings_yield, fcf_yield, book_to_price are available.

2. **SEC EDGAR rate limits**: EdgarClient enforces <10 req/s per sec.gov ToS; hence Parquet caching with 7-day TTL. Fresh data fetches are rate-limited; cold-start intraday runs may experience delays.

3. **Filing lag realism**: US companies file 10-Q within 45 days (small-cap accelerated 40 days), 10-K within 75 days. Value sleeve backtest assumes 60-day conservative lag. Real trading should use actual filed_date <= today, not assumptions.

3. **Macro proxy is approximate**: Using TLT/HYG 20-day returns as a macro proxy is a simplification. A more robust implementation would use ISM PMI, yield curve slope, and credit spread data.

4. **No live intraday support**: All datastores assume end-of-day prices. Intraday pricing would require a different data provider.

5. **Backtest does not model market impact**: The cost model uses flat slippage (25bps) regardless of position size or liquidity. Large positions in small-cap names would have higher market impact.

6. **Shadow NAV is simplified**: `shadow_runner.py` records target allocation but does not compute true mark-to-market P&L (would require comparing previous day's book against today's closes). See `_update_nav()` docstring.

7. **No cross-asset sleeves**: Options overlay, fixed income, and commodity sleeves are not implemented.

---

## Remaining Work (Prioritised)

### P0 — Required before any forward results are meaningful
- [x] Wire a real PIT-safe fundamentals source (SEC EDGAR XBRL) — **COMPLETED**
- [x] Implement and test Value sleeve end-to-end — **COMPLETED**
- [ ] Run `pytest Tests/alpha_stack/ -v` — validate new PIT tests (test_fundamentals_pit.py, test_value_features.py)
- [ ] Enable `ENABLE_VALUE_SLEEVE: true` in alpha_stack/config/alpha_stack.yaml

### P1 — Before shadow mode goes live
- [ ] Create `scripts/alpha_stack_shadow.py` CLI entrypoint
- [ ] Add shadow-mode GitHub Actions workflow (`.github/workflows/alpha_stack_shadow.yml`)
- [ ] Enable `ENABLE_ALPHA_STACK=true` and `ENABLE_ALPHA_STACK_SHADOW=true` (and verify `ENABLE_VALUE_SLEEVE=true`) in YAML after P0 complete
- [ ] Backtest validation: run `AlphaStackBacktest` over 2020–2024 with Value sleeve enabled; verify Sharpe, MaxDD, IC in expected range

### P2 — Research validation milestones
- [ ] Walk-forward IC > 0.03 on trend sleeve (monthly rebalance)
- [ ] MaxDD < 30% in 2018, 2020, 2022 risk-off periods
- [ ] Annual turnover < 400% gross
- [ ] Regime attribution showing correct allocation shifts in crisis periods

### P3 — Promotion to production consideration
- [ ] All P0/P1/P2 complete
- [ ] 6+ months of clean shadow track record
- [ ] Correlation with existing production sleeve < 0.7
- [ ] Stress test: 2008-style scenario (–50% equity drawdown)
- [ ] Risk team sign-off
- [ ] Proper connection to production portfolio state (reconciliation module)

---

## How to Run

### Run shadow mode (testing only — forces flags on)
```bash
cd quant-daily-report-main
python -m alpha_stack.research.shadow_runner --enable --date 2024-06-01
```

### Run backtest
```python
from alpha_stack.research.backtest import AlphaStackBacktest
bt = AlphaStackBacktest()
result = bt.run(start_date="2020-01-01", end_date="2024-01-01")
```

### Enable flags in config
Edit `alpha_stack/config/alpha_stack.yaml`:
```yaml
feature_flags:
  ENABLE_ALPHA_STACK: true
  ENABLE_ALPHA_STACK_SHADOW: true
```

### Run tests
```bash
pytest Tests/alpha_stack/ -v
```

---

## Production Safety Verification

The following production files were **not modified**:

- `daily_quant_report.py`
- `reconciliation.py`
- `paper/` (all files)
- `sleeves/sleeve_trend/` (all files)
- `engine/` (all files)
- `.github/workflows/` (all files)
- `core/` (all files)
- `data/universe.csv` (read-only access)

The `alpha_stack/` namespace imports nothing from the production execution path. The only shared dependency is the `data/universe.csv` universe file, which is read-only.

---

*Generated automatically by Alpha Stack v1 build process.*
