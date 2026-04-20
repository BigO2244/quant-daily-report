# AGENTS.md

Single agent-facing handoff for this repository. Operational, architecture,
scheduler, and workflow guidance lives here.

Last updated: 2026-04-20 — options promoted to paper execution, directional
contract sizing, ALLOW_OPTIONS_EXECUTION default enabled, overnight agents
moved to 1 AM ET, test suite clean at 955.

---

## System Snapshot

- **Project**: Caerus Quant / Alpha Stack quantitative trading platform
- **Scope**: US long-only equities + options overlay, paper trading through Alpaca
- **Production posture**: paper only, no shorting, no leverage
- **Promotion ladder**: research → backtest → shadow → paper → live
- **Test suite**: 955 passing, 0 failing (as of 2026-04-20)
- **Hard rule**: do not change production trading behavior casually; bias toward
  safety, deterministic artifacts, and explicit verification

---

## Current Operating Environments

- **Local development**
  - host: `brettolson@BDO-Macbook` (Mac Studio, arm64)
  - use for coding, tests, diagnostics, dashboard generation, artifact review
  - venv: `.venv/` at repo root
- **Scheduler VM**
  - host: `brettolson@34.61.147.38`
  - path: `~/quant-daily-report`
  - venv: `source venv/bin/activate`
  - secrets: `~/quant-daily-report/.env`
- **Cron install**: `crontab scripts/crontab.txt`

---

## Scheduled Automation — Full Pipeline

Five phases run on the VM weekdays. Install with `crontab scripts/crontab.txt`.

| Time (ET) | Phase | Script | Output |
|---|---|---|---|
| 1:00 AM | 0a — Overnight agents | `scripts/cron_overnight.sh` | `outputs/overnight_signals/YYYY-MM-DD.json` |
| 6:30 AM | 0b — Claude research digest | `scripts/cron_research.sh` | `quant_research_agent/outputs/digest_YYYY-MM-DD.json` |
| 7:00 AM | 1 — Precompute | `scripts/cron_precompute.sh` | `outputs/precompute/YYYY-MM-DD/` bundle |
| 9:35 AM | 2 — Order execution | `scripts/cron_execute.sh` | Alpaca paper equity orders + gated protective-put options |
| 10:00 AM | 3 — Confirmation + email | `scripts/cron_confirm.sh` | Email report |
| Monday 8 AM | Weekly model review | `scripts/cron_weekly_review.sh` | Review artifacts |

**Data flow**: Phase 0a runs overnight agents → Phase 0b runs Claude to score
news/arxiv/earnings → Phase 1 precompute consumes both (via thematic overlay) →
Phase 2 executes the precomputed plan → Phase 3 confirms and emails.

Overnight signals are accepted up to 3 days old; research digest up to 3 days
old. Non-fatal failures in 0a/0b do not block Phase 1.

---

## Architecture

### Main Orchestrator

`daily_quant_report.py` — Phase 1 entry point. Runs regime classification,
sleeve scoring, allocation, reconciliation, and writes the precompute bundle.

### Alpha Stack (alpha_stack/)

Regime-switching multi-sleeve equity engine. Four components:

**Regime Engine** (`alpha_stack/regime/`)
- Classifies market in 4 independent dimensions: trend, volatility, breadth, macro
- Each dimension has its own `HysteresisController` — 5-day min dwell, 2-close
  confirmation, max 1-state jump per transition, crisis-bypass for VIX spikes
- Trend: SPY `(close/EMA200)-1` and `(EMA50/EMA200)-1`
- Volatility: VIX — calm <16, normal 16-22, elevated 22-30, crisis >30
- Breadth: % of 201 universe members above their 200-DMA
- Macro: TLT + HYG proxies (approximation; marked TODO for proper data source)

**Sleeve Allocator** (`alpha_stack/portfolio/`)
- Regime-determined base budgets (e.g. strong_up → 70% trend / 25% quality)
- Layered modifiers: vol crisis cuts trend 30%; breadth washed_out blocks new entries
- Hard constraints: 95% max gross, 20% per-name, 40% per-sector
- Turnover smoothing: 15% max sleeve budget shift per day

**Active Sleeves**:

| Sleeve | Status | Signal |
|---|---|---|
| Trend | **Active** | 12-1, 6-1, 3-1 momentum z-scores + ATR vol adjustment + thematic boost |
| Quality | **Active** | ROE, ROIC, net leverage, margin vol, accruals (yfinance; not PIT-safe) |
| Mean Reversion | Gated off | Needs shadow validation before promotion |
| Value | Gated off | Needs PIT-safe fundamentals (filed-date aware source) |

