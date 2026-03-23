# Alpha Stack — Project Context for Claude

## What This Is
A quantitative trading platform running on a GCP Compute Engine VM.
Connects to Alpaca paper trading API. Daily HTML report emailed to brett.olson@gmail.com.
Overarching project is called Caerus. Alpha Stack (also called Alpha) is the first strategy under Caerus.

## Environments
**Local Mac:** `brettolson@BDO-Macbook` — development and testing only
**GCP VM:** `brettolson@alpha-stack-scheduler` — live production
- External IP: `34.61.147.38`
- SSH: `ssh brettolson@34.61.147.38`
- Project path: `~/quant-daily-report`
- venv: always activate with `source venv/bin/activate` before running anything
- Secrets live in: `~/quant-daily-report/.env`

## Workflow
1. Develop and test locally on Mac
2. Confirm local run passes: `python3 daily_quant_report.py`
3. SCP changed files to VM: `scp file.py brettolson@34.61.147.38:~/quant-daily-report/`
4. SSH into VM and verify deployment — ALWAYS verify with md5sum or grep, never assume SCP succeeded
5. Never edit files directly on the VM unless it is a hotfix
6. Git commit is for version control only — it does NOT deploy to the VM
7. Always measure twice: any deployment confirmation must include a verification step on the VM

## Cron Schedule (America/New_York, weekdays only)
- 7:00 AM — precompute: `scripts/cron_precompute.sh`
- 9:35 AM — execution: `scripts/cron_execute.sh`
- 10:00 AM — confirm: `scripts/cron_confirm.sh`
- Logs: `logs/cron.log` (all three phases append here via crontab redirect)
- Phase-specific logs: `logs/precompute_YYYY-MM-DD.log`, `logs/execute_YYYY-MM-DD.log`, `logs/confirm_YYYY-MM-DD.log`
- Crontab has `TZ=America/New_York` and `CRON_TZ=America/New_York` as first two lines

## Architecture
Seven-layer quantitative platform with regime-switching allocation.

### Project Structurequant-daily-report/
├── daily_quant_report.py       # main orchestrator
├── core/
│   ├── quant_report.py         # shared utilities, price download
│   ├── portfolio_alloc.py      # PortfolioAllocator, SleeveOutput
│   ├── attribution.py          # IC, ICIR, Sharpe, factor decay
│   ├── risk_controls.py        # position caps, sector limits, drawdown circuit breaker
│   ├── benchmark_tracking.py   # SPY benchmark tracking
│   ├── email_env.py            # email credential resolver
│   └── operator_summary.py     # execution operator summaries
├── sleeves/
│   ├── sleeve_1/               # momentum sleeve (fully built)
│   │   ├── indicators.py
│   │   └── selection.py
│   ├── sleeve_trend/           # trend sleeve (fully built)
│   │   └── backtest.py
│   ├── sleeve_2/               # value sleeve (EDGAR XBRL, PIT-correct)
│   │   ├── valuation.py
│   │   ├── selection.py
│   │   └── signals.py
│   ├── sleeve_quality/         # quality sleeve (signals defined, NOT YET INTEGRATED)
│   │   ├── indicators.py
│   │   └── selection.py
│   └── sleeve_mean_reversion/  # mean reversion sleeve (signals defined, NOT YET INTEGRATED)
│       ├── indicators.py
│       └── selection.py
├── regime/                     # regime detection engine
│   ├── regime_config.py        # all thresholds and EWM spans — tune here
│   ├── regime_indicators.py    # computes raw + EWM indicators
│   ├── regime_classifier.py    # state machine, 4 dimensions + composite
│   ├── regime_allocator.py     # maps regime → sleeve weights
│   ├── regime_engine.py        # top-level orchestrator
│   └── fred_client.py          # FRED API wrapper with parquet caching
├── paper/
│   ├── paper_broker.py         # execution engine, Alpaca integration
│   └── perf_artifact_producers.py  # SPY/VIX close history, benchmark artifacts
├── brokers/
│   ├── alpaca_broker.py
│   └── alpaca_snapshot.py
├── scripts/
│   ├── cron_precompute.sh      # Phase 1: runs planner, writes bundle, sends trade plan email
│   ├── cron_execute.sh         # Phase 2: reads bundle, executes via Alpaca
│   ├── cron_confirm.sh         # Phase 3: sends confirmation email (silent on success, alerts on failure)
│   ├── run_precomputed_alpaca_execution.py  # core execution script called by cron_execute.sh
│   ├── send_trading_confirmation_email.py   # sends the 10AM confirmation email
│   ├── format_precompute_email.py           # formats trade plan for 7AM email
│   ├── send_precompute_email.py             # sends 7AM precompute trade plan email
│   └── diag_regime_engine.py               # regime diagnostic tool
├── research/
│   └── vix_regime.py
├── data/
│   ├── universe.csv            # 200-ticker universe with sector tags
│   └── cache/
├── outputs/
│   ├── runs/                   # per-run archives
│   ├── paper_state/            # canonical_positions.json, peak_equity.json
│   ├── precompute/             # precompute bundles: contract.json, signals.json, etc.
│   ├── benchmark/              # benchmark_vs_spy.json
│   └── regime_validation/      # validation charts and scorecard

