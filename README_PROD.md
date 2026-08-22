# Alpha Stack Run Commands

## Current Strategy State

- `Caerus Orion` / `caerus_orion` is the sole PAPER capital sleeve.
- `Caerus Lyra` / `caerus_lyra` is a Shadow comparison series and the strategy
  in the separately governed, owner-approved Live portfolio.
- `Caerus Polaris` / `caerus_polaris` is the historical research baseline and
  Shadow comparison control.
- `SPY` remains the benchmark.
- Lyra is not promoted to PAPER; its Live authority is an independent lane.

Current multi-lane authority: `docs/CURRENT_OPERATING_STATE.md`.

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
- Successful precompute now triggers `scripts/run_shadow_candidates_daily.sh` as a non-blocking artifact-only step.
- Shadow artifacts are written to `outputs/shadow_candidates/YYYY-MM-DD/` and `outputs/shadow_candidates/performance/`.
- Shadow failures are logged and swallowed; they do not block production execution.
