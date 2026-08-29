# AGENTS.md

## AI Orchestration Instructions

Before beginning any Caerus task, read
`docs/governance/ORCHESTRATOR_CONTEXT.md`. Treat it as the durable operating
context for ChatGPT-as-orchestrator and Codex-as-implementation-agent work.
The strategic escalation policy lives at
`docs/governance/STRATEGIC_ESCALATION_POLICY.md`.

Caerus is Brett's paper-traded quantitative investment operating system. The
mission is to build a disciplined, evidence-driven, long-only alpha stack with
deterministic artifacts, explicit governance, and a promotion ladder from
research to shadow to paper to live. The strategic doctrine for strategy,
sleeve, promotion, retirement, and portfolio-construction decisions is
`docs/governance/caerus_investment_doctrine.md`.

Safety boundaries:
- Do not mutate trading, execution, allocation, broker, strategy, cron, or
  production runtime behavior unless the task explicitly authorizes it.
- Do not weaken execution reconciliation, asset validation, cash gates,
  freshness gates, look-ahead controls, or fail-closed behavior.
- Do not promote, retire, rename, or reweight strategies or sleeves without
  explicit Brett approval.
- Documentation, diagnostics, and tests must remain clearly separate from
  production trading behavior unless a task explicitly says otherwise.

## VM Access Policy

- Do not assume a static external IP address for the scheduler VM.
- Canonical direct SSH alias:
  `ssh caerus-vm`
- External IPs may change after stop/start, billing suspension, maintenance, or
  VM recreation.
- Treat direct SSH by external IP as ephemeral and non-authoritative.
- Do not use `gcloud compute ssh` in Codex tasks when the `caerus-vm` SSH alias
  is available.
- Canonical non-interactive VM validation:
  `ssh caerus-vm 'cd ~/quant-daily-report && ./scripts/ops/run_vm_validation.sh'`

Required pre-work before editing:
- Run `python3 scripts/build_operating_truth.py --strict` before any fund-level,
  capital, readiness, lane, or strategy-state assessment. Resolve every
  `CONTEXT_CONFLICT` before recommending action.
- Read `docs/governance/ORCHESTRATOR_CONTEXT.md`.
- Read the files named by the task and any immediately referenced governance
  documents.
- Check `git status --short` and preserve unrelated user changes.
- Identify whether the task is operational, strategic, or both. Escalate
  strategic ambiguity before implementing behavior changes.

Required validation before final response:
- For documentation-only work: run `git diff --check` and relevant grep/link
  sanity checks.
- For Python/runtime work: run targeted pytest for touched behavior, relevant
  `py_compile`, and any task-specific validation.
- For execution-adjacent changes: run execution integrity, lifecycle/timeline,
  MCP/status, and focused fixture tests unless the task explicitly narrows
  validation.

Git hygiene:
- Keep commits scoped to the task.
- Do not stage unrelated modified files.
- Prefer git revert over ad hoc rollback for deployed changes.
- Do not push, deploy, or fast-forward the VM unless the user explicitly asks.