## Email Flow (3 emails per trading day)
1. **7:00 AM** — `cron_precompute.sh` → `scripts/send_precompute_email.py` → trade plan email showing sells, buys, sizes, regime, VIX
2. **9:35 AM** — `cron_execute.sh` → `scripts/run_precomputed_alpaca_execution.py` → no email (execution only)
3. **10:00 AM** — `cron_confirm.sh` → `scripts/send_trading_confirmation_email.py` → single confirmation email with submitted/accepted/rejected counts

`cron_confirm.sh` is SILENT on success (no wrapper email). It only sends an alert email if the confirmation script exits non-zero.

## Precompute Bundle
Written by `daily_quant_report.py --plan-only --write-precompute-bundle` to:
`outputs/precompute/{YYYY-MM-DD}/`
- `contract.json` — metadata, validation, file manifest
- `daily_snapshot.json` — portfolio snapshot
- `signals.json` — signal scores
- `planned_execution_payload.json` — full trade list with sides, shares, notional, stop/take

## Regime Engine
- Four dimensions: trend, volatility, breadth, macro
- EWM smoothing (not discrete day-count hysteresis)
- Validated 7/8 on known historical episodes (2010-2024)
- Only failing episode: 2013 Taper Tantrum — acceptable, not a bug
- Transition frequency: ~14.6/year (target: 10-30)
- FRED data: T10Y2Y, BAMLH0A0HYM2, DFF — cached as parquet
- Config: all thresholds in `regime/regime_config.py`
- Diagnostic: `python3 scripts/diag_regime_engine.py --period 2y --window 252`

### Regime → Sleeve Weight Mapping
| Regime | Trend | Value | Quality | MeanRev | Cash |
|---|---|---|---|---|---|
| risk_on_trending | 45% | 20% | 20% | 10% | 5% |
| neutral_mixed | 30% | 25% | 25% | 15% | 5% |
| risk_off_defensive | 10% | 30% | 35% | 10% | 15% |
| high_volatility | 5% | 20% | 30% | 5% | 40% |
| breadth_washout | 15% | 15% | 20% | 40% | 10% |

### Sleeve Name Mapping
- regime "trend" → "sleeve_trend"
- regime "value" → "sleeve_2" (re-enabled 2026-03-23 via `run_sleeve_value()` → `build_value_sleeve_output()`)
- regime "quality" → "sleeve_quality" (integrated; requires `datastore.py` on VM)
- regime "mean_reversion" → "sleeve_mean_reversion" (integrated)

## Risk Controls (core/risk_controls.py)
- Max single position: 10% of total portfolio
- Max sector exposure: 30% of total portfolio (uses data/universe.csv sector tags)
- Max net long exposure: 95% (min 5% cash)
- Gross leverage: 100% (long only)
- Drawdown circuit breaker: reduce all sleeve sizes by 50% if portfolio draws down 15% from peak
- Peak equity persisted in: `outputs/paper_state/peak_equity.json`
- Integrated into `scripts/run_precomputed_alpaca_execution.py` before `run_paper_day()`

## Allocation Logic
- `PortfolioAllocator` in `core/portfolio_alloc.py`
- Inter-sleeve allocation driven by `SleeveOutput.meta.strength`
- `resolve_regime_strengths()` in `daily_quant_report.py` maps regime weights → sleeve strengths
- Cash weight from regime is redistributed proportionally across active sleeves
- Drift gate: only rebalance a sleeve if drift vs target exceeds 3% threshold

## Trading Modes
- `shadow` — pure simulation, no broker calls (default if TRADING_MODE not set)
- `alpaca` — connects to Alpaca paper API, submits real paper orders
- `live` — raises RuntimeError immediately, not implemented

## Broker Integration (Alpaca only)
- Paper API: `https://paper-api.alpaca.markets`
- Keys: `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY` in `.env`
- Pre-trade reconciliation: compares `canonical_positions.json` to live broker
- If mismatch: execution is blocked until bootstrap reset
- Bootstrap reset: `python3 daily_quant_report.py --bootstrap-model-ledger-from-broker`
- Capital sizing uses live Alpaca cash and equity
- `.env` loader pattern: scripts must load .env themselves using `os.environ.setdefault()` — do not rely on shell inheritance

