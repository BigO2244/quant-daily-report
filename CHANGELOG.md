# Changelog

**System:** Caerus Quant — Daily Execution & Research System
**Owner:** Brett Olson

This log covers material architectural and process changes. Routine bug fixes and minor patches are not recorded here — see `git log` for full commit history. Engineering-level detail for individual changes is in [`docs/MODEL_CHANGES.md`](docs/MODEL_CHANGES.md).

---

## Recent Major Changes (Early 2026)

### Single Orion Decision Line and Broker-Truth NAV — August 14, 2026

- Replaced the split PAPER morning authority with one sealed Orion Decision
  target selected from the current/immediately-prior XNYS sleeve evaluation.
- Added schema-2 bundle hashes and one `approved_target_hash` across the target
  package, signals, handoff, 09:35 plan, and exact authorization.
- Quarantined the legacy `growth_engine_v4` precompute signals/trades as
  content-hashed research evidence with no execution authority.
- Made readiness certify target lineage/assets/connectivity instead of
  presenting stale pre-open share counts as expected broker submissions.
- Made canonical actual PAPER NAV derive only from the direct Alpaca
  broker-truth ledger; rescheduled the ledger to 19:15 ET and portfolio-history
  projection/freshness escalation to 19:45 ET.
- PAPER/live boundaries remain unchanged: no live credentials, approvals,
  limits, or capital were enabled.

### Shadow Scorecard Publication Integrity Gates (FR-085) — June 24, 2026

- The daily Shadow CIO Model Scorecard (`scripts/send_shadow_cio_report.py`) now withholds publication instead of printing a leaderboard from stale or internally inconsistent research artifacts.
- Freshness gate: withholds rankings, leader/runner-up/laggard, promotion signals, and the CIO takeaway when the shadow refresh failed, the latest valid NAV lags the report date beyond `SHADOW_CIO_MAX_NAV_AGE_TRADING_DAYS` (default 2 trading days), or NAV integrity is corrupt — emitting a `MODEL SCORECARD: PUBLICATION WITHHELD` block while preserving the DATA HEALTH diagnostics.
- Internal-consistency gate: a sleeve is excluded from ranking and leader selection unless its period return is NAV-derived (`period_return_source == "nav"`) and it has at least `MIN_VALID_DAYS` (10) valid days. This prevents a zero-track-record concentration variant (e.g. `Orion_Alpha`, activated 2026-06-23) from being crowned leader via an unvalidated `cumulative_return` fallback.
- Reporting-only; no trading, execution, allocation, broker, or cron behavior changed. Branch `fix/scorecard-publication-gates`, draft PR #117. The upstream NAV refresh-freeze (why the series is stuck at 2026-06-05) is tracked as a P2 follow-up. Full FR record: `docs/governance/fr_active_backlog.md` (FR-085).

### Shadow Refresh Alpha Inception Handling & NAV Restatement (FR-086) — June 24, 2026

- Diagnosed and fixed the Shadow NAV refresh freeze (the series was stuck at 2026-06-05). Root cause was not scheduler, cache, path, or signal warmup: the NAV append correctly failed closed on a continuity mismatch (`SHADOW_NAV_CONTINUITY_MISMATCH`) because the active NAV file carried mixed/legacy-scale history (`caerus_polaris` prior NAV 38.22 vs operational-scale 1.89).
- `scripts/refresh_shadow_scorecard_artifacts.py` and shadow definition/performance generation are now date-aware for the alpha concentration variants: `observation_start_date` 2026-06-23, blank pre-inception NAV cells, and the first real alpha row seeded from `previous_nav=1.0`. Regression coverage added.
- Operational recovery (VM): re-seeded the active NAV from the validated same-day operational staging artifact and replayed the 2026-06-15 → 2026-06-23 backfill in order (Juneteenth and weekend correctly skipped); the active NAV is restored to 29 rows (2026-05-12 → 2026-06-23). Combined with FR-085 the scorecard un-withholds (Data health Fresh; Leader Orion +30.25% Since Observation Inception).
- No trading, allocation, execution, broker, scheduler, or price-download logic changed. Branch `fix/shadow-refresh-freeze`, draft PR #118. Full FR record: `docs/governance/fr_active_backlog.md` (FR-086).

### Named Strategy Framework and Daily Shadow Lane — April 2026

