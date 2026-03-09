# Alpha Stack v1 — Deliverables Summary

**Build Date:** 2024
**Status:** Research / Shadow Mode — All feature flags default `false`
**Test Result:** 100 / 100 tests passing
**Production Impact:** Zero — no production files modified

---

## What Was Built

Alpha Stack v1 is a complete parallel research/shadow framework: modular sleeves, regime engine, regime-aware portfolio allocator, attribution harness, and PIT-safe data interfaces. It runs entirely in the `alpha_stack/` namespace and shares nothing with the production execution path.

### Exact Files Added

#### Core Library — `alpha_stack/` (34 files)

```
alpha_stack/
├── __init__.py                        — Package entry, is_enabled(), load_config()
├── _config_loader.py                  — YAML loader with caching; get_flag(), get_section()
├── config/
│   └── alpha_stack.yaml               — Master config; all feature flags default false
├── datastore/
│   ├── __init__.py
│   ├── base.py                        — Abstract DataStoreBase; DataStorePITWarning
│   ├── prices.py                      — yfinance + Parquet cache; strict PIT filtering
│   ├── macro.py                       — VIX, SPY trend, TLT, HYG
│   ├── breadth.py                     — % above 200/50-DMA, A/D ratio
│   └── fundamentals.py                — ⚠️ STUB — returns None + emits PIT warning
├── features/
│   ├── __init__.py
│   ├── trend.py                       — r12_1, r6_1, r3_1, EMA ratios, z-scores
│   ├── volatility.py                  — realized_vol, ATR, vol_regime_label
│   ├── breadth.py                     — Breadth regime wrapper
│   ├── value.py                       — ⚠️ STUB — requires PIT fundamentals
│   └── quality.py                     — ⚠️ STUB — requires PIT fundamentals
├── regime/
│   ├── __init__.py
│   ├── state_machine.py               — TrendState (5), VolState (4), BreadthState (4), MacroState (3)
│   ├── hysteresis.py                  — 5-day dwell, 2-close confirm, max 1 jump, crisis bypass
│   └── context.py                     — RegimeContext dataclass; RegimeEngine orchestrator
├── sleeves/
│   ├── __init__.py
│   ├── base.py                        — Abstract SleeveBase; SleeveOutput; HoldState
│   ├── trend.py                       — Full TrendSleeve implementation
│   ├── mean_reversion.py              — MeanReversionSleeve + regime gate
│   ├── value.py                       — ⚠️ Stub (active=False until PIT fundamentals)
│   ├── quality.py                     — ⚠️ Stub (active=False until PIT fundamentals)
│   └── registry.py                    — Lazy-import registry; active_sleeves()
├── portfolio/
│   ├── __init__.py
│   ├── constraints.py                 — PortfolioConstraints; position/sector caps; smoothing
│   ├── sizing.py                      — inverse_vol_weights(), equal_weights()
│   └── allocator.py                   — AlphaStackAllocator v1; AllocationResult
└── research/
    ├── __init__.py
    ├── metrics.py                     — Sharpe, Sortino, MaxDD, CAGR, IC, turnover
    ├── attribution.py                 — Sleeve returns, IC series, regime attribution
    ├── backtest.py                    — PIT-safe full backtest loop
    └── shadow_runner.py               — Single-date shadow mode; CLI entrypoint
```

#### Tests — `Tests/alpha_stack/` (8 files, 100 tests)

```
Tests/alpha_stack/
├── __init__.py
├── test_config.py              — Config loading, all flags default false
├── test_datastore_pit.py       — PIT safety, FundamentalsDataStore stub
├── test_regime_transitions.py  — State classifiers, hysteresis, RegimeContext
├── test_sleeve_registry.py     — Registry, disabled sleeves, active_sleeves()
├── test_trend_sleeve.py        — Eligibility, scoring, weights, full run
├── test_allocator.py           — Base weights, vol/breadth/macro modifiers, drawdown breaker
└── test_metrics.py             — Sharpe, Sortino, MaxDD, CAGR, IC, turnover
```

#### Scripts and Docs

```
scripts/alpha_stack_shadow.py               — CLI wrapper for shadow runner
docs/alpha_stack/implementation_status.md  — Detailed build status, deviations, roadmap
docs/alpha_stack/alpha_stack_v1_deliverables.md  — This file
```

---

## Risks and Limitations

