# Caerus Quant Model — Change Log
**Author:** Brett Olson
**Period:** Early 2026
**Reviewed by:** Claude (Anthropic)

---

## Background

This document is a historical change log, not the current architecture spec.

Current live status as of April 9, 2026:

- `sleeve_trend`, `sleeve_2` (value), `sleeve_quality`, and `sleeve_mean_reversion` feed the live allocator in `daily_quant_report.py`
- `sleeve_1` still runs as research and its output is discarded in the live path
- all trading remains long-only US equities; no options or leverage are used

The earlier entries below describe the system at the time of those commits.

| Component | Role |
|---|---|
| Sleeve Trend | EMA crossover (20/50/200) + ADX filter → live Alpaca orders |
| Sleeve 1 | Cross-sectional momentum scoring → research / backtesting only |
| PortfolioAllocator | Combines sleeve outputs, enforces position caps, routes residual to CASH |
| daily_quant_report.py | Orchestrator — runs sleeves, allocates, emails report |

---

## Commit 1 — Tier 1 Model Enhancements
**`0a7a717`** — *Tier 1 model enhancements: config extraction, WFO validation, IC monitoring*

### 1.1 Sleeve 1 Config Extraction
**File:** `sleeves/sleeve_1/config.py` *(new)*
**File:** `sleeves/sleeve_1/backtest.py` *(modified)*

All 25 hardcoded constants previously inline in `backtest.py` were extracted into a dedicated `config.py`, mirroring the pattern already used by Sleeve Trend. No parameter values were changed — this was a structural extraction only.

Before, changing any threshold required finding it buried inside logic. Now it lives in one place with explicit names (`LONG_THRESHOLD`, `MAX_HOLD_DAYS`, `VOL_BUCKET_RULES`, etc.).

### 1.2 Walk-Forward Validation (WFO)
**File:** `backtests/sleeve1_robustness.py` *(modified)*

Added `run_walk_forward()` implementing rolling train/OOS windows. Each window trains on 12 months of in-sample data and tests on the following 3 months of out-of-sample data, advancing by one OOS period each iteration.

New CLI flags:
```bash
python -m backtests.sleeve1_robustness --wfo
python -m backtests.sleeve1_robustness --wfo --wfo_train_months 12 --wfo_oos_months 3 --start 2015-01-01
```

Output written to `outputs/backtests/sleeve1_robustness/wfo_results.csv`.

Initial run (2023–2024, 4 windows): Mean OOS Sharpe 2.97, Mean IS Sharpe 1.60. OOS > IS is a bull-market artifact — the extended 2015–present run (36 windows) is the statistically meaningful validation.

### 1.3 Signal IC Monitor
**File:** `research/ic_monitor.py` *(new)*
**File:** `daily_quant_report.py` *(modified)*

Added a rolling Information Coefficient (IC) monitor for Sleeve Trend signals. IC measures the Pearson correlation between today's signal score and next-day forward return — a leading indicator of edge decay.

Runs automatically after each daily report via a non-blocking hook. Outputs:
- `outputs/ic_monitor/ic_daily.csv` — daily IC log
- `outputs/ic_monitor/ic_rolling_60d.csv` — 60-day rolling mean IC
- `outputs/ic_monitor/ic_summary.json` — current status + automated alerts

Alerts fire when: IC < 0 for 20+ consecutive days, or rolling 60-day mean IC < 0.03.

---

## Commit 2 — Codebase Cleanup
**`c9bfc96`** — *Cleanup: remove dead wrapper, fix breaker logger, update sleeve defaults, clarify IC scope*

### 2.1 Deleted Root `portfolio_alloc.py`
The root-level `portfolio_alloc.py` was a 3-line re-export wrapper (`from core.portfolio_alloc import *`). Every active import in the codebase already pointed directly at `core.portfolio_alloc`. Confirmed unused via grep and deleted.

### 2.2 Fixed Logging in `engine/breaker.py`
The breaker policy function used a bare `print()` statement while every other module in the engine layer used Python's `logging` module. Replaced with `logger.debug()` so breaker events appear in structured log output and can be filtered/captured consistently.

### 2.3 Corrected `DEFAULT_SLEEVE_WEIGHTS`
**File:** `core/portfolio_alloc.py`

`DEFAULT_SLEEVE_WEIGHTS` previously listed `sleeve_2` and `charlie_munger` alongside `sleeve_trend` despite both being disabled. Updated to reflect live reality:
```python
DEFAULT_SLEEVE_WEIGHTS = {"sleeve_trend": 1.00}
```

`STASH_SLEEVE_NAME` default changed from `"charlie_munger"` (disabled) to `"CASH"`.

### 2.4 IC Monitor Docstring Correction
**File:** `research/ic_monitor.py`

Docstring incorrectly referenced "Sleeve 1 signals." Corrected to "Sleeve Trend signals" — Sleeve Trend is the only active live signal source feeding the IC monitor.

---

## Commit 3 — Tier 2: VIX Regime Detection
**`bf560f6`** — *Tier 2: VIX regime detection — position scaling and top_n capping*

### Overview

Adds a four-regime volatility classifier driven by the CBOE VIX index. In high-volatility environments the strategy automatically reduces position count and routes undeployed capital to CASH. No options, derivatives, or new instruments are required.

### Regime Table

