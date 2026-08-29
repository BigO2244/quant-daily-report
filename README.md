# Alpha Stack

Alpha Stack is the Caerus quantitative trading platform for US long-only equities plus a gated options overlay. The current operating reality is:
- Live: Caerus Lyra — active, funded, recurring Tuesday rebalance
- Paper: Caerus Orion — active, full-current-account PAPER lane
- Shadow: Polaris, Orion, Lyra, and concentration comparisons
- new strategy variants are validated through research and shadow lanes first
- promotion stays explicit: `research -> backtest -> shadow -> paper -> live`

## Current State

- Daily orchestrator: `daily_quant_report.py`
- Capital lanes: **Caerus Lyra Live** and **Caerus Orion PAPER**
- Research control: **Caerus Polaris**
- Lyra and Orion also remain in Shadow for modeled comparison; lane state and
  research lifecycle are separate axes.
- Benchmark: **SPY**

## Named Strategies

- `Caerus Polaris` / `caerus_polaris`
  - historical research baseline / Shadow control; no capital authority
- `Caerus Orion` / `caerus_orion`
  - active PAPER capital sleeve and simultaneous Shadow comparison
  - Alpha Lab v2 lead candidate: H2 rank-decay exit + H6 top-5 concentration
- `Caerus Lyra` / `caerus_lyra`
  - active, separately governed Live portfolio and simultaneous Shadow comparison
  - Alpha Lab v2 challenger: H1 weekly rebalance + H6 top-5 concentration
- `SPY` / `spy_benchmark`
  - benchmark only

Current promotion state:
- Orion is PAPER-authorized and Shadow-observed
- Lyra is Live-authorized and Shadow-observed
- Polaris is the historical Shadow research control
- Shadow is artifact-only and non-blocking

Machine authority: `config/operations/operating_lane_registry.json`. Current
runtime truth: `outputs/operating_state/current/operating_truth.json`. Generated
human view: `docs/CURRENT_OPERATING_STATE.md`.

## Upstream Research Automation

The owner-approved Alpha Lab weekend research cycle is a Codex automation,
not a production VM cron job or GitHub Action. It runs Sunday from 00:05 through
05:00 America/New_York against the dedicated Alpha Lab project and remains
research-only. The complete four-authority discovery rule and schedule map are
recorded in `docs/governance/WORKFLOW_AUTHORITY_REGISTRY.md`.

## Seven-Layer Architecture

1. Data Layer: yfinance OHLCV plus snapshot fundamentals; FRED macro integration is planned.
2. Feature Layer: derived indicators computed from raw data.
3. Signal Layer: per-sleeve entry and exit scores.
4. Regime Layer: four-dimension classifier across trend, volatility, breadth, and macro. A state machine with hysteresis is planned.
5. Portfolio Construction Layer: sleeve weighting and position sizing.
6. Execution Layer: order generation and trade tracking.
7. Attribution Layer: IC/IR measurement and performance reporting.

## Sleeve Status

| Sleeve | Status | Notes |
|---|---|---|
| Sleeve 1 | Partial | Factor pipeline stubs remain in `core/quant_report.py`; signal logic under `sleeves/sleeve_1/` is not part of this handoff. |
| Sleeve 2 | Implemented | Uses yfinance `.info` snapshot P/E, z-score thresholds, score ranks, hold-day limits, and SGOV as cash proxy. |
| Sleeve 3 | Planned | Signals not yet defined. |
| Sleeve 4 | Planned | Signals not yet defined. |

## Key Files

| File | Role |
|---|---|
| `daily_quant_report.py` | Daily orchestrator: runs sleeves, builds HTML report, optionally emails |
| `core/quant_report.py` | Shared utilities: universe loader, price download, ATR, SMTP sender |
| `core/portfolio_alloc.py` | Sleeve scaling and portfolio combining |
| `sleeves/sleeve_2/config.py` | Sleeve 2 parameters |
| `sleeves/sleeve_2/valuation.py` | yfinance `.info` snapshot fetch and cache |
| `sleeves/sleeve_2/signals.py` | Valuation and trend composite score computation |
| `sleeves/sleeve_2/backtest.py` | Sleeve 2 realized-PnL backtest |
| `data/universe.csv` | 200-ticker trading universe with sector tags |

