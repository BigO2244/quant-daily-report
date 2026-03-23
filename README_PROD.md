# Alpha Stack Run Commands

## Environment Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Daily Report / Shadow

```bash
REPORT_DATE=$(TZ=America/New_York date +%F) MODE=shadow TRADING_MODE=shadow python3 daily_quant_report.py
```

## Alpaca Paper

```bash
REPORT_DATE=$(TZ=America/New_York date +%F) MODE=alpaca TRADING_MODE=alpaca ALPACA_PAPER=1 ALPACA_API_KEY_ID=... ALPACA_API_SECRET_KEY=... ALPACA_BASE_URL=https://paper-api.alpaca.markets python3 daily_quant_report.py
```

## Current Platform Notes

- Alpha Stack is a multi-sleeve platform, not a single-strategy script.
- Sleeve 1 is partially implemented.
- Sleeve 2 is fully implemented but its backtests still have look-ahead bias because P/E is fetched from yfinance snapshot data.
- Current allocator baseline is 80% Sleeve 1 and 20% Sleeve 2 on a $10,000 notional baseline.
