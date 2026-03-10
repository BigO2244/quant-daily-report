# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Caerus Quant is a quantitative equity trading and research platform. It generates daily long-only US equity selections via a technical trend-following strategy, executes paper trades through Alpaca's paper API, and maintains full audit trails. No live money, no short positions, no leverage.

## Common Commands

### Run Locally (Shadow Mode — No Broker)
```bash
REPORT_DATE=$(TZ=America/New_York date +%F) MODE=shadow TRADING_MODE=shadow python3 daily_quant_report.py
```

### Run Locally (Alpaca Paper Mode)
```bash
REPORT_DATE=$(TZ=America/New_York date +%F) MODE=alpaca TRADING_MODE=alpaca ALPACA_PAPER=1 \
  ALPACA_API_KEY_ID=... ALPACA_API_SECRET_KEY=... \
  ALPACA_BASE_URL=https://paper-api.alpaca.markets \
  python3 daily_quant_report.py
```

### Tests
```bash
pytest -q                                    # Full suite
pytest Tests/test_aiops_contracts.py -v      # AIOps contracts only
pytest Tests/test_allocation.py -v           # Single test file
bash scripts/local_green_loop.sh             # Smoke test without broker
```

### Alpha Research
```bash
python3 scripts/alpha_report.py --apply-costs --cost-bps 25
python3 scripts/daily_alpha_run.py           # Update live_nav.csv
python3 scripts/diag_alpaca_auth.py          # Validate broker credentials
python3 alpaca_smoke_test.py                 # Connectivity check
```

## Architecture

### Daily Execution Flow (9:35 AM ET via GitHub Actions)

```
daily_quant_report.py  (main orchestrator)
  ├── sleeves/sleeve_trend/selection.py   → Signal generation (EMA gates, ADX, scoring)
  ├── research/vix_regime.py              → VIX regime classifier (LOW/ELEVATED/HIGH/CRISIS)
  ├── core/portfolio_alloc.py             → Combine sleeves, inverse-vol weighting, enforce caps
  ├── engine/breaker.py                   → Circuit breakers (10% soft / 15% hard drawdown)
  ├── reconciliation.py                   → Pre-trade recon vs canonical snapshot; block if drift
  ├── paper/paper_broker.py               → Alpaca market order execution
  └── core/execution_summary.py           → Write run artifacts to outputs/runs/<RUN_ID>/
```

### Three GitHub Actions Workflows
- **`daily-alpaca-paper.yml`** — 9:35 AM ET: main engine → order execution → email delivery
- **`alpha_daily.yml`** — 6:15 AM ET: alpha report, update `data/live_nav.csv`, commit to repo
- **`research-digest.yml`** — 7:00 AM ET: AI macro digest via `quant_research_agent/`; isolated from trading

### Strategy: Sleeve Trend (only active production sleeve)
Entry requires **all** gates to pass:
- Price > 200-day EMA, EMA(20) > EMA(50), ADX ≥ 20
- Price ≥ $5, avg volume ≥ 100K shares/day

Ranking: 5-factor composite (trend 30%, momentum 25%, ADX 25%, rel. volume 10%, inv-vol bonus 10%).

Position sizing: inverse-volatility weighting. VIX regime scales position count and gross exposure.

Risk limits (all in `sleeves/sleeve_trend/config.py`):
- Max 10% per position, max 2 positions/sector, 5% min cash, 50% max gross exposure
- Circuit breakers: 10% soft drawdown (reduce 50%), 15% hard (no new entries)

### Reconciliation & Bootstrap
`reconciliation.py` is critical safety infrastructure:
- `pre_trade_reconcile_or_exit()` — compare canonical snapshot vs broker; halt on drift
- `bootstrap_model_ledger_from_broker()` — write canonical snapshot from broker state
- Canonical snapshot persisted via GitHub Actions cache (`outputs/paper_state/canonical_positions.json`)
- Auto-bootstrap on recon failure controlled by `AUTO_BOOTSTRAP_ON_RECON_FAIL` repo variable

### Key Architectural Rules
- **Sleeve 1** (`sleeves/sleeve_1/`) is research-only; its output is explicitly discarded in `daily_quant_report.py`. Do not wire it into production without a go/no-go decision.
- **Alpha Stack** (`alpha_stack/`, `docs/alpha_stack/`) is forward-architecture research only — documentation-first, not implemented. Production engine remains frozen to bug fixes and operational hardening.
- **Immutable run artifacts** are written to `outputs/runs/<RUN_ID>/` with checksums; never mutate these post-write.
- All email control is gated by flags in `core/email_governance.py`.

### Output Directories
| Path | Contents |
|------|----------|
| `outputs/runs/<RUN_ID>/` | Immutable per-run archive (reports, ledger, NAV, meta.json, checksums) |
| `outputs/paper_state/` | Canonical snapshot, ledger2.csv, nav2.csv |
| `outputs/execution_email/<DATE>.json` | Persisted execution email payload |
| `data/live_nav.csv` | Shadow NAV committed to repo (updated by alpha_daily workflow) |
| `signals/<DATE>.json` | Daily signal snapshot |

### Test Configuration
Tests live in `Tests/`. Config in `pytest.ini` sets `pythonpath = .` and excludes `archive`, `outputs`, `.venv`.

### Required Secrets (GitHub Actions)
`ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `ALPACA_PAPER`, `EMAIL_SENDER`, `EMAIL_APP_PASSWORD`, `EMAIL_RECIPIENT`, `ANTHROPIC_API_KEY`, `FRED_API_KEY`

## Agent Working Style
- Prefer minimal, surgical changes over broad rewrites unless explicitly requested.
- First inspect relevant files and briefly summarize current state before making large changes.
- Then propose a concise implementation plan.
- Then implement.
- Then run the narrowest relevant validation possible.
- Then summarize changes, validation results, and risks.

## High-Risk Areas
Treat these as sensitive and call out impact explicitly before changing:
- reconciliation.py
- broker / paper execution paths
- canonical state persistence under outputs/paper_state/
- daily_quant_report.py orchestration flow
- GitHub Actions workflows
- artifact schemas and CSV / JSON output contracts

## Research vs Production Discipline
- Keep experimental or alpha-discovery work under research/ or other explicitly research-only paths unless the task is to promote something into production.
- Do not wire research sleeves or forward-architecture components into production without explicitly stating that promotion is happening.
- Preserve the separation between production bug-fix / hardening work and exploratory alpha research.

## Validation Expectations
When code changes are made:
- Run the narrowest relevant tests first.
- If touching orchestration, reconciliation, execution, or reporting, run broader integration checks if available.
- Include exact commands run and their outcomes.
- If validation cannot be run, state why clearly.

## Change Hygiene
- Preserve deterministic artifact behavior unless the task explicitly changes artifact contracts.
- Do not silently rename, move, or remove operational files.
- If changing schema, output paths, or workflow expectations, document old vs new behavior.
- Add or update tests where appropriate.
- Production trading behavior should be treated as frozen except for explicitly requested fixes, safety improvements, and approved enhancements.
- Don't mix research and production
- Summarize before changing
- Valudation is required

## Suggested Model Routing
- Claude Sonnet: default for engineering logic and day-to-day repo work
- Claude Opus: use for deep architecture, audits, and ambiguous system problems
- Codex / GPT-5.3-Codex: preferred for implementation-heavy coding tasks
- GPT-5.4: use for mixed reasoning + implementation tasks

## Expected Response Format for Substantial Tasks
Return:
1. Summary
2. Files changed
3. Validation run
4. Risks / assumptions
5. Recommended next steps