**Trend Sleeve Scoring Pipeline** (`alpha_stack/sleeves/trend.py`):
1. Eligibility gates: price ≥ $5, ADV ≥ 100K shares, r12_1 must exist
2. Signal: `S_raw = 0.45×z(r12_1) + 0.30×z(r6_1) + 0.15×z(r3_1) + 0.10×trend_flag`
3. Vol adjust: `S_adj = S_raw / max(ATR_20d_pct, 1%)`
4. Thematic boost (before rank): `S_final = S_adj + 0.15 × thematic_boost`
5. Percentile rank → [0, 100] across 201 tickers
6. Entry: score ≥ 65 AND EMA50 > EMA200; Hold ≥ 50; Exit < 40
7. Select top-10; hard cap at 12 positions; 20% per-name max
8. Inverse-vol weights: `w_i ∝ 1 / clip(vol_20d, 10%, 60%)`

**Feature Flags** (`alpha_stack/config/alpha_stack.yaml`):

| Flag | State | Notes |
|---|---|---|
| `ENABLE_ALPHA_STACK` | `true` | Master switch |
| `ENABLE_ALPHA_STACK_SHADOW` | `true` | Shadow NAV tracking |
| `ENABLE_QUALITY_SLEEVE` | `true` | Active for paper |
| `ENABLE_OPTIONS_OVERLAY` | `true` | Enabled 2026-04-17 |
| `ENABLE_MEAN_REVERSION` | `false` | Gated — shadow validation pending |
| `ENABLE_VALUE_SLEEVE` | `false` | Gated — needs PIT-safe fundamentals |

### Overnight Research Agents (`overnight_agents/`)

Seven agents run nightly at 8 PM ET via `python -m overnight_agents.orchestrator`.
Output: `outputs/overnight_signals/YYYY-MM-DD.json`.

| Agent | Data Source | Signal |
|---|---|---|
| `liquidity` | FRED: WALCL, RRPONTSYD, WTREGEN, WRESBAL | Expanding / contracting Fed liquidity |
| `gamma_regime` | SPY options chain (yfinance) + Black-Scholes GEX | Positive / negative / neutral gamma |
| `etf_flows` | 11 sector ETFs + 4 broad ETFs (yfinance) | Sector rotation pressure |
| `earnings_revision` | yfinance analyst recs + EPS estimates, 20 tickers | Positive / negative revision |
| `commodity` | FRED: WTI, nat gas; yfinance: GLD, BDRY, DBA | Inflationary / deflationary pressure |
| `insider_activity` | SEC EDGAR Form 4 XML (data.sec.gov/submissions API) | Cluster buys / sells, 21-day window |
| `sentiment` | AAII survey XLS + research digest news tone | Contrarian sentiment signal |

Orchestrator: `overnight_agents/orchestrator.py`
- `run_all(as_of_date, agent_filter, dry_run)` — runs agents, writes JSON
- `load_latest(as_of, max_age_days=3)` — consumed by thematic overlay
- CLI: `python -m overnight_agents.orchestrator --agents gamma liquidity --dry-run`

### Thematic Overlay (`alpha_stack/research_signal/thematic_overlay.py`)

Bridges research signals into trend sleeve scoring. Merges two sources:
- **Source 1**: Claude research digest (`quant_research_agent/outputs/digest_*.json`)
  — max score per ticker across all items; bearish items get 50% haircut
- **Source 2**: Overnight agent signals
  — insider cluster buys → +0.75; gamma negative → +0.10 uniform; liquidity contracting → -0.10

Combined boost applied **before** percentile rank in trend sleeve (weight: 0.15).
Files accepted up to 3 days old.

### Claude Research Agent (`quant_research_agent/`)

Runs at 6:30 AM ET via `python -m quant_research_agent.main`.
Ingests: arxiv papers, earnings releases, macro data, news headlines.
Scores each item against 5 themes and 14-ticker watchlist using Claude.
Items scoring ≥ 0.40 enter the digest JSON consumed by thematic overlay.

Themes: `ai_energy_nexus`, `ai_infrastructure`, `defense_supercycle`,
`earnings_catalyst`, `macro_regime`.

Watchlist: AVGO, NVDA, MSFT, VST, TLN, MU, META, GOOGL, AMZN, LMT, NOC, GD, SPY, QQQ.

Config: `quant_research_agent/config/strategy_context.yaml`

### Options Overlay (`core/options_overlay_shadow.py`, `core/options_execution.py`)

