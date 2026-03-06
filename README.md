# Caerus Quant — Daily Execution & Research System

**Owner:** Brett Olson
**Last reviewed:** March 2026
**Status:** Active production (paper trading via Alpaca)

---

## Project Overview

Caerus is a quantitative equity trading and research platform. It generates daily long-only stock selections via a technical trend-following strategy, executes paper trades through Alpaca's paper API, and maintains a full audit trail of every run. A secondary research sleeve (Sleeve 1) runs daily for backtesting purposes but does not drive live orders.

The system is operated via three GitHub Actions workflows and can also be run locally.

---

## What This System Does

1. **Generates daily signals** — EMA crossover + ADX filter applied to a configured universe of US equities, with a VIX-driven position-scaling overlay.
2. **Executes paper trades** — Orders are sent to Alpaca Paper at 9:35 AM ET on weekdays via the `daily-alpaca-paper` workflow.
3. **Reconciles model vs broker state** — Before any orders are placed, the canonical model snapshot is compared against actual Alpaca positions. Trades are blocked if drift is detected.
4. **Emails reports** — An execution summary email (HTML + text) is sent after each run. A separate alpha performance report is sent after the pre-market alpha workflow.
5. **Runs a nightly research digest** — A separate AI-powered research digest emails macro and market signals at 7:00 AM ET.
6. **Archives all artifacts** — Every run produces an immutable artifact bundle under `outputs/runs/<RUN_ID>/`.

---

## Current Trading / Research Objective

**Mode:** Alpaca Paper (not live money)
**Universe:** Long-only US equities (no options, no leverage, no short positions in production)
**Active sleeve:** `sleeve_trend` (EMA crossover + ADX + VIX regime scaling)
**Research sleeve:** `sleeve_1` (cross-sectional momentum — output currently discarded in production)

The system targets capturing intermediate-term equity trends with controlled drawdown via circuit breakers and VIX-based regime scaling.

---

## System Architecture

```
GitHub Actions
├── daily-alpaca-paper.yml     9:35 AM ET   Main execution workflow
├── alpha_daily.yml            6:15 AM ET   Pre-market alpha research + NAV update
└── research-digest.yml        7:00 AM ET   Nightly macro/market research digest

daily_quant_report.py          Main orchestrator (Python)
├── sleeves/sleeve_trend/      Active production strategy
│   ├── selection.py           select_and_weight() — scores and selects tickers
│   ├── indicators.py          EMA, ATR, ADX, volatility functions
│   ├── backtest.py            prepare_data() — OHLCV enrichment pipeline
│   ├── build_sleeve_output.py Bridge: signals → SleeveOutput for allocator
│   └── config.py              All tunable parameters
├── sleeves/sleeve_1/          Research-only (output discarded in production)
├── core/portfolio_alloc.py    PortfolioAllocator — combines sleeves, enforces caps
├── engine/breaker.py          Portfolio exposure overlay (FULL/PARTIAL/LOCK modes)
├── reconciliation.py          Pre-trade recon + canonical snapshot management
├── paper/paper_broker.py      Alpaca order execution layer
└── research/vix_regime.py     VIX fetcher and four-regime classifier
```

---

## Strategy Overview

The active strategy is a **technical trend-following system** on US equities.

**Entry criteria (all must pass):**
- Price above 200-day EMA (trend filter)
- EMA(20) > EMA(50) (short-term above medium-term)
- ADX ≥ 20 (market is trending, not ranging)
- Price ≥ $5 and average volume ≥ 100K shares/day (liquidity gates)

**Ranking:** Passing stocks are scored on a 0–100 composite across five factors (trend strength 30%, momentum 25%, ADX 25%, relative volume 10%, inverse-volatility bonus 10%).

**Position sizing:** Inverse-volatility weighting — lower-volatility names receive more capital. Volatility clipped to [5%, 80%] to prevent blow-ups.

**VIX regime overlay:** A four-tier volatility regime (LOW/ELEVATED/HIGH/CRISIS) automatically scales position count and gross exposure based on the current VIX reading.

**Risk limits:** Max 10% per position, max 2 positions per sector, 5% minimum cash buffer, drawdown circuit breakers at 10% (soft) and 15% (hard).

