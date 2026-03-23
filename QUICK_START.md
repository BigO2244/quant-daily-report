# Quick Start

This repository hosts Alpha Stack, a regime-switching multi-sleeve equity platform with a daily HTML report and staged promotion controls.

## Current Baseline

- Sleeve 1: Trend/Momentum, partially implemented
- Sleeve 2: Value, fully implemented
- Baseline portfolio mix: 80% Sleeve 1 / 20% Sleeve 2
- Capital baseline: $10,000
- Cash proxy for Sleeve 2: `SGOV`

## Run Locally

Setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Shadow mode:

```bash
REPORT_DATE=$(TZ=America/New_York date +%F) MODE=shadow TRADING_MODE=shadow python3 daily_quant_report.py
```

Alpaca paper mode:

```bash
REPORT_DATE=$(TZ=America/New_York date +%F) MODE=alpaca TRADING_MODE=alpaca ALPACA_PAPER=1 ALPACA_API_KEY_ID=... ALPACA_API_SECRET_KEY=... ALPACA_BASE_URL=https://paper-api.alpaca.markets python3 daily_quant_report.py
```

## Files to Know

- `daily_quant_report.py`
- `core/quant_report.py`
- `core/portfolio_alloc.py`
- `sleeves/sleeve_2/config.py`
- `sleeves/sleeve_2/valuation.py`
- `sleeves/sleeve_2/signals.py`
- `sleeves/sleeve_2/backtest.py`
- `data/universe.csv`

## Known Issues

- Sleeve 2 backtests are not point-in-time safe because P/E comes from yfinance snapshot `.info`.
- Sleeve 2 backtests need a full daily equity curve, not only a terminal equity result.
- Sleeve 1 factor functions in `core/quant_report.py` remain stubs.
- The regime layer is still conceptual and not yet a hysteresis-driven state machine.
- Backtests are gross only; transaction costs are not modeled.

## Promotion Ladder

`research -> backtest -> shadow -> paper -> live`

Alpha Stack should continue alongside the frozen legacy model until validation gates are satisfied.