**Status: Paper execution active as of 2026-04-20. Mode: `paper` (was `shadow_only`).**

Six strategy types, each regime-gated:

| Strategy | Regime | Purpose |
|---|---|---|
| Covered call | Risk-on trending, normal/elevated VIX | Harvest premium on held positions |
| LEAP call | Risk-on or neutral, healthy/mixed breadth | Capital-efficient long exposure |
| Protective put | Crisis VIX, breadth washed out | Directional downside bet |
| Put spread | Risk-off, deteriorating breadth | Defined-risk directional bet |
| Call butterfly | Neutral, elevated VIX | Low-cost directional bet |
| Long straddle | Crisis/elevated VIX, washed out | Volatility play |

**Contract Sizing** (`config/options_overlay_policy.json`):
- Feasibility gate: `premium_budget_dollars >= min_contract_premium` ($50 floor).
  Replaces the old portfolio-coverage check that required $38K+ to pass.
- Directional sizing (protective_put + put_spread): `contracts = min(max_contracts, floor(budget / per_contract_cost_estimate))`.
  Scales with conviction rather than treating puts purely as a hedge.
- Current policy for protective_put: 500bps budget (~$486 on $9.7K), $150/contract estimate, max 5 → **3 contracts** in crisis.
- Current policy for put_spread: 200bps budget, $75/contract estimate, max 3 → up to 3 contracts.
- Accounts below $1K (budget < $50 floor) remain `WATCH_ONLY_CONTRACT_TOO_LARGE`.

Execution config: `config/options_execution_policy.json`
- `allow_live_submission: true` — policy permits submission only after runtime gates pass
- Runtime gates: `ALLOW_OPTIONS_EXECUTION=1` (default enabled in `cron_execute.sh`),
  `WORKFLOW_KIND=live`, not plan-only, `ENABLE_OPTIONS_OVERLAY=true`, Alpaca paper endpoint
- `allowed_strategies: ["protective_put"]` — enforced allowlist; only protective
  puts execute live, others generate shadow/review artifacts only
- `require_paper_ready: true` and `require_allocator_ready: true` remain as gates
- Override to disable: set `ALLOW_OPTIONS_EXECUTION=0` in `.env`

Execution path: `scripts/execute_options_overlay.py` →
`core/options_execution.py` → `brokers/alpaca_broker.py:submit_option_market_order()`
OCC symbol construction: `build_option_symbol(underlying, expiry, option_type, strike)`

**Note**: Only `protective_put` has live execution implemented. Other strategies
are blocked by the execution allowlist; if later allowlisted before implementation,
they must still fail closed until their execution paths and tests are wired.

### Universe (`data/universe.csv`)

201 US large/mid-cap equities across all S&P 500 sectors. Pure price-based
scoring — no fundamental data required for trend sleeve (PIT-safe by design).
Benchmark: SPY. Cash proxy: SGOV.

### Broker Integration (`brokers/alpaca_broker.py`)

- `submit_market_order()` — equity orders
- `submit_option_market_order()` — options market orders (line 589)
- `submit_option_limit_order()` — options limit orders (line 611)

All cron scripts force `ALPACA_PAPER=1` and `ALPACA_BASE_URL=paper-api.alpaca.markets`.

### Anti-Churn Mechanisms

Four independent layers prevent position and regime whipsaw:

1. **Regime hysteresis** (`alpha_stack/regime/hysteresis.py`): 5-day min dwell,
   2-close confirmation, max 1-state jump, crisis bypass for VIX spikes
2. **Sleeve smoothing** (`alpha_stack.yaml`): max 15% sleeve budget shift per day
3. **Score band**: Enter ≥ 65, Hold ≥ 50, Exit < 40 — 25-point gap keeps
   existing positions sticky against noise
4. **Top-N with hold preference**: existing positions held while score ≥ hold
   threshold, preventing rank-10/rank-11 daily flip

### Drawdown Circuit Breakers

- Soft (12% DD): all sleeves reduced 50%
- Hard (20% DD): cash-only, no new positions

---

## Key File Map