- Introduced named strategy framework for operator-facing materials:
  - `Caerus Polaris` = current paper baseline / operational control
  - `Caerus Orion` = primary shadow candidate
  - `Caerus Lyra` = secondary shadow challenger
  - `SPY` remains the benchmark
- Added DEV-only shadow lane under `research/shadow_tracking`
- Added automatic post-precompute shadow hook via `scripts/run_shadow_candidates_daily.sh`
- Shadow artifacts now land under `outputs/shadow_candidates/YYYY-MM-DD/` and `outputs/shadow_candidates/performance/`
- Polaris remains the active production paper control; Orion and Lyra are shadow only

### VIX Producer Integration — March 6, 2026

- Added `update_vix_close_history()` producer in `paper/perf_artifact_producers.py`
- VIX data now automatically refreshed in `daily_quant_report.py` and `research/alpha_assessment/build_alpha_assessment.py`
- Output: `outputs/perf/vix_close_history.csv` with columns `date`, `vix_close`, `vix_return`
- Unified perf artifacts pipeline: benchmark + VIX + analyzer scores all auto-refreshed before canonical build
- See `docs/canonical_performance_runbook.md` for build commands and producer automation
### Documentation Pack — March 2026

- Created `README.md` as the authoritative repo-level overview and onboarding doc
- Created `docs/model_strategy.md` — strategy intent, alpha hypotheses, selection framework, risk overlays
- Created `docs/runbook.md` — operator guide, daily checklists, failure recovery procedures
- Created `CHANGELOG.md` (this file)
- Existing docs preserved: `docs/MODEL_AUDIT.md`, `docs/MODEL_CHANGES.md`, `docs/OPERATIONS.md`, `docs/run_archiving.md`, `docs/audit.md`, `docs/performance_reporting.md`, `docs/aiops_workflow.md`

---

### Research Digest Workflow — `research-digest.yml`

- Added nightly `research-digest.yml` workflow (7:00 AM ET, weekdays)
- Uses `quant_research_agent/main.py` with Claude (Anthropic) and FRED API for macro/market signal scoring
- Dedup store persisted via GitHub Actions cache (`quant_research_agent/store/seen_ids.json`)
- Completely isolated from trading workflows — failures here cannot block execution
- Requires separate secrets: `ANTHROPIC_API_KEY`, `FRED_API_KEY`

---

### Alpha Lab v0 Research Framework — `d8519a2`

- Added `research/alpha_lab_v0/` — initial research framework for signal discovery and evaluation
- Signal registry and evaluation pipeline design documented in `docs/ALPHA_LAB_V1_TECH_BUILD_PLAN.md`
- Alpha Lab is the planned bridge between research discovery and strategy promotion

---

### Canonical Positions Refresh After Execution — `49b5bda`

- Canonical model snapshot is now refreshed from broker state after each successful execution run
- Ensures the snapshot always reflects actual filled positions, not just the pre-execution model view

---

### VIX Regime Detection (Tier 2) — `bf560f6`

- Added four-tier VIX-based volatility regime classifier (`research/vix_regime.py`)
- Regime (LOW / ELEVATED / HIGH / CRISIS) drives both position count cap and gross exposure scale
- Regime table (from `sleeves/sleeve_trend/config.py`):
  - LOW (VIX < 20): 100% scale, max 10 positions
  - ELEVATED (20–30): 75% scale, max 7 positions
  - HIGH (30–40): 50% scale, max 4 positions
  - CRISIS (VIX ≥ 40): 25% scale, max 2 positions
- Fallback VIX = 25.0 (ELEVATED) if yfinance unavailable
- Full deployment preserved if entire VIX call fails (log warning)
- Persists regime history to `outputs/vix_regime/`

---

### Tier 1 Model Enhancements — `0a7a717`, `c9bfc96`

**Config extraction (Sleeve 1):** All 25 hardcoded constants in `sleeves/sleeve_1/backtest.py` extracted to `sleeves/sleeve_1/config.py`. No parameter values changed.

**Walk-Forward Optimization (WFO):** Added `run_walk_forward()` to `backtests/sleeve1_robustness.py`. Rolling 12-month in-sample / 3-month OOS windows. Initial 4-window result (2023–2024): OOS Sharpe 2.97, IS Sharpe 1.60 — OOS > IS is a bull-market artifact; extended 2015–present run pending.