See [`docs/model_strategy.md`](docs/model_strategy.md) for full detail.

---

## Where the "Brains" Live

| Concern | File(s) |
|---|---|
| Entry gates & ticker scoring | `sleeves/sleeve_trend/selection.py` |
| Indicator math (EMA, ADX, ATR) | `sleeves/sleeve_trend/indicators.py` |
| All tunable strategy params | `sleeves/sleeve_trend/config.py` |
| Portfolio allocation & caps | `core/portfolio_alloc.py` |
| Exposure / drawdown breaker | `engine/breaker.py` |
| VIX regime detection | `research/vix_regime.py` |
| Main daily orchestrator | `daily_quant_report.py` |
| Pre-trade reconciliation | `reconciliation.py` |
| Alpaca order execution | `paper/paper_broker.py` |

---

## Daily Operating Workflows

### 1. Alpha Daily (`alpha_daily.yml`) — 6:15 AM ET
- Runs `scripts/alpha_report.py` to refresh alpha analysis from market data (25 bps cost assumption)
- Runs `scripts/daily_alpha_run.py` to update shadow NAV (`data/live_nav.csv`)
- Commits updated `live_nav.csv` back to the repo
- Emails alpha report (if `ENABLE_EMAIL != '0'`)
- Archives artifacts to `outputs/runs/<RUN_ID>/`

### 2. Daily Alpaca Paper (`daily-alpaca-paper.yml`) — 9:35 AM ET
1. Restores canonical model snapshot from GitHub Actions cache
2. Runs Alpaca connectivity smoke test and credential validation
3. Executes `daily_quant_report.py`:
   - Checks broker connectivity
   - Runs `sleeve_trend` signal generation
   - Applies VIX regime scaling
   - Runs `PortfolioAllocator`
   - Applies exposure/breaker overlay
   - **Pre-trade reconciliation**: compares canonical snapshot vs Alpaca positions → halts if drift detected
   - Sends paper orders to Alpaca
   - Writes execution email payload, run artifacts, canonical snapshot
4. If pre-trade recon fails and `AUTO_BOOTSTRAP_ON_RECON_FAIL=1`: auto-bootstraps canonical snapshot from broker, sends drift-alert email, marks run as recovered
5. Saves canonical snapshot back to cache
6. Uploads artifacts (run dir, canonical snapshot, execution email payload)
7. **Email job** (always runs): downloads artifacts, sends execution email + daily snapshot email

### 3. Research Digest (`research-digest.yml`) — 7:00 AM ET
- Runs `quant_research_agent/main.py` — AI-driven macro/market signal digest
- Uses Anthropic (Claude) and FRED APIs for signal scoring
- Maintains a dedup store (`quant_research_agent/store/seen_ids.json`) via cache to avoid re-emailing seen items
- Emails HTML digest; saves to `outputs/runs/<RUN_ID>/`
- Completely isolated from trading workflows — failures here do not affect execution

---

## Execution and Broker Integration

**Broker:** Alpaca Markets (paper endpoint: `https://paper-api.alpaca.markets`)
**Mode control:** `TRADING_MODE=alpaca`, `ALPACA_PAPER=1`
**Order type:** Market orders at open (next-day fill assumption)

**Required secrets (GitHub Actions):**
- `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `ALPACA_PAPER`
- `EMAIL_SENDER`, `EMAIL_APP_PASSWORD`, `EMAIL_RECIPIENT`
- `ANTHROPIC_API_KEY`, `FRED_API_KEY` (research digest only)

**Bootstrap (first run or after manual reset):**
```bash
# Via GitHub Actions workflow_dispatch:
# Set bootstrap_model_ledger_from_broker = true
# This writes the canonical snapshot from broker state and exits without orders.
```

---

## Reconciliation / Canonical State

The canonical model snapshot (`outputs/paper_state/canonical_positions.json`) is the authoritative record of what positions the model believes the broker holds.

**Pre-trade reconciliation** compares this snapshot against actual Alpaca positions before any orders are sent. If mismatch is detected (missing positions or quantity differences), trades are blocked and an alert is sent.

**Recovery paths:**
- `AUTO_BOOTSTRAP_ON_RECON_FAIL=1` — automatic recovery on next recon failure (opt-in)
- Manual: run `workflow_dispatch` with `bootstrap_model_ledger_from_broker=true`

**Canonical snapshot is persisted** between workflow runs via GitHub Actions cache (date-scoped key `canonical-model-snapshot-v2-<date>`).

See [`docs/OPERATIONS.md`](docs/OPERATIONS.md) for full reconciliation failure recovery procedures.

---

## Risk Controls and Safeguards

| Control | Mechanism | Location |
|---|---|---|
| VIX regime scaling | 4-tier position scale (100%/75%/50%/25%) + position count cap | `research/vix_regime.py`, `sleeves/sleeve_trend/config.py` |
| Drawdown circuit breaker (soft) | 10% DD → reduce all sizes by `BREAKER_PARTIAL_EXPOSURE` (default 50%) | `engine/breaker.py` |
| Drawdown circuit breaker (hard) | 15% DD → `LOCK` mode — no new entries | `engine/breaker.py` |
| Sector concentration cap | Max 2 positions per sector | `sleeves/sleeve_trend/selection.py` |
| Position size cap | Max 10% per position (`MAX_POSITION_PCT`) | `sleeves/sleeve_trend/config.py` |
| Gross exposure cap | Max 50% gross | `sleeves/sleeve_trend/config.py` |
| Pre-trade reconciliation | Block all trades on model/broker drift | `reconciliation.py` |
| IC monitor (signal health) | Alert when rolling 60d IC < 0.03 or IC < 0 for 20+ consecutive days | `research/ic_monitor.py` |
| Liquidity gates | Price ≥ $5, volume ≥ 100K avg shares | `sleeves/sleeve_trend/selection.py` |
| No leveraged/inverse ETFs | Universe exclusion rule | `core/universe_v4.py` |
| Minimum cash buffer | 5% always in cash (`MIN_CASH_PCT`) | `sleeves/sleeve_trend/config.py` |

---

## Artifacts and Output Directories

| Path | Contents |
|---|---|
| `outputs/runs/<RUN_ID>/` | Immutable per-run archive (reports, broker, ledger, snapshots, meta.json, checksums.sha256) |
| `outputs/latest.json` | Mutable pointer to most recent run |
| `outputs/paper_state/canonical_positions.json` | Canonical broker position snapshot |
| `outputs/paper_state/ledger2.csv` | Cumulative trade ledger |
| `outputs/paper_state/nav2.csv` | NAV time series |
| `outputs/execution_email/<DATE>.json` | Persisted execution email payload |
| `outputs/alpha_report/` | Daily alpha attribution outputs |
| `outputs/perf/nav_timeseries.csv` | Mark-to-market NAV with daily return + turnover |
| `outputs/ic_monitor/` | Daily IC log, 60d rolling IC, alerts JSON |
| `outputs/vix_regime/` | Current regime JSON + history CSV |
| `outputs/broker/recon_pretrade_<DATE>.json` | Pre-trade reconciliation report |
| `outputs/audit/` | Deterministic audit bundles (policy backtest, Monte Carlo) |
| `data/live_nav.csv` | Shadow NAV committed to repo by alpha_daily workflow |
| `reports/ai_runs/<RUN_ID>/` | AIOps spec-driven run artifacts |
| `signals/<DATE>.json` | Daily signal snapshot |

---

## AIOps / Governance Model

The `aiops/` module provides a CLI for spec-driven development and validation. Specs are markdown files in `specs/` that define a build contract; AIOps parses them, generates deterministic plans, and can dispatch Codex (or a fallback) to execute.

```bash
aiops parse specs/my_spec.md           # Validate spec headers
aiops plan  --spec specs/my_spec.md --mode BUILD   # Generate plan artifacts
aiops run-all --spec specs/my_spec.md --mode BUILD  # Full lifecycle
```

**Exit codes:** 0=OK, 2=needs_operator, 3=verify_failed, 4=parse_plan_failed, 5=dispatch_failed, 6=run_failed

See [`specs/aiops_system_contract_v0_1.md`](specs/aiops_system_contract_v0_1.md) and [`docs/aiops_workflow.md`](docs/aiops_workflow.md).

---

## Testing and Verification

```bash
# Full test suite
pytest -q

