# Quick Start

This repository hosts Alpha Stack, a regime-switching multi-sleeve equity platform with a daily HTML report and staged promotion controls.

## Current Strategy State

- `Caerus Polaris` / `caerus_polaris`: current paper baseline / operational control
- `Caerus Orion` / `caerus_orion`: primary shadow candidate
- `Caerus Lyra` / `caerus_lyra`: secondary shadow challenger
- `SPY` / `spy_benchmark`: benchmark
- Current promotion state:
  - Polaris = paper
  - Orion = shadow only
  - Lyra = shadow only

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

Daily shadow generation only:

```bash
python3 -m research.shadow_tracking.run \
  --trade-date YYYY-MM-DD \
  --start-date 2014-01-01 \
  --end-date YYYY-MM-DD \
  --output-dir outputs/shadow_candidates
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

Daily automation now runs shadow generation after successful precompute via `scripts/run_shadow_candidates_daily.sh`. That shadow step is artifact-only, non-blocking, and does not affect paper execution.
