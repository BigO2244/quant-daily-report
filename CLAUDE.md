# Alpha Stack — Project Context for Claude

## What This Is
A quantitative trading platform running on a GCP Compute Engine VM.
Connects to Alpaca paper trading API. Daily HTML report emailed to brett.olson@gmail.com.

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
4. SSH into VM and run manual confirmation: `python3 daily_quant_report.py`
5. Never edit files directly on the VM unless it is a hotfix

## Cron Schedule (America/New_York, weekdays only)
- 7:00 AM — precompute: `scripts/cron_precompute.sh`
- 9:35 AM — execution: `scripts/cron_execute.sh`
- 10:00 AM — confirm: `scripts/cron_confirm.sh`
- Logs: `logs/precompute_YYYY-MM-DD.log` etc.

## Architecture
Seven-layer quantitative platform with regime-switching allocation.

### Project Structure
```
quant-daily-report/
├── daily_quant_report.py       # main orchestrator
├── core/
│   ├── quant_report.py         # shared utilities, price download
│   ├── portfolio_alloc.py      # PortfolioAllocator, SleeveOutput
│   └── attribution.py          # IC, ICIR, Sharpe, factor decay
├── sleeves/
│   ├── sleeve_1/               # momentum sleeve (fully built)
│   │   ├── indicators.py
│   │   └── selection.py
│   └── sleeve_2/               # value sleeve (EDGAR XBRL, PIT-correct)
│       ├── valuation.py
│       ├── selection.py
│       └── signals.py
├── regime/                     # regime detection engine
│   ├── regime_config.py        # all thresholds and EWM spans — tune here
│   ├── regime_indicators.py    # computes raw + EWM indicators
│   ├── regime_classifier.py    # state machine, 4 dimensions + composite
│   ├── regime_allocator.py     # maps regime → sleeve weights
│   ├── regime_engine.py        # top-level orchestrator
│   └── fred_client.py          # FRED API wrapper with parquet caching
├── paper/
│   └── paper_broker.py         # execution engine, Alpaca integration
├── brokers/
│   ├── alpaca_broker.py
│   └── alpaca_snapshot.py
├── research/
│   └── vix_regime.py
├── data/
│   ├── universe.csv            # 200-ticker universe with sector tags
│   └── cache/
├── scripts/
│   ├── cron_precompute.sh
│   ├── cron_execute.sh
│   └── cron_confirm.sh
└── outputs/
    ├── runs/                   # per-run archives
    ├── paper_state/            # canonical_positions.json
    └── regime_validation/      # validation charts and scorecard

## Regime Engine
- Four dimensions: trend, volatility, breadth, macro
- EWM smoothing (not discrete day-count hysteresis)
- Validated 7/8 on known historical episodes (2010-2024)
- Only failing episode: 2013 Taper Tantrum — acceptable, not a bug
- Transition frequency: ~14.6/year (target: 10-30)
- FRED data: T10Y2Y, BAMLH0A0HYM2, DFF — cached as parquet
- Config: all thresholds in `regime/regime_config.py`

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
- regime "value" → "sleeve_2"
- regime "quality" → "sleeve_quality" (NOT YET BUILT)
- regime "mean_reversion" → "sleeve_mean_reversion" (NOT YET BUILT)

## Allocation Logic
- `PortfolioAllocator` in `core/portfolio_alloc.py`
- Inter-sleeve allocation driven by `SleeveOutput.meta.strength`
- `resolve_regime_strengths()` in `daily_quant_report.py` maps regime weights → sleeve strengths
- Cash weight from regime is redistributed proportionally across active sleeves
- Drift gate: only rebalance a sleeve if drift vs target exceeds 3% threshold
- `compute_sleeve_drift()` in `daily_quant_report.py` computes current vs target per sleeve

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

## Current Account State (as of 2026-03-22)
- Equity: $9,577.23
- Cash: $1,833.05
- Buying power: $11,410.28
- Positions: 9
- Mode: Alpaca paper trading

## Known Issues / Remaining Work
1. Regime classifier showing 98% neutral on VM — breadth/trend indicators
   may need longer price history to compute correctly
2. SPY benchmark series empty — attribution metrics lack benchmark comparison
3. Quality sleeve — NOT YET BUILT (signals defined in critique doc)
4. Mean Reversion sleeve — NOT YET BUILT
5. Portfolio-level risk controls — position caps, sector limits, drawdown
   circuit breaker not yet implemented
6. TRADING_MODE=live raises RuntimeError — not implemented

## Modeling Sophistication Assessment
- Infrastructure quality: 8-9/10 (strong)
- Modeling sophistication: 3-4/10 (improving)
- Regime engine: validated, live
- Look-ahead bias: fixed in Sleeve 2
- Attribution: built, not yet validated against live data

## Key Design Principles
- Regime weights are EWM-smoothed (not discrete day-count hysteresis)
- All thresholds in regime_config.py — never hardcode in logic
- Promotion ladder: shadow → paper → live (never skip steps)
- Point-in-time data correctness is non-negotiable for backtests
- Tune regime thresholds on market structure only — never on sleeve returns
- Drift gate prevents unnecessary turnover (3% minimum threshold)
```