# AIOps contract tests only
pytest Tests/test_aiops_contracts.py -v

# Local green loop (smoke run without broker)
bash scripts/local_green_loop.sh

# Alpha report (local, no email)
python3 scripts/alpha_report.py --apply-costs --cost-bps 25

# Audit export (2022 + Monte Carlo worst window)
python3 scripts/run_audit_2022_and_worst.py
```

Test coverage includes: AIOps CLI contracts, allocation logic, breaker policy, canonical ledger health, attribution reporting, email coherency, reconciliation, alpha lab schemas, and drawdown/turnover constraints.

---

## How to Run the System

### Local setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Shadow mode (no broker)
```bash
REPORT_DATE=$(TZ=America/New_York date +%F) MODE=shadow TRADING_MODE=shadow python3 daily_quant_report.py
```

### Alpaca paper mode
```bash
REPORT_DATE=$(TZ=America/New_York date +%F) \
  MODE=alpaca TRADING_MODE=alpaca ALPACA_PAPER=1 \
  ALPACA_API_KEY_ID=... ALPACA_API_SECRET_KEY=... \
  ALPACA_BASE_URL=https://paper-api.alpaca.markets \
  python3 daily_quant_report.py
```

### Email controls
- `EMAIL_STRICT=0` (default) — SMTP failures are warnings, run continues
- `EMAIL_STRICT=1` — SMTP failures are fatal
- `EMAIL_DRY_RUN=1` — skips sending but still produces email artifacts

See [`README_PROD.md`](README_PROD.md) for quick-reference production commands.

---

## Known Limitations / Watch Items

- **Yahoo Finance dependency** — All market data (prices, VIX) fetched via `yfinance`. No fallback data provider. Network failures during CI degrade to VIX fallback (25.0 = ELEVATED regime) and may cause partial signal generation.
- **Sleeve 1 is research-only** — It runs daily but its output is explicitly discarded (`_, _ = run_sleeve_1()`). Decision to integrate or formally archive is open.
- **Sleeve 2 and Charlie Munger stubs** — Dead code remains in `daily_quant_report.py`. Should be removed.
- **No live money** — System is paper-trading only. Live brokerage execution is not wired.
- **macOS venv** — The local `.venv` Python binary is macOS-only. Use `source .venv/bin/activate` on Mac; CI creates its own venv on Linux.
- **Walk-forward validation** — Extended WFO run (2015–present, ~36 windows) is backlog; short 4-window result shows OOS Sharpe elevated due to bull-market artifact.
- **Transaction cost model** — Fixed at ~25 bps in alpha_report runs. Actual costs vary by order size and liquidity.
- **Short selling** — Config defines short parameters but no short positions are placed in production.

---

## Related Documentation

| Document | Purpose |
|---|---|
| [`docs/model_strategy.md`](docs/model_strategy.md) | Investment strategy, alpha hypotheses, portfolio construction |
| [`docs/runbook.md`](docs/runbook.md) | Day-to-day operator guide, checklists, failure recovery |
| [`CHANGELOG.md`](CHANGELOG.md) | Material change history |
| [`docs/MODEL_AUDIT.md`](docs/MODEL_AUDIT.md) | Deep-dive: signal generation, weighting, backtest harness |
| [`docs/MODEL_CHANGES.md`](docs/MODEL_CHANGES.md) | Engineering change log (commit-level) |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Reconciliation failure recovery, auto-bootstrap procedures |
| [`docs/run_archiving.md`](docs/run_archiving.md) | Run artifact structure and integrity verification |
| [`docs/audit.md`](docs/audit.md) | Audit export, policy backtest, Monte Carlo workflows |
| [`docs/performance_reporting.md`](docs/performance_reporting.md) | Ledger schema, NAV computation, attribution artifacts |
| [`docs/aiops_workflow.md`](docs/aiops_workflow.md) | AIOps CLI commands, lifecycle, troubleshooting |
| [`specs/aiops_system_contract_v0_1.md`](specs/aiops_system_contract_v0_1.md) | AIOps CLI contracts and exit codes |
| [`ARCHIVING.md`](ARCHIVING.md) | Archiving policy for legacy code and sleeves |
| [`README_PROD.md`](README_PROD.md) | Quick-reference production commands |