## Data Sources
- **Price data:** yfinance (OHLCV, daily)
- **Macro/FRED:** T10Y2Y, BAMLH0A0HYM2, DFF via FRED API (requires FRED_API_KEY)
- **Fundamentals:** EDGAR XBRL API (no key needed, User-Agent header only)
- **Universe:** `data/universe.csv` — 200 tickers with sector tags
- **Known missing ticker:** MMC — delisted from yfinance, safe to ignore

## Environment Variables (all in .env on VM)
| Variable | Purpose |
|---|---|
| TRADING_MODE | alpaca / shadow |
| ALPACA_API_KEY_ID | Alpaca key |
| ALPACA_API_SECRET_KEY | Alpaca secret |
| ALPACA_BASE_URL | https://paper-api.alpaca.markets |
| ALPACA_PAPER | 1 |
| FRED_API_KEY | FRED macro data |
| SMTP_HOST | smtp.gmail.com |
| SMTP_PORT | 587 |
| SMTP_USER | brett.olson@gmail.com |
| SMTP_PASSWORD | Gmail app password |
| REPORT_TO_EMAIL | brett.olson@gmail.com |
| ACCOUNT_EQUITY | Starting equity (default 10000) |
| MAX_POSITION_PCT | Max notional per position (default 0.10) |
| MAX_RISK_PCT_PER_TRADE | Sleeve 1 risk per trade (default 0.01) |