**IC Monitor:** Added `research/ic_monitor.py` — rolling Pearson IC between composite signal and next-day return. Runs non-blocking after each daily report. Alerts when 60d rolling IC < 0.03 or IC < 0 for 20+ days.

**Cleanup:** Removed dead root-level `portfolio_alloc.py` wrapper. Fixed `engine/breaker.py` to use `logging` instead of `print()`. Updated `DEFAULT_SLEEVE_WEIGHTS = {"sleeve_trend": 1.00}` and `STASH_SLEEVE_NAME = "CASH"` to reflect production state.

---

### Paper Mode Enhancements — `e20ef9d`

- Weekend pause: execution skips on non-trading days
- Holdings sync: positions marked to market daily
- 75% daily turnover cap enforced in paper execution layer
- Documented in `docs/PAPER_MODE_ENHANCEMENTS.md`

---

### Self-Healing Pre-Trade Reconciliation — `ae2dbd4`

- Added optional auto-bootstrap on reconciliation failure (`AUTO_BOOTSTRAP_ON_RECON_FAIL` repo variable)
- When enabled: workflow detects exit code 2 from recon failure, re-bootstraps canonical snapshot from broker, sends alert email, marks run as recovered (exit 0)
- Safety sentinel: auto-bootstrap only triggers if `outputs/broker/recon_pretrade_<DATE>.json` is present (preventing false triggers from unrelated exit code 2 failures)
- When disabled (default): workflow fails with exit 2; manual bootstrap required via `workflow_dispatch`
- Full ops documentation added to `docs/OPERATIONS.md`

---

### Workflow Split: Execution and Email Decoupled

- `daily-alpaca-paper.yml` split into two jobs: `engine_run` (execution + artifact production) and `email` (artifact download + send)
- Email job always runs (`if: always()`) even when `engine_run` fails, ensuring operator notification on recon failures and other errors
- Execution email payload persisted as `outputs/execution_email/<DATE>.json` so the email job can reconstruct it without re-running the engine

---

### Run Archiving — Immutable Artifact Structure

- Each CI run now writes all outputs under `outputs/runs/<RUN_ID>/`
- `RUN_ID` is a unique timestamp + GitHub run ID per attempt; reruns do not overwrite prior runs
- `outputs/latest.json` is a mutable pointer to the most recent run
- Structure documented in `docs/run_archiving.md`
- Legacy `paper/ledger.csv` and `paper/trades.csv` demoted to seed-only inputs at first init

---

### Sleeve Consolidation: Sleeve Trend Only in Production

- `DEFAULT_SLEEVE_WEIGHTS` updated to `{"sleeve_trend": 1.00}` (previously incorrectly listed `sleeve_2` and `charlie_munger`)
- Sleeve 1 runs but its output is discarded (`_, _ = run_sleeve_1()`)
- Sleeve 2 and Charlie Munger exist only as stubs in the orchestrator
- Decision on Sleeve 1 (integrate vs archive) is documented as an open question

---

### AIOps Governance Layer Added

- `aiops/` module added with CLI commands: `parse`, `plan`, `dispatch`, `verify`, `run`, `run-all`
- Specs live in `specs/` — markdown files defining build contracts with deterministic artifact outputs
- Exit codes standardized: 0=OK, 2=needs_operator, 3=verify_failed, 4=parse_plan_failed, 5=dispatch_failed, 6=run_failed
- Contract test suite enforces deterministic CLI outputs, stable exit codes, and golden artifact formats
- See `specs/aiops_system_contract_v0_1.md` and `docs/aiops_workflow.md`

---

### Audit and Monte Carlo Framework

- Added deterministic audit bundles: `audit/policy_backtest.py`, `scripts/run_audit_2022_and_worst.py`, `scripts/run_mc_and_pick_worst.py`
- Monte Carlo worst-window analysis with configurable metrics (MAX_DD, CAGR, ULCER)
- Environment knobs documented in `docs/audit.md`

---

## Open Items (as of March 2026)

| Item | Notes |
|---|---|
| Extended WFO run (2015–present) | Initial 4-window result pending full 36-window validation |
| Sleeve 1 fate | Runs daily but output discarded — integrate or archive decision open |
| Sleeve 2 / Charlie Munger stub removal | Dead code in `daily_quant_report.py` |
| Secondary data provider | Single Yahoo Finance dependency; no fallback |
| Live money criteria | No formal criteria defined for paper → live transition |