| File | Purpose |
|---|---|
| `daily_quant_report.py` | Main orchestrator — Phase 1 entry point |
| `alpha_stack/config/alpha_stack.yaml` | Master config — feature flags, regime thresholds, sleeve params |
| `alpha_stack/regime/hysteresis.py` | Anti-whipsaw hysteresis controller |
| `alpha_stack/sleeves/trend.py` | Trend/momentum sleeve — primary alpha engine |
| `alpha_stack/sleeves/quality.py` | Quality sleeve — ROE/ROIC/leverage |
| `alpha_stack/research_signal/thematic_overlay.py` | Merges Claude digest + overnight signals into score boost |
| `overnight_agents/orchestrator.py` | Runs all 7 overnight research agents |
| `overnight_agents/base.py` | BaseAgent ABC — error handling, neutral stub |
| `quant_research_agent/main.py` | Claude research digest producer |
| `quant_research_agent/config/strategy_context.yaml` | Themes, watchlist, scoring calibration |
| `core/options_overlay_shadow.py` | Options strategy selection + shadow artifacts |
| `core/options_execution.py` | Options live execution — OCC symbol, Alpaca submission |
| `config/options_execution_policy.json` | Options execution gates — `allow_live_submission` flag |
| `brokers/alpaca_broker.py` | Alpaca broker — equity + options order submission |
| `scripts/crontab.txt` | Full cron schedule — install with `crontab scripts/crontab.txt` |
| `scripts/cron_overnight.sh` | Phase 0a — runs overnight agents at 8 PM ET |
| `scripts/cron_research.sh` | Phase 0b — runs Claude research agent at 6:30 AM ET |
| `scripts/cron_precompute.sh` | Phase 1 — 7:00 AM ET precompute |
| `scripts/cron_execute.sh` | Phase 2 — 9:35 AM ET order execution |
| `scripts/cron_confirm.sh` | Phase 3 — 10:00 AM ET confirmation + email |
| `trading_audit.py` | Holding period, slippage, turnover utilities |
| `reconciliation.py` | Broker-authoritative pre/post-trade reconciliation |
| `data/universe.csv` | 201-ticker trading universe |

---

## Deployment and Verification Rules

- Local commits do not deploy to the VM.
- Prefer local development first, then explicit SCP deploy.
- After SCP, verify remote content with `md5sum` or `grep`. Never assume SCP succeeded.
- Do not edit the VM directly unless an explicit hotfix.
- After deploying, install the updated cron: `crontab scripts/crontab.txt`

For scheduler incidents, inspect:
- `outputs/latest_run.json`
- `logs/execute_<date>.log`
- `logs/overnight_<date>.log`
- `logs/research_<date>.log`
- `outputs/broker/recon_pretrade_<date>.json`
- `outputs/precompute/<date>/contract.json`
- `outputs/overnight_signals/<date>.json`

---

## High-Risk Areas

Changes in these areas require explicit caution and validation:

- Reconciliation and broker state repair paths (`reconciliation.py`)
- Paper execution and order-submission flow (`core/options_execution.py`, `brokers/alpaca_broker.py`)
- Canonical state under `outputs/paper_state/`
- Main orchestrator (`daily_quant_report.py`)
- Thematic overlay and scoring pipeline (`alpha_stack/sleeves/trend.py`)
- Overnight agent orchestrator (`overnight_agents/orchestrator.py`)
- Artifact schemas and JSON/CSV output contracts
- Cron schedules (`scripts/crontab.txt`)

---

## Phase Implementation Status

| Phase | Description | Status | Date |
|---|---|---|---|
| Phase 1 | Live regime-aware multi-sleeve allocator | **Active** | 2026-04-09 |
| Phase 2A | SPY options overlay — shadow + paper execution | **Active** | 2026-04-09 |
| Phase 3A | Defensive ETF sleeve (SGOV, SHY, IEF, TLT) | **Active** | 2026-04-09 |
| Alpha Stack | Concentrated trend + quality (10-12 positions, 20% cap) | **Active** | 2026-04-17 |
| Overnight Agents | 7-agent nightly research pipeline | **Active** | 2026-04-17 |
| Claude Research | Nightly digest → thematic overlay → trend scoring | **Active** | 2026-04-17 |
| Options Execution | Protective put live submission via Alpaca | **Active** | 2026-04-17 |
| Mean Reversion Sleeve | RSI, Bollinger, volume shock | Gated — shadow pending | — |
| Value Sleeve | ROE, FCF yield, earnings yield | Gated — PIT fundamentals needed | — |

---

## Open Items / Future Work

- **Macro dimension**: TLT/HYG proxies are approximations. Wire a proper macro
  data source (FRED series, credit spread feed) for full 4D regime classification.
- **Regime → overnight signal bridge**: Overnight agent `liquidity_state` and
  `gamma_state` currently only feed the thematic overlay. Future: wire directly
  into the regime allocator weights as a 5th/6th dimension.
- **Universe expansion**: Universe is 201 tickers. Can expand to full S&P 500
  (~350-400 mid-cap additions) by appending to `data/universe.csv`. Bond ETFs
  (TLT, IEF, HYG) and sector ETFs are additive — no code change required.