## Current Account State (as of 2026-03-23)
- Equity: ~$9,651 (post first full execution)
- Positions: ~14 (after today's rebalance)
- Mode: Alpaca paper trading
- First successful full execution: 2026-03-23 (10 orders, all filled)

## Project Status Snapshot (updated 2026-03-23 PM)

### Production Status
- Cron pipeline is live on VM in paper mode:
  - 7:00 AM precompute
  - 9:35 AM execute
  - 10:00 AM confirm
- 10:00 AM duplicate confirmation email issue is fixed:
  - `cron_confirm.sh` is silent on success
  - `send_trading_confirmation_email.py` sends the only confirmation email
- `run_precomputed_alpaca_execution.py` must self-load `.env` using `os.environ.setdefault()` before broker imports
- Risk controls are implemented and integrated before `run_paper_day()`:
  - single-name cap
  - sector cap
  - net/gross exposure caps
  - drawdown circuit breaker with `outputs/paper_state/peak_equity.json`
- Benchmark artifact generation is working:
  - `outputs/benchmark/benchmark_vs_spy.json` writes successfully
  - full alpha / IR / drawdown attribution still needs more live equity history

### Regime Status
- Live regime on 2026-03-23 precompute snapshot:
  - `market_analyzer.regime = ELEVATED`
  - `market_analyzer.signal_bucket = DEFENSIVE`
  - `vix = 26.78`
- Regime diagnostics are available in:
  - `scripts/diag_regime_engine.py`
- The previously suspected "98% neutral" VM regime issue did not reproduce on the March 23 diagnostic pass

### Research Status
- Research-only sleeve backtest artifacts:
  - `research/backtest_quality_meanrev.py`
  - `research/backtest_results_quality_meanrev.json`
  - `research/backtest_equity_curves_quality_meanrev.png`
- 3-year research backtest results:
  - `quality_current`: 16.01% annualized return, 0.818 Sharpe, -26.36% max drawdown
  - `quality_enhanced`: 18.62% annualized return, 1.099 Sharpe, -20.80% max drawdown
  - `mean_reversion`: 14.37% annualized return, 0.849 Sharpe, -19.42% max drawdown
  - `SPY`: 18.88% annualized return, 1.222 Sharpe, -18.76% max drawdown
- No research signal breached the `|IC| > 0.15` look-ahead warning threshold
- Important research finding:
  - current production `sleeve_quality` code is simpler than the originally intended verbal spec
  - research variant adding sector-relative ROE, gross-margin stability, debt/equity trend, and revenue-growth consistency materially improved the backtest
- Mean reversion remains below SPY on a risk-adjusted basis and still does not implement the intended healthy-breadth gate

### TP/SL Research Status
- Research write-up:
  - `research/tpsl_analysis_2026-03-23.md`
- Research scenario runner:
  - `research/regime_aware_tpsl_2026-03-23.py`
  - `research/regime_aware_tpsl_results_2026-03-23.json`
- March 23 TP/SL finding:
  - no arithmetic bug found in CVX or the rest of the ATR-based names
  - current live template is a static `2.0 ATR` stop / `3.0 ATR` target
- Recommended research scenario for `ELEVATED / DEFENSIVE`:
  - `2.5 ATR` stop
  - `3.75 ATR` target
  - `0.80x` size to preserve dollar stop risk
- Important implementation caution:
  - integer-share rounding causes several 1-share names to round to zero under naive size scaling
  - if promoted, regime-aware TP/SL sizing should be applied before final share rounding

### Immediate Next Decisions
1. Decide whether `quality_enhanced` becomes the intended Quality sleeve spec for future promotion
2. Decide whether to add the missing healthy-breadth gate to Mean Reversion in research before promoting confidence in that sleeve
3. Decide whether regime-aware TP/SL should be implemented at the allocator/notional layer rather than by shrinking already-rounded share counts

## Known Issues / Remaining Work
1. **VM deployment verification** — always verify files on VM with md5sum after SCP, never assume
2. ~~**March 13 duplicate orders**~~ — FIXED 2026-03-23: atomic execution lock in `run_precomputed_alpaca_execution.py`; lock file at `outputs/execution_locks/{date}.lock` prevents TOCTOU duplicate submissions
3. ~~**Duplicate 10:00 AM confirmation email**~~ — FIXED 2026-03-23: `cron_confirm.sh` no longer sends a wrapper success email; only `send_trading_confirmation_email.py` sends the confirmation email
4. ~~**Execution script missing Alpaca credentials when run manually over SSH**~~ — FIXED 2026-03-23: `scripts/run_precomputed_alpaca_execution.py` now self-loads `.env` with `os.environ.setdefault()` before broker imports
5. ~~**`write_operator_summary()` caller mismatch**~~ — FIXED 2026-03-23: removed the unsupported keyword argument from the caller in `scripts/run_precomputed_alpaca_execution.py`
6. **SPY benchmark** — partially fixed; benchmark_vs_spy.json now writes, but alpha/IR/drawdown metrics need more equity observations before attribution is meaningful
7. ~~**sleeve_quality not integrated**~~ — FIXED 2026-03-23: integrated in pipeline; requires `datastore.py` deployed to VM (`scp datastore.py brettolson@34.61.147.38:~/quant-daily-report/`)
8. ~~**sleeve_mean_reversion not integrated**~~ — FIXED 2026-03-23: already integrated in pipeline
9. ~~**sleeve_2 inactive**~~ — FIXED 2026-03-23: re-enabled via `run_sleeve_value()` calling `build_value_sleeve_output()` directly
10. **Quality sleeve missing datastore on VM** — `datastore.py` exists locally but not on VM; deploy with `scp datastore.py brettolson@34.61.147.38:~/quant-daily-report/`
11. **TRADING_MODE=live raises RuntimeError** — not implemented
12. **Portfolio-level attribution** — IC, IR, factor decay curves are built, but not yet validated against a meaningful live history
13. ~~**CVX take profit anomaly**~~ — RECLASSIFIED 2026-03-23: no arithmetic bug found; current issue is design, not formula. Research recommends regime-aware ATR multipliers plus size reduction in `ELEVATED / DEFENSIVE`
14. ~~**VM regime stuck 98% neutral**~~ — RECLASSIFIED 2026-03-23: not reproduced on the March 23 diagnostic pass; keep `scripts/diag_regime_engine.py` as the validation tool if it reappears
15. **Quality sleeve spec mismatch** — current integrated code works, but research shows a stronger variant with sector-relative and stability/trend features; decision pending on whether to promote that richer spec
16. **Mean reversion breadth gate missing** — current integrated code does not implement the originally intended healthy-breadth regime gate
17. **Regime-aware TP/SL not integrated** — research scenario completed locally, but production integration is still pending and must account for integer-share rounding

## Modeling Sophistication Assessment
- Infrastructure quality: 8-9/10 (strong)
- Modeling sophistication: 4-5/10 (improving)
- Regime engine: validated, live
- Look-ahead bias: fixed in Sleeve 2
- Attribution: built, not yet validated against live data
- Risk controls: implemented, first live run 2026-03-23

## Key Design Principles
- Regime weights are EWM-smoothed (not discrete day-count hysteresis)
- All thresholds in regime_config.py — never hardcode in logic
- Promotion ladder: shadow → paper → live (never skip steps)
- Point-in-time data correctness is non-negotiable for backtests
- Tune regime thresholds on market structure only — never on sleeve returns
- Drift gate prevents unnecessary turnover (3% minimum threshold)
- All scripts must load .env themselves — never rely on shell environment inheritance
- Deployment rule: SCP first, verify on VM with md5sum, then confirm