| Risk | Severity | Notes |
|------|----------|-------|
| No PIT fundamentals | HIGH | Value + Quality sleeves are stubs. Using them would introduce look-ahead bias. Explicit warning emitted. |
| yfinance reliability | MEDIUM | Occasional data gaps, adjusted-price inconsistencies. No validation against canonical source. |
| Macro proxy is approximate | LOW | TLT/HYG 20-day return as macro proxy; simplification vs. ISM PMI / yield curve data. |
| Shadow NAV simplified | LOW | Records target allocation; does not compute true mark-to-market P&L day-to-day. |
| No market impact model | LOW | Flat 25bps slippage regardless of position size or liquidity. |
| Backtest survivorship | MEDIUM | Universe is current — does not account for delisted/bankrupt companies in historical periods. |

---

## How to Run

### 1. Shadow mode (testing — forces flags on)

```bash
cd quant-daily-report-main
python scripts/alpha_stack_shadow.py --enable --date 2024-06-01
```

Writes to `outputs/alpha_stack_shadow/`:
- `target_book_<DATE>.json` — target portfolio
- `regime_<DATE>.json` — regime context
- `diagnostics_<DATE>.json` — sleeve diagnostics
- `summary_<DATE>.json` — high-level summary
- `shadow_nav.csv` — running log of daily allocation

### 2. Backtest

```python
from alpha_stack.research.backtest import AlphaStackBacktest

bt = AlphaStackBacktest()
result = bt.run(start_date="2020-01-01", end_date="2024-01-01")
# Outputs written to outputs/alpha_stack/2020-01-01_2024-01-01/
# Keys: nav.csv, regime_history.csv, sleeve_returns.csv, summary.json
```

### 3. Regime engine standalone

```python
from alpha_stack.regime.context import RegimeEngine

engine = RegimeEngine()
ctx = engine.classify("2024-06-01")
print(ctx)
# RegimeContext(2024-06-01) trend=neutral vol=normal breadth=mixed macro=neutral
```

### 4. Enable in production shadow schedule

Edit `alpha_stack/config/alpha_stack.yaml`:
```yaml
feature_flags:
  ENABLE_ALPHA_STACK: true
  ENABLE_ALPHA_STACK_SHADOW: true
```

Then add a GitHub Actions workflow calling `scripts/alpha_stack_shadow.py` (no `--enable` flag needed once config is updated).

### 5. Run tests

```bash
pytest Tests/alpha_stack/ -v
```

Or without pytest installed:
```bash
python3 run_alpha_stack_tests.py
```

---

## Promotion Checklist

Alpha Stack must not be connected to production until ALL of the following are checked:

### P0 — PIT Data (blocker)
- [ ] Wire PIT-safe fundamentals (SEC EDGAR XBRL, Calcbench, or Intrinio)
- [ ] Enable and validate Value sleeve end-to-end
- [ ] Enable and validate Quality sleeve end-to-end

### P1 — Shadow Validation
- [ ] Enable shadow flags; run 20+ consecutive trading days without error
- [ ] Verify regime transitions are sensible vs. contemporaneous market events
- [ ] Confirm target_book JSON is well-formed every day
- [ ] Confirm shadow outputs never write to production state directories

### P2 — Backtest Validation
- [ ] Run backtest 2015–2024; compute Sharpe, MaxDD, turnover, IC
- [ ] Walk-forward IC > 0.03 (monthly rebalance, trend sleeve)
- [ ] MaxDD < 30% in 2018, 2020, 2022 drawdown periods
- [ ] Annual gross turnover < 400%
- [ ] Regime attribution shows correct defensive shifts in crisis periods

### P3 — Paper Trading
- [ ] 6+ months of clean shadow-mode track record
- [ ] Correlation with existing production sleeve < 0.7
- [ ] Stress test: 2008-style scenario analysis

### P4 — Go/No-Go
- [ ] Risk team review and sign-off
- [ ] Connection to production portfolio state (reconciliation module)
- [ ] Rollback plan documented and tested

---

## Production Safety Verification

The following production files were **not modified** during this build:

| File / Directory | Status |
|-----------------|--------|
| `daily_quant_report.py` | ✅ Untouched |
| `reconciliation.py` | ✅ Untouched |
| `paper/` | ✅ Untouched |
| `sleeves/sleeve_trend/` | ✅ Untouched |
| `engine/` | ✅ Untouched |
| `core/` | ✅ Untouched |
| `.github/workflows/` | ✅ Untouched |
| `data/universe.csv` | ✅ Read-only access |

`README.md` was updated to add documentation links (non-functional change).

The `alpha_stack/` namespace has zero imports from any production module.