> SOURCE OF TRUTH (added 2026-06-08): Before creating or editing any strategy,
> FR, or governance document, read `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
> and `config/research/strategy_registry.json`. That roadmap holds the canonical
> FR table, strategy state table, taxonomy decisions (Cassiopeia/Argo resolved 2026-06-08 per Option A; Cygnus open),
> and current blockers. Do not create a parallel "design" spec for a strategy that
> already has a canonical FR spec, and do not reassign strategy IDs or change
> execution/broker/cron behavior as part of documentation work. The strategy state
> below this banner predates the FR-050..FR-053 research wave and is retained as
> history — defer to the canonical roadmap where they differ.
>
> Canonical doctrine: `docs/governance/caerus_investment_doctrine.md` is the
> strategic doctrine for strategy, sleeve, promotion, and portfolio-construction
> work. Defer to it unless an explicit amendment is recorded.

CURRENT STRATEGY STATE

Paper (Active):
- Caerus Orion (owner-approved PAPER authority as of 2026-08-08)

Live (Active, Separate):
- Caerus Lyra (owner-approved 2026-08-19; funded Tuesday recurring portfolio)

Shadow (Daily, Non-Blocking):
- Caerus Polaris (historical research control)
- Caerus Lyra (Challenger)

Benchmark:
- SPY

Execution:
- Precompute admits one immutable session, produces one terminal decision for
  every registered non-frozen sleeve, and allocates configured capital sleeves
  exactly once; Orion currently has 100% of the sleeve risk budget
- The legacy daily allocator is research evidence only and is quarantined below
  the dated precompute bundle; it cannot publish canonical PAPER signals/trades
- The 09:35 builder reuses the sealed session, decisions, allocation, Evidence,
  and Decision packages; Risk may constrain but may not invent alpha
- Trader consumes only the hash-verified approved execution package
- Broker fills preserve exact-order and sleeve-decision provenance through the
  causal ownership ledger, valuation, and read-only daily audit
- Lyra Live is active, funded, recurring, and separately governed.
- Orion is the active PAPER capital sleeve.
- The legacy FR-104/generic Live lanes remain disabled; Orion's PAPER promotion
  does not arm either legacy lane or modify Lyra Live authority.

Automation:
- Shadow runs automatically after precompute via:
  scripts/run_shadow_candidates_daily.sh


## System Overview
Single agent-facing handoff for this repository. Operational, architecture,
scheduler, and workflow guidance lives here.

Last updated: 2026-05-16 — Waves 1-3 are deployed under deterministic
git-based VM deployment. Current operations include rollback-first deployment
governance, shadow orchestration observability, repository-scoped CI caches, and
fail-closed self-heal recovery integrity validation. Phase 4 is planned as a
non-trading Artifact Governance + Operational Telemetry backlog phase.

---

## System Snapshot

- **Project**: Caerus Quant / Alpha Stack quantitative trading platform
- **Scope**: US long-only equities + options overlay, paper trading through Alpaca
- **Production posture**: Lyra Live plus Orion PAPER; long-only, no leverage
- **Promotion ladder**: research → backtest → shadow → paper → live
- **Test suite**: 955 passing, 0 failing (as of 2026-04-20)
- **Hard rule**: do not change production trading behavior casually; bias toward
  safety, deterministic artifacts, and explicit verification

## Named Strategy Framework

- **Caerus Polaris** (`caerus_polaris`)
  - preserved historical research baseline / comparison control
  - continues daily shadow evidence; no PAPER execution authority
- **Caerus Orion** (`caerus_orion`)
  - current PAPER execution authority, owner-approved 2026-08-08
  - derived from Alpha Lab v2 winner: H2 rank-decay exit + H6 top-5 concentration
  - exact same-day shadow snapshot is wrapped into immutable Decision/Risk/Execution packages
  - 5% target cash and 2% target-attainment tolerance; live remains disabled
- **Caerus Lyra** (`caerus_lyra`)
  - active, separately governed Live portfolio and secondary Shadow challenger
  - derived from Alpha Lab v2 challenger: H1 weekly rebalance + H6 top-5 concentration
  - not promoted to PAPER; Live authority is an independent owner decision
- **SPY** (`spy_benchmark`)
  - benchmark symbol and comparison anchor
  - remains `SPY` in code and artifacts

---

## Current Operating Environments

- **Local development**
  - host: `brettolson@BDO-Macbook` (Mac Studio, arm64)
  - use for coding, tests, diagnostics, dashboard generation, artifact review
  - venv: `.venv/` at repo root
- **Scheduler VM**
  - canonical host: `alpha-stack-scheduler`
  - canonical SSH alias: `caerus-vm`
  - canonical project: `alpha-stack-490922`
  - canonical zone: `us-central1-a`
  - canonical access for Codex tasks: `ssh caerus-vm`
  - direct external IPs are ephemeral and non-authoritative
  - do not use `gcloud compute ssh` when the `caerus-vm` alias is available
  - path: `~/quant-daily-report`
  - validation venv:
    `/home/brettolson/.venvs/quant-daily-report/bin/python`
    and `/home/brettolson/.venvs/quant-daily-report/bin/pytest`
  - canonical validation:
    `ssh caerus-vm 'cd ~/quant-daily-report && ./scripts/ops/run_vm_validation.sh'`
  - secrets: `~/quant-daily-report/.env`
  - web routes:
    - `/` → landing page with links to dashboard and golf bot
    - `/dashboard/` → primary protected dashboard
    - `/dashboardDEV/` → protected dev/prototype dashboard
    - `/golf/` → golf bot (must remain isolated; do not break or overwrite)
- **Cron install**: `crontab scripts/crontab.txt`

---

## Workflow Authority Inventory

Before declaring a Caerus workflow present, absent, active, or blocked, inspect
all four authorities in `docs/governance/WORKFLOW_AUTHORITY_REGISTRY.md`:

1. Codex automations under `$CODEX_HOME/automations/`;
2. the nearest workspace and project `AGENTS.md` plus referenced operating
   documents;
3. deployed VM cron, runtime state, and current artifacts; and
4. GitHub branches and workflows.

Repository cron is not the sole automation authority. In particular, the
owner-approved Alpha Lab weekend research cycle is an external Codex automation
running Sunday from 00:05 through 05:00 America/New_York. Its versioned
research contract lives on the canonical `project/alpha-lab` branch. Do not
duplicate it as a GitHub Action or production VM cron job.

---

## Scheduled Automation — Full Pipeline

The following production operations run on the VM. Install with
`crontab scripts/crontab.txt`.

| Time (ET) | Phase | Script | Output |
|---|---|---|---|
| 6:30 AM | Advisory research digest | `scripts/cron_research.sh` | `quant_research_agent/outputs/digest_YYYY-MM-DD.json`; non-capital and not consumed by canonical precompute |
| 7:00 AM | 1 — Precompute | `scripts/cron_precompute.sh` | Immutable session + full sleeve decisions + one account allocation |
| 9:35 AM | 2 — Order execution | `scripts/cron_execute.sh` | Exact Alpaca PAPER equity orders; options disabled |
| 10:00 AM | 3 — Confirmation + email | `scripts/cron_confirm.sh` | Email report |
| 6:30 PM | Post-close price hydration | `python3 -m scripts.hydrate_price_cache_only --refresh-shadow-artifacts --strict` | `outputs/price_hydration/YYYY-MM-DD/status.json` + refreshed Shadow scorecard artifacts |
| 7:15 PM | Broker-truth ledger | `scripts/cron_broker_ledger.sh` | Broker fills/positions plus causal ownership and single-as-of valuation |
| 7:45 PM | Canonical portfolio history and audit | strict history + `scripts/build_daily_portfolio_audit.py` | Same-as-of reporting snapshot and `outputs/audit/<date>/portfolio_audit.json` |
| 9:00 PM | Shadow CIO report | `python3 -m scripts.send_shadow_cio_report` | Daily Shadow scorecard/reporting email |
| Monday 8 AM | Weekly model review | `scripts/cron_weekly_review.sh` | Review artifacts |

**Data flow**: inputs → immutable session → complete sleeve decision batch →
configured account allocation → independent Risk → exact execution → broker
reconciliation → causal ownership → valuation → daily audit/reporting. The
post-precompute shadow refresh is research-only and cannot mutate the seal.

There is no deployed 1:00 AM overnight-agent job. The 6:30 AM digest is an
advisory research artifact and has no active consumer in canonical precompute;
its success or failure cannot change the session, sleeve decisions, allocation,
or execution plan.

Shadow generation is best-effort only:
- wrapper: `scripts/run_shadow_candidates_daily.sh`
- invoked from: `scripts/cron_precompute.sh`
- outputs: `outputs/shadow_candidates/YYYY-MM-DD/` and `outputs/shadow_candidates/performance/`
- status artifacts:
  - `outputs/workflow/YYYY-MM-DD/shadow_generate.json`
  - `outputs/workflow/YYYY-MM-DD/shadow_latest.json`
  - `outputs/workflow/YYYY-MM-DD/shadow_reconciliation.json`
  - `outputs/workflow/YYYY-MM-DD/shadow.json`
- failures are logged to `logs/shadow_YYYY-MM-DD.log`; stale/unavailable input
  publishes no new `latest` and makes system health non-green
- shadow cannot block production execution

Self-heal execution recovery is fail-closed:
- `scripts/cron_execute.sh` validates the full precompute bundle before
  execution continuation.
- If the bundle is missing or invalid, execution invokes
  `scripts/cron_precompute.sh` with `SELF_HEAL_PRECOMPUTE_ONLY=1`.
- Self-heal precompute suppresses precompute email, shadow generation, latest
  shadow publication, and shadow reconciliation.
- Execution continues only when `core/precompute_bundle_validation.py` confirms
  the schema-3 hash manifest for the session, sleeve evaluations, sleeve
  decisions, portfolio allocation, sealed target, read-only projections, and
  audit manifest, including matching session/allocation/target lineage.
- Recovery writes:
  - `outputs/workflow/YYYY-MM-DD/execution_bundle_validation.json`
  - `outputs/workflow/YYYY-MM-DD/execution_self_heal.json`
  - `outputs/workflow/YYYY-MM-DD/precompute_bundle_validation.json`
  - `outputs/workflow/YYYY-MM-DD/precompute_self_heal.json`
- Partial recovery output fails closed. Do not bypass bundle validation to force
  execution.

Post-close Shadow reporting guardrails:
- `scripts/hydrate_price_cache_only.py` is the routine VM cache-only hydrator.
- `scripts/check_shadow_scorecard_health.py` is a read-only post-recovery health
  check for scorecard freshness, NAV continuity, valid-day advancement, and
  post-baseline stale reasons.
- `scripts/audit_shadow_promotion_readiness.py` is a read-only governance audit.
  It never promotes Orion or Lyra and must not alter strategy selection.
- `scripts/backfill_shadow_artifacts.py` is artifact-only recovery tooling. Use
  dry-run, backups, manifest, chronological processing, and look-ahead guards.
- `scripts/validate_cron_commands.py` validates repo-owned cron module/script
  references before deployment.
- Shadow scorecard recovery procedure lives in
  `docs/runbooks/shadow_scorecard_recovery.md`.

---

## Architecture

### Main Orchestrator

`daily_quant_report.py` collects the historical market/research frame. The
registry control plane and `core/portfolio_operating_model.py` then produce the
capital-authoritative session, sleeve decisions, account allocation, and seal.
Legacy planner targets are quarantined research evidence.

### Current Strategy State

- **PAPER portfolio**: registry allocator, currently with Orion as the sole
  capital sleeve and 100% of sleeve risk budget
- **LIVE portfolio**: owner-scoped Lyra weekly portfolio, independently funded
  and scheduled Tuesday at 09:35 ET
- **Historical research control**: Caerus Polaris
- **Shadow daily models**: Caerus Polaris, Caerus Orion, Caerus Lyra
- **Default FR-104 `LIVE_PILOT` sleeve**: Caerus Orion, only when all
  live-pilot approval gates pass
- **Benchmark**: SPY
- **Promotion state**:
  - Polaris: shadow research control
  - Orion: PAPER-only authority; live disabled and separately gated
  - Lyra: Shadow-observed and independently Live-authorized

Named strategy labels are governance identities. They are distinct from
functional alpha-stack sleeves and from the capped FR-104 live-pilot execution
lane.

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

### Retired overnight/thematic design

Earlier documentation described an `overnight_agents` package and thematic
overlay that would feed precompute. Those modules and the corresponding cron
job are not present in the canonical repository and are not deployed. Retain
historical reports for lineage, but do not describe this design as operating
behavior or infer a missing production input from its absence.

### Claude Research Agent (`quant_research_agent/`)

Runs at 6:30 AM ET via `python -m quant_research_agent.main`.
Ingests: arxiv papers, earnings releases, macro data, news headlines.
Scores each item against 5 themes and 14-ticker watchlist using Claude.
Items scoring ≥ 0.40 enter the advisory digest JSON. Canonical precompute does
not currently consume that artifact.

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

### Dashboard Stack (`web/dashboard/`, `scripts/refresh_quant_dashboard.py`)

**Status: V2 prototype live as of 2026-04-21.**

The dashboard was reset from an executive-summary style page into a
broker-authoritative terminal-style surface. Current production intent:

- primary route: `/dashboard/`
- development mirror: `/dashboardDEV/`
- both protected by the existing `Caerus` basic auth
- root host `/` is now a landing page and should not auto-redirect to golf

**Current dashboard source-of-truth hierarchy**

- Positions: Alpaca-backed broker snapshot artifacts
- NAV / cash / buying power: Alpaca-backed broker account snapshot artifacts
- Trades today: Alpaca fills for the report date
- Historical performance: Alpaca portfolio history overlay + SPY benchmark history

**Hard dashboard rule**

Do not reintroduce blended or heuristic headline metrics. If a dashboard panel
cannot be built from a canonical persisted source, degrade visibly or fail.

**Current builder**

- `scripts/research/build_dashboard_v1.py`
  - despite the filename, this now emits `schema_version: dashboard-v2-prototype`
  - contains the strict validation checks and the terminal data model
- `scripts/refresh_quant_dashboard.py`
  - refreshes Alpaca-backed broker snapshot + fills + portfolio history
  - writes the primary static site to `/var/www/caerus-dashboard`
  - writes the dev mirror to `/var/www/caerus-dashboard-dev`

**Current frontend**

- `web/dashboard/index.html`
- `web/dashboard/quant_daily_executive.css`
- `web/dashboard/quant_daily_executive.js`

The UI now defaults dark and terminal-like. The three main chart panels expose:

- Relative Performance — indexed NAV vs SPY
- Excess Curve — cumulative relative edge vs SPY
- Drawdown — portfolio loss from prior peak

Axis/unit notes are intentionally explicit; do not remove them unless replaced
with clearer axis labels and scale markers.

**Dashboard deployment**

- Nginx config: `deploy/caerus-dashboard.nginx`
- Landing page: `deploy/root_landing.html`
- Refresh service: `deploy/caerus-dashboard-refresh.service`
- Refresh timer: `deploy/caerus-dashboard-refresh.timer`
- Deploy script: `scripts/deploy_dashboard_vm.sh`
- Dashboard auth reset: `scripts/reset_dashboard_auth.sh`
- Protected routes use nginx basic auth via `/etc/nginx/.htpasswd_dashboard`
- The reset script uses direct `htpasswd` writes and a local authenticated curl
  check; it must not print or commit secrets.

**Dashboard operational notes**

- `/dashboard/` is the stable user-facing route
- `/dashboardDEV/` is the safe place for iterative UI changes and V3 work
- `/golf/` must remain isolated; dashboard deploys must not overwrite golf bot config
- the dashboard may still surface `DRIFT_DETECTED` from posttrade reconciliation;
  do not hide reconciliation issues just to keep the UI green

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
| `quant_research_agent/main.py` | Claude research digest producer |
| `quant_research_agent/config/strategy_context.yaml` | Themes, watchlist, scoring calibration |
| `core/options_overlay_shadow.py` | Options strategy selection + shadow artifacts |
| `core/options_execution.py` | Options live execution — OCC symbol, Alpaca submission |
| `core/precompute_bundle_validation.py` | Full precompute bundle validation and recovery status payload generation |
| `core/portfolio_operating_model.py` | Immutable session, standardized sleeve decisions, account allocator, lineage validation |
| `core/causal_ownership_ledger.py` | Exact-plan-to-fill sleeve ownership and single-as-of broker valuation |
| `core/daily_portfolio_audit.py` | End-of-day decision/execution/ownership/valuation/reporting proof |
| `config/options_execution_policy.json` | Options execution gates — `allow_live_submission` flag |
| `brokers/alpaca_broker.py` | Alpaca broker — equity + options order submission |
| `scripts/crontab.txt` | Full cron schedule — install with `crontab scripts/crontab.txt` |
| `scripts/cron_research.sh` | Advisory Claude research digest at 6:30 AM ET; no canonical precompute consumer |
| `scripts/cron_precompute.sh` | Phase 1 — 7:00 AM ET precompute |
| `scripts/cron_execute.sh` | Phase 2 — 9:35 AM ET order execution |
| `scripts/cron_confirm.sh` | Phase 3 — 10:00 AM ET confirmation + email |
| `scripts/build_causal_paper_ledger.py` | Build broker-reconciled causal ownership and valuation |
| `scripts/build_daily_portfolio_audit.py` | Build strict end-of-day portfolio audit |
| `scripts/research/build_dashboard_v1.py` | Current strict dashboard builder — emits the broker-authoritative V2 prototype payload |
| `scripts/refresh_quant_dashboard.py` | Refreshes Alpaca broker/fill/history artifacts and publishes both dashboard surfaces |
| `scripts/deploy_dashboard_vm.sh` | Deploys dashboard assets, landing page, refresh service, and nginx config to the VM |
| `deploy/caerus-dashboard.nginx` | VM nginx config for `/`, `/dashboard/`, `/dashboardDEV/`, and preserved `/golf/` |
| `deploy/root_landing.html` | Root landing page linking dashboard, dashboardDEV, and golf bot |
| `web/dashboard/index.html` | Terminal-style dashboard shell |
| `web/dashboard/quant_daily_executive.css` | Dark terminal dashboard styling |
| `web/dashboard/quant_daily_executive.js` | Frontend rendering for metrics, charts, tape, and validation |
| `trading_audit.py` | Holding period, slippage, turnover utilities |
| `reconciliation.py` | Broker-authoritative pre/post-trade reconciliation |
| `data/universe.csv` | 201-ticker trading universe |

---

## Deployment and Verification Rules

Canonical deployment model:
- `origin/main` is the canonical source of truth for deployable source.
- The scheduler VM is a deploy target, not a canonical source.
- Standard flow is `commit -> push -> scripts/deploy.sh on VM -> attest`.
- VM deployment must be fast-forwardable from `origin/main`; do not create VM
  merge commits, rebase VM history, or force-overwrite VM source.
- Local commits do not deploy to the VM until pushed and pulled on the VM.

Wave deployment methodology:
- Group changes by blast radius and rollback boundary.
- Promote independent waves in order of lowest operational risk first.
- Use simulation before promotion for scheduler, recovery, and
  execution-adjacent changes.
- Mark sensitive changes `DEPLOYED_OBSERVING` until runtime artifacts show the
  expected behavior in production.
- Do not assume implemented FRs are deployable; use the FR governance model in
  `docs/governance/fr_governance_model.md`, active work in
  `docs/governance/fr_active_backlog.md`, and deployed/deferred history in
  `docs/governance/fr_registry.md`.

Canonical validation commands:
- Reporting/learning changes:
  `python3 -m pytest Tests/test_feedback_loop_artifacts.py Tests/test_portfolio_learning_report.py -q`
- Shadow orchestration changes:
  `python3 -m pytest Tests/test_shadow_daily_wrapper.py Tests/test_execution_pipeline_integration.py -q`
- Recovery integrity changes:
  `python3 -m pytest Tests/test_execution_pipeline_integration.py Tests/test_precompute_bundle_validation.py -q`
- Governance check:
  `python3 scripts/operational_validation.py`
- VM validation:
  `ssh caerus-vm 'cd ~/quant-daily-report && ./scripts/ops/run_vm_validation.sh'`
- Cron-adjacent shell syntax:
  `bash -n scripts/cron_precompute.sh` and `bash -n scripts/cron_execute.sh`

Canonical deployment sequence:
1. Local audit and targeted validation.
2. Isolated commit with rollback boundary clear.
3. Push to `origin/main`.
4. VM audit: status, HEAD, staged/unstaged/untracked drift.
5. Run `scripts/deploy.sh`; it validates a pinned `origin/main` SHA in a
   temporary worktree, publishes by fast-forward only, and atomically records
   the exact full-SHA attestation.
6. Run routine VM validation to verify the published attestation and targeted checks.
7. Observe the wave-specific runtime artifacts before considering the wave
   fully settled.

Canonical rollback process:
1. Record VM status, HEAD, and relevant runtime evidence.
2. Prefer `git revert <bad-commit>` locally.
3. Push the revert commit.
4. VM deploy the revert through `scripts/deploy.sh`.
5. Re-run operational validation and targeted checks.
6. Preserve generated recovery/status artifacts as evidence; do not delete them
   as a rollback shortcut.

SCP governance:
- SCP is exception-only for emergency hotfixes or recovery diagnostics.
- Any SCP use must be documented, verified, and later reconciled back through git.
- After SCP, verify remote content with `md5sum`, `sha256sum`, or `grep`; never
  assume SCP succeeded.
- Do not leave SCP-only source as production drift.

Deterministic deployment philosophy:
- Reproducibility, rollback safety, and operational auditability are required.
- No undocumented production drift is acceptable.
- Preserve rollback capability before mutation: record HEAD, status, diffs, and
  untracked source candidates before changing the VM.
- Stop on unexplained staged, unstaged, or untracked production source drift.
- Prefer reconciliation over overwrite. Do not use destructive cleanup to make a
  state look clean.

Cron and deployed services:
- `scripts/crontab.txt` is the source cron definition.
- Do not reinstall cron unless the task explicitly includes cron deployment.
- If cron is changed, validate source cron, installed cron, and rollback path.
- Before deploying cron-adjacent changes, run
  `python3 scripts/validate_cron_commands.py scripts/crontab.txt` to catch
  missing repo-owned Python modules or scripts.
- Deployment details live in `docs/deployment_workflow.md`.

For scheduler incidents, inspect:
- `outputs/latest_run.json`
- `logs/execute_<date>.log`
- `logs/shadow_<date>.log`
- `logs/overnight_<date>.log`
- `logs/research_<date>.log`
- `outputs/broker/recon_pretrade_<date>.json`
- `outputs/precompute/<date>/contract.json`
- `outputs/precompute/<date>/session_manifest.json`
- `outputs/precompute/<date>/sleeve_decisions.json`
- `outputs/precompute/<date>/portfolio_allocation.json`
- `outputs/precompute/<date>/audit_manifest.json`
- `outputs/workflow/<date>/execution_bundle_validation.json`
- `outputs/workflow/<date>/execution_self_heal.json`
- `outputs/workflow/<date>/precompute_bundle_validation.json`
- `outputs/workflow/<date>/precompute_self_heal.json`
- `outputs/workflow/<date>/shadow_generate.json`
- `outputs/workflow/<date>/shadow_latest.json`
- `outputs/workflow/<date>/shadow_reconciliation.json`
- `outputs/workflow/<date>/shadow.json`
- `outputs/ledger/paper/causal_fills.jsonl`
- `outputs/ledger/paper/ownership_latest.json`
- `outputs/ledger/paper/valuation_latest.json`
- `outputs/portfolio_history/reporting_snapshot.json`
- `outputs/audit/<date>/portfolio_audit.json`
- `outputs/overnight_signals/<date>.json`
- `outputs/shadow_candidates/<date>/comparison.md`
- `outputs/shadow_candidates/performance/shadow_summary.json`

For dashboard incidents, inspect:
- `/var/www/caerus-dashboard/dashboard_data.json`
- `/var/www/caerus-dashboard-dev/dashboard_data.json`
- `outputs/broker/broker_snapshot_latest.json`
- `outputs/broker/posttrade_positions.json`
- `outputs/broker_snapshot/broker_snapshot_<date>.json`
- `outputs/perf/live_overlay_nav_series.csv`
- `outputs/perf/live_overlay_benchmark_close_history.csv`
- `outputs/broker/recon_posttrade_<date>.json`

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
- Dashboard publishing and nginx routing (`scripts/refresh_quant_dashboard.py`, `deploy/caerus-dashboard.nginx`)

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
- Audit before mutation, especially on the VM or in execution-adjacent code.
- Preserve rollback capability before changing deployment, cron, broker,
  reconciliation, or reporting paths.
- Avoid destructive cleanup. Do not run `git reset --hard`, broad `git clean`,
  stash drops, artifact deletion, or VM overwrites unless explicitly requested
  and recoverability is already established.
- Stop and report if source ownership or canonical state becomes ambiguous.
- If you touch execution, reconciliation, or reporting contracts, run the
  narrowest relevant validation first, then a broader check.
- Report exact commands run and whether they passed.
- Keep research work separate from production behavior unless the task is an
  explicit promotion.
- All cron scripts source `.env` and `scripts/runtime_env.sh` for venv activation.
  Replicate this in any new script.
- The overnight agents accept a `--dry-run` flag for local testing without
  writing output files. Always dry-run first when modifying agent logic.
- If you touch the dashboard, preserve the broker-authoritative data path first
  and the visual layer second. Never silently swap in planned trades, accepted
  orders, or stale run artifacts for positions/NAV/fills just to keep the page populated.
- Prefer promoting risky dashboard/UI experiments to `/dashboardDEV/` first,
  then push to `/dashboard/` only after data and route verification pass.

FR governance:
- Friday refactor work should default to Friday after market close.
- FR work must have a status, blast-radius assessment, dependencies, selected
  validation, rollback plan, and documentation impact before implementation.
- Preferred FR status flow:
  `BACKLOG -> READY -> READY_VALIDATED -> IN_PROGRESS -> PROMOTION_READY -> DEPLOYED_OBSERVING -> DEPLOYED`.
- Do not mark an FR `DEPLOYED` when local WIP, unreconciled VM state, missing
  docs, incomplete validation, or unresolved observation requirements still
  block operational completion.
- Track active FR work in `docs/governance/fr_active_backlog.md`.
- Track deployed and reviewed deferred FR history in
  `docs/governance/fr_registry.md`.
- Track FR methodology, observation-window exits, blast radius, validation, and
  rollback rules in `docs/governance/fr_governance_model.md`.
- Track FR deployment and recovery lessons in
  `docs/governance/operational_lessons.md`.
- Phase 4 backlog work centers on artifact governance, operational telemetry,
  freshness manifests, retention policy, validation isolation, documentation
  taxonomy, and operator trust surfaces. It is non-trading and non-execution by
  default; do not use it as justification for broker, strategy, cron, or
  scheduler rewrites.
- Phase 4 foundation docs:
  `docs/artifact_governance.md`,
  `docs/operational_health_aggregator.md`, and
  `docs/documentation_taxonomy.md`.

Degraded-state engineering:
- Fail safe over fail open.
- Recovery paths must validate their own outputs before allowing execution to
  continue.
- Suppressed side effects must be explicit in status artifacts.
- Do not overwrite or delete latest artifacts to hide stale state; add metadata
  that makes stale/degraded state visible.
- Use controlled local simulations before promoting recovery or scheduler
  changes.

Documentation governance:
- Documentation drift is operational risk.
- Architecture, deployment, cron, scheduler, dashboard, execution, and artifact
  contract changes require documentation review in the same change set or an
  explicit follow-up blocker.
- Agent-facing operational rules in this file must stay synchronized with
  `docs/deployment_workflow.md`, `docs/documentation_governance.md`,
  `docs/OPERATIONS.md`, and `docs/runbook.md`.

Runtime separation:
- Source code: tracked files intended to flow through git.
- Runtime artifacts: `outputs/`, broker snapshots, generated reports, and
  dated research outputs. These are evidence, not deployable source.
- Logs: `logs/` are operational evidence and should not be used to define source
  truth.
- Generated files: dashboard payloads, reports, caches, and model artifacts may
  be regenerated by scheduled jobs; do not treat them as canonical source unless
  the repo explicitly tracks them.
- Deployment state: VM working tree, installed cron, nginx config, services, and
  stashes/backups must be audited before mutation.

---

## Ops Handoff

- Scheduler host path: `~/quant-daily-report`
- Cron source: `scripts/crontab.txt` — it includes the canonical Lyra recurring
  entry; validate operating truth before and after `crontab scripts/crontab.txt`
- Canonical deploy source: `origin/main`
- Standard VM deploy: run `scripts/deploy.sh`; raw pull/merge is not a complete deployment
- SCP is exception-only; reconcile any SCP hotfix back through git before
  considering deployment deterministic again
- Preserve recovery patches and VM stashes until they have been explicitly
  reviewed and declared obsolete
- After source deployment: run targeted validation first; run the full suite only
  when blast radius justifies it
- For post-recovery Shadow checks, run
  `python3 scripts/check_shadow_scorecard_health.py --baseline-date 2026-05-11 --baseline-valid-days 16 --strict`.
- For promotion governance review, run
  `python3 scripts/audit_shadow_promotion_readiness.py`; Polaris remains the
  historical research baseline, Orion is the PAPER execution authority, Lyra
  is Shadow-observed and independently Live-active, and the legacy FR-104 lane
  remains separately disabled.
- The VM cron is the production scheduler for precompute/live execution; GitHub
  daily precompute/live schedules are dispatch-only to avoid duplicate runs
- Successful precompute triggers `scripts/run_shadow_candidates_daily.sh` for
  Polaris / Orion / Lyra. The session, complete decision batch, and account
  allocation are already sealed before that refresh; later shadow output cannot
  mutate PAPER Decision. Stale shadow state prevents overall green health.
- Missing or invalid precompute bundles trigger `SELF_HEAL_PRECOMPUTE_ONLY=1`;
  execution continues only after full bundle validation passes
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