| Regime | VIX Range | Position Scale | Max Positions | Behavior |
|---|---|---|---|---|
| LOW | < 20 | 100% | 10 | Full deployment, risk-on |
| ELEVATED | 20–30 | 75% | 7 | Cautious, trim exposure |
| HIGH | 30–40 | 50% | 4 | Defensive, move toward cash |
| CRISIS | ≥ 40 | 25% | 2 | Near-cash, 2 positions max |

### How It Works

Position scaling operates at two levels simultaneously:

1. **Position count cap** (`build_sleeve_output.py`): `top_n` is capped to `regime["max_positions"]` before selection runs. In CRISIS, only the top 2 names by composite score are considered.

2. **Gross exposure cap** (`daily_quant_report.py` → `PortfolioAllocator`): `position_scale` is passed as `min_gross_exposure` to the allocator. The allocator's existing boost-to-min-gross-exposure step fills only up to this level; the remainder routes automatically to CASH via `_add_cash_allocation`. No changes to the allocator's internal logic were required.

### Files Changed

**`sleeves/sleeve_trend/config.py`** — VIX threshold, scale, and max-position constants added as named tunable parameters:
```python
VIX_LOW_THRESHOLD = 20
VIX_ELEVATED_THRESHOLD = 30
VIX_HIGH_THRESHOLD = 40
VIX_SCALE_LOW = 1.00
VIX_SCALE_ELEVATED = 0.75
VIX_SCALE_HIGH = 0.50
VIX_SCALE_CRISIS = 0.25
VIX_MAX_POSITIONS_LOW = 10
VIX_MAX_POSITIONS_ELEVATED = 7
VIX_MAX_POSITIONS_HIGH = 4
VIX_MAX_POSITIONS_CRISIS = 2
VIX_FETCH_FALLBACK = 25.0  # used if yfinance unavailable
```

**`research/vix_regime.py`** *(new)* — standalone regime module:
- Fetches `^VIX` via yfinance
- Classifies into one of four regimes
- Persists to `outputs/vix_regime/regime_current.json` and `regime_history.csv`
- Supports `vix_override=` for backtesting and testing
- CLI: `python -m research.vix_regime [optional_vix_level]`

**`sleeves/sleeve_trend/build_sleeve_output.py`** — accepts optional `regime` dict, caps `top_n` by `regime["max_positions"]`

**`daily_quant_report.py`** — calls `get_current_regime()` after sleeve runs (non-blocking, falls back to full deployment on error), passes `position_scale` as `min_gross_exposure` to `PortfolioAllocator`

### Failure Handling

If yfinance is unavailable (network issue, market closure), the regime module falls back to VIX = 25.0 (ELEVATED, 75% scale) — conservative but not zero. If the entire `get_current_regime()` call fails, the daily report logs a warning and defaults to full deployment, preserving existing behavior.

---

## Pending / Roadmap

| Priority | Item | Status |
|---|---|---|
| Now | Extended WFO (2015–present, ~36 windows) | Running |
| Tier 2 | Unit tests for indicator functions | Backlog |
| Tier 2 | Secondary data provider (backup for Yahoo Finance) | Backlog |
| Tier 3 | SPY puts overlay for explicit drawdown hedge | **Phase 2A shadow-only confirmed 2026-04-09**; no live execution path |
| Tier 3 | Options paper/promotion review lane | **Phase 2B in progress**; paper-ready review artifacts, no execution |
| Tier 3 | Options strategy overlay | **Phase 2C expanded**; shadow/paper-review candidates now include protective puts, put spreads, covered calls, long straddles, call butterflies, and LEAP calls |
| Tier 3 | Options execution lane | **Phase 2C scaffolded**; gated, disabled-by-default submit path for protective SPY puts (`ALLOW_OPTIONS_EXECUTION=1`) |
| Tier 3 | Defensive Treasury ETF sleeve | **Phase 3A confirmed 2026-04-09**; live-capable, regime-gated |
| Architectural | Decide fate of Sleeve 1: integrate into live allocator or formally archive | Open |
| Architectural | Remove `run_sleeve_2()` / `run_charlie_munger()` stubs from daily report | Open |

---

## Key Architectural Facts (for reference)

- **Live sleeve set:** `sleeve_trend`, `sleeve_2` (value), `sleeve_quality`, `sleeve_mean_reversion`, `sleeve_defensive_etf`. Sleeve 1 output is discarded (`_, _ = run_sleeve_1()`).
- **No live options execution path by default.** Phase 2A writes shadow SPY hedge recommendations only (`mode=shadow_only`); Phase 2B adds paper-review artifacts only; Phase 2C scaffolds a gated, opt-in protective-put submit path but keeps live order routing disabled until explicitly enabled via `ALLOW_OPTIONS_EXECUTION=1` / `ALLOW_OPTIONS_SUBMISSION=1`. The overlay model now evaluates richer review-only structures — covered calls, long straddles, call butterflies, and LEAP calls — but those structures remain paper-review artifacts until execution, covered-inventory validation, and options risk limits are explicitly promoted.
- **Defensive ETF sleeve** activates only in `risk_off_defensive`, `high_volatility`, or `breadth_washout` regimes; freed weight falls to cash on invalid output.
- **Drawdown circuit breakers** exist at 10% (soft — reduce size) and 15% (hard — stop new entries) in `engine/breaker.py`.
- **The venv Python binary is macOS-only** and cannot run in the Linux sandbox. Use `source .venv/bin/activate` from your Mac terminal for all WFO and live runs.