## Running the System

Environment setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Shadow run:

```bash
REPORT_DATE=$(TZ=America/New_York date +%F) MODE=shadow TRADING_MODE=shadow python3 daily_quant_report.py
```

Alpaca paper run:

```bash
REPORT_DATE=$(TZ=America/New_York date +%F) MODE=alpaca TRADING_MODE=alpaca ALPACA_PAPER=1 ALPACA_API_KEY_ID=... ALPACA_API_SECRET_KEY=... ALPACA_BASE_URL=https://paper-api.alpaca.markets python3 daily_quant_report.py
```

## Environment Variables

- `REPORT_DATE`
- `MODE`
- `TRADING_MODE`
- `ALPACA_PAPER`
- `ALPACA_API_KEY_ID`
- `ALPACA_API_SECRET_KEY`
- `ALPACA_BASE_URL`
- `EMAIL_SENDER`
- `EMAIL_APP_PASSWORD`
- `EMAIL_RECIPIENT`

## Daily Workflow Note

After successful precompute, the system now runs a best-effort shadow generation step:
- wrapper: `scripts/run_shadow_candidates_daily.sh`
- called from: `scripts/cron_precompute.sh`
- outputs:
  - `outputs/shadow_candidates/YYYY-MM-DD/`
  - `outputs/shadow_candidates/performance/`

This shadow lane writes target books and comparison artifacts for Polaris, Orion, and Lyra. It does not send orders and cannot block production execution.

## Known Issues / Technical Debt

1. Sleeve 2 has look-ahead bias in backtests because it uses yfinance snapshot P/E rather than point-in-time fundamentals.
2. Sleeve 2 backtests currently return only the final equity point instead of a full daily equity curve.
3. Sleeve 1 factor functions in `core/quant_report.py` are still stubs: `fetch_factor_data`, `build_factor_scores`, and `compute_full_signals`.
4. The regime layer is not yet a fully defined state machine with explicit thresholds and hysteresis rules.
5. No transaction cost model is applied in current backtests.
6. No portfolio-level risk controls are yet defined for position caps, sector limits, or drawdown circuit breakers.
7. Report outputs do not yet include benchmark comparison versus SPY or the S&P 500 total return series.
8. The repository has a `requirements.txt`, but dependency pinning and environment-governance standards still need review as part of production hardening.

## Planned Implementation Sequence

1. Data foundation: FRED macro integration plus point-in-time correct fundamental caching.
2. Regime state machine: four-dimension classifier with hysteresis.
3. Trend sleeve: extend Sleeve 1 with sector-relative signals and ATR-based sizing.
4. Value sleeve: refactor Sleeve 2 with point-in-time correct fundamentals and multi-metric composite.
5. Attribution module: IC/IR measurement before adding more sleeves.
6. Allocator v1: static weights with regime overrides.
7. Quality sleeve.
8. Mean Reversion sleeve.
9. Shadow mode: 60+ trading days alongside the legacy model.
10. Production cutover and legacy archive.

## Documentation Map

- [Architecture Reference](docs/Alpha_Stack_Architecture_Reference.md)
- [Shadow Testing: Polaris / Orion / Lyra](docs/shadow_testing_caerus_orion_lyra.md)
- [Alpha Stack Docs Index](docs/alpha_stack/README.md)
- [Architecture Overview](docs/alpha_stack/architecture_overview.md)
- [Sleeve Specifications](docs/alpha_stack/sleeve_specifications.md)
- [Regime Allocator Spec](docs/alpha_stack/regime_allocator_spec.md)
- [Data Standards](docs/alpha_stack/data_standards.md)
- [Research Validation Spec](docs/alpha_stack/research_validation_spec.md)
- [Model Strategy](docs/model_strategy.md)