- **Options strategy expansion**: Only `protective_put` has live execution wired.
  `put_spread`, `covered_call`, `leap_call` need execution paths in
  `core/options_execution.py` before they can be promoted from shadow to live.
- **Value sleeve**: Needs a PIT-safe fundamental data source with filed-date
  awareness (Compustat, Polygon fundamentals, or Tiingo).
- **Mean reversion**: Ready to enable via `ENABLE_MEAN_REVERSION: true` once
  shadow validation is complete. Only activates in weak_up/neutral + calm/normal VIX.

---

## Agent Working Rules

- Inspect the current implementation before making broad changes.
- Prefer minimal, surgical edits unless the task explicitly calls for restructuring.
- If you touch execution, reconciliation, or reporting contracts, run the
  narrowest relevant validation first, then a broader check.
- Report exact commands run and whether they passed.
- Keep research work separate from production behavior unless the task is an
  explicit promotion.
- All cron scripts source `.env` and `scripts/runtime_env.sh` for venv activation.
  Replicate this in any new script.
- The overnight agents accept a `--dry-run` flag for local testing without
  writing output files. Always dry-run first when modifying agent logic.

---

## Ops Handoff

- Scheduler host path: `~/quant-daily-report`
- Cron source: `scripts/crontab.txt` — install with `crontab scripts/crontab.txt`
- After SCP: run `python -m pytest Tests/ -q --tb=no` on the VM to confirm clean suite
- The VM cron is the production scheduler for precompute/live execution; GitHub
  daily precompute/live schedules are dispatch-only to avoid duplicate runs
- If a `SELF_HEAL` pretrade reconciliation occurs, the wrapper re-runs reconciliation
  once against the refreshed canonical state before proceeding
- Same-day retry locks block duplicate successful executions but must not strand
  genuine recovery reruns after pre-execution failure

---

## Auto-Generated Nightly Findings

<!-- BEGIN AUTO-GENERATED: NIGHTLY FINDINGS -->
- Last refresh: `2026-04-15T16:59:14+00:00`
- Source: `reports/agents/nightly_findings.json`
- Findings generated at: `2026-04-15T16:59:11+00:00`
- Broker state partially confirmed
- Trade date: 2026-04-13.
- Broker trust level: MEDIUM.
- Pretrade status: UNKNOWN; posttrade reconciliation: UNKNOWN.
- GitHub workflows are dispatch-only; scheduled production work runs from the GCP VM cron.
- Pretrade broker snapshot was not confirmed in the latest available artifacts.
<!-- END AUTO-GENERATED: NIGHTLY FINDINGS -->

## Auto-Generated Workflow Inventory

<!-- BEGIN AUTO-GENERATED: WORKFLOW INVENTORY -->
- Materialized workflow files in this checkout:
- `_archived_backtest_sleeve1.yml`: Run Backtest (Sleeve 1) | triggers=workflow_dispatch | cron=none
- `_archived_backtest_sleeve1_robustness.yml`: Sleeve1 Robustness Backtest | triggers=unknown | cron=none
- `_archived_backtest_sleeve2.yml`: Run Backtest (Sleeve 2) | triggers=workflow_dispatch | cron=none
- `alpha_daily.yml`: Alpha Daily Run | triggers=workflow_dispatch | cron=none
- `daily-alpaca-live.yml`: Daily Alpaca Live | triggers=workflow_dispatch | cron=none
- `daily-alpaca-paper.yml`: Daily Alpaca Paper Run (Deprecated Wrapper) | triggers=workflow_dispatch | cron=none
- `daily-alpaca-precompute.yml`: Daily Alpaca Precompute | triggers=workflow_dispatch | cron=none
- `export-broker-snapshot.yml`: Export Alpaca Broker Snapshot | triggers=workflow_dispatch | cron=none
- `nightly-agents-refresh.yml`: Nightly Agents Refresh | triggers=workflow_dispatch | cron=none
- `research-digest.yml`: Research Digest — Nightly | triggers=workflow_dispatch | cron=none
<!-- END AUTO-GENERATED: WORKFLOW INVENTORY -->

---

## Historical References

- `specs/phase_5_broker_pretrade_snapshot.md`
- `specs/broker_authoritative_execution_model.md`
- `CHANGELOG.md`
- `docs/Alpha_Stack_Architecture_Reference.md`
- `docs/alpha_stack/sleeve_specifications.md`
- `docs/alpha_stack/regime_allocator_spec.md`
