# Caerus Friday Refactor Backlog

## Purpose

This backlog captures larger Caerus architecture improvements that are valuable but should be reviewed and implemented only during controlled maintenance windows, preferably Friday after market close.

These items are not emergency fixes. They are intended to reduce workflow scope, improve observability, simplify operations, and lower the chance that reporting or research work interferes with trading-critical paths.

## Maintenance Window Rule

- Default implementation window: Friday after market close.
- No changes during active trading windows unless there is an urgent safety issue.
- Every refactor requires:
  - scoped Codex prompt
  - status and dependency review
  - blast-radius assessment
  - targeted tests
  - rollback plan
  - VM validation
  - documentation impact review
  - no execution behavior changes unless explicitly approved

## FR Governance

Recommended status flow:

```text
BACKLOG -> READY -> READY_VALIDATED -> IN_PROGRESS -> DONE -> DEPLOYED
```

Status meanings:

- `BACKLOG`: useful work, not ready for implementation.
- `READY`: scope, dependencies, rollback path, and validation plan are clear.
- `READY_VALIDATED`: pre-work audit confirms source ownership, VM state, and
  dependencies are safe enough to begin.
- `IN_PROGRESS`: implementation is actively underway.
- `DONE`: implementation and local validation are complete, but deployment may
  still be pending.
- `DEPLOYED`: committed, pushed, pulled on VM through git, validated, and docs
  updated.

Blast-radius framework:

- `LOW`: docs, tests, or isolated reporting with no scheduler/runtime effect.
- `MEDIUM`: research/reporting code, generated artifacts, dashboard rendering,
  or non-blocking shadow behavior.
- `HIGH`: cron, deployment, execution, reconciliation, broker state, order
  submission, or canonical runtime state.

Dependency expectations:

- Identify upstream FRs, source ownership, and runtime assumptions before work.
- Audit local and VM git state before any deployment-related FR.
- Stop if canonical source or VM ownership is ambiguous.

Validation expectations:

- Select targeted validation before implementation.
- Validate the smallest relevant slice first.
- Do not run trading workflows or regenerate broker artifacts as FR validation
  unless the FR explicitly requires it and the risk is approved.

Documentation expectations:

- Update docs in the same change when operational behavior changes.
- Add or update `docs/fr_execution_ledger.md` for completed FR work.
- Documentation drift is operational risk and can block `DONE` or `DEPLOYED`.

## Backlog Items

### FR-001

**Title:** Split shadow wrapper responsibilities
**Category:** Shadow / Workflow
**Priority:** HIGH
**Status:** BACKLOG

**Why it matters:**
`scripts/run_shadow_candidates_daily.sh` currently handles generation, latest publication, desktop symlink, and reconciliation. Those are related operationally, but they are separate responsibilities with separate failure modes.

**Risk if ignored:**
Shadow generation can appear healthy or unhealthy for the wrong reason. Non-blocking failures may be hard to diagnose, and future additions could make the wrapper too broad.

**Proposed approach:**
Split into small helpers:

- `generate_shadow_artifacts`
- `publish_shadow_latest`
- `reconcile_live_vs_shadow`

Each helper should write its own status artifact. The top-level wrapper should remain non-blocking and summarize substep status.

**Files likely involved:**

- `scripts/run_shadow_candidates_daily.sh`
- `research/shadow_tracking/run.py`
- `scripts/live_vs_shadow_reconciliation.py`
- `outputs/workflow/YYYY-MM-DD/shadow.json`

**Validation required:**

- `python3 -m pytest Tests/test_shadow_daily_wrapper.py -q`
- `python3 -m pytest Tests/test_shadow_tracking.py -q`
- Manual dry run with a historical trade date
- VM validation after git-based deploy

**Rollback plan:**
Restore the prior `scripts/run_shadow_candidates_daily.sh` and remove any new helper invocations from the wrapper.

**Notes:**
Keep shadow non-blocking. Do not allow shadow failures to block precompute or execution.

### FR-002

**Title:** Add price cache coverage metadata sidecar
**Category:** Data / Hydration
**Priority:** MEDIUM
**Status:** BACKLOG

**Why it matters:**
Freshness checks should not need to inspect the full `price_panel.parquet` when only max date and symbol coverage are needed.

**Risk if ignored:**
Full parquet reads remain acceptable now, but will become more expensive as history and universe size grow.

**Proposed approach:**
Create:

```text
outputs/research/flow_detection_v1/price_panel_coverage.json
```

Include max cache date, row count, ticker count, ignored tickers, aliased tickers, provider, and generation timestamp.

**Files likely involved:**

- `research/flow_detection/data.py`
- `core/price_hydration.py`
- `scripts/hydrate_price_cache_only.py`
- `Tests/test_price_cache_only_hydrator.py`

**Validation required:**

- `python3 -m pytest Tests/test_price_cache_only_hydrator.py -q`
- Manual cache-only dry run
- Manual cache-only strict run on VM

**Rollback plan:**
Stop writing/reading the sidecar and fall back to current parquet inspection.

**Notes:**
Sidecar must be advisory. The parquet remains canonical.

### FR-003

**Title:** Add managed bad ticker / ticker exceptions
**Category:** Data Quality
**Priority:** MEDIUM
**Status:** DONE

**Why it matters:**
Repeated provider failures, such as empty Yahoo downloads for `MMC`, create log noise and wasted work.

**Risk if ignored:**
Hydration diagnostics remain noisy and can obscure real freshness issues.

**Proposed approach:**
Add `data/ticker_exceptions.json` with:

- `ignore`
- `aliases`
- `notes`

Hydration should explicitly report ignored and aliased tickers.

**Files likely involved:**

- `data/ticker_exceptions.json`
- `research/flow_detection/data.py`
- `scripts/hydrate_price_cache_only.py`
- `docs/price_hydration.md`
- `Tests/test_ticker_exceptions.py`

**Validation required:**

- `python3 -m pytest Tests/test_price_cache_only_hydrator.py Tests/test_ticker_exceptions.py -q`
- `python3 -m scripts.hydrate_price_cache_only --dry-run`

**Rollback plan:**
Remove the config or empty the `ignore` and `aliases` sections to restore default provider behavior.

**Notes:**
Implemented after the initial architecture audit. Keep the item for tracking and future additions.

### FR-004

**Title:** Create feedback-loop rolling index
**Category:** Learning / Reporting
**Priority:** MEDIUM
**Status:** BACKLOG

**Why it matters:**
Weekly learning reports should not need to scan many dated JSON files as history grows.

**Risk if ignored:**
Reporting may become slower and harder to reason about. Missing per-day files may create noisy partial status even when the key data exists.

**Proposed approach:**
Write compact daily rows for return, turnover, top-3 concentration, valid days, attribution status, regime, and readiness. Weekly reports can eventually read the index instead of scanning per-date artifacts.

**Files likely involved:**

- `core/feedback_loop_artifacts.py`
- `core/portfolio_learning_report.py`
- `scripts/send_portfolio_learning_review.py`
- `outputs/shadow_candidates/performance/`

**Validation required:**

- `python3 -m pytest Tests/test_feedback_loop_artifacts.py -q`
- `python3 -m pytest Tests/test_portfolio_learning_report.py -q`
- Manual generation for a historical trade date

**Rollback plan:**
Leave existing per-date artifact reads in place until the index is proven. Disable index reads if mismatches appear.

**Notes:**
Index should be additive first. Do not remove existing artifacts.

### FR-005

**Title:** Add self-heal-only precompute mode
**Category:** Execution Safety / Scheduler
**Priority:** HIGH
**Status:** BACKLOG

**Why it matters:**
`scripts/cron_execute.sh` can call `scripts/cron_precompute.sh` when a precompute bundle is missing. That recovery path should avoid non-critical emails and shadow side effects.

**Risk if ignored:**
Execution-window recovery can run broader work than necessary and blend recovery logs with normal precompute side effects.

**Proposed approach:**
Add a self-heal mode that:

- rebuilds only the required precompute bundle
- suppresses precompute email
- suppresses shadow lane
- writes explicit recovery status

**Files likely involved:**

- `scripts/cron_execute.sh`
- `scripts/cron_precompute.sh`
- `daily_quant_report.py` only if strictly necessary
- `outputs/workflow/YYYY-MM-DD/`

**Validation required:**

- targeted shell tests if available
- `python3 -m pytest Tests/test_execution_pipeline_integration.py -q`
- manual VM simulation with missing precompute bundle in a safe historical date directory

**Rollback plan:**
Restore the existing `cron_execute.sh` self-heal invocation.

**Notes:**
Do not change normal execution behavior. This is recovery-path-only work.

### FR-006

**Title:** Separate required vs optional artifact health in portfolio learning report
**Category:** Reporting
**Priority:** LOW
**Status:** BACKLOG

**Why it matters:**
The weekly portfolio learning report can look weak when optional learning artifacts are missing even if the scoreboard is usable.

**Risk if ignored:**
Operator diagnosis may overstate reporting weakness and create unnecessary concern.

**Proposed approach:**
Split artifact health into:

- core required artifacts
- optional learning artifacts
- diagnostics-only artifacts

**Files likely involved:**

- `core/portfolio_learning_report.py`
- `Tests/test_portfolio_learning_report.py`

**Validation required:**

- `python3 -m pytest Tests/test_portfolio_learning_report.py -q`
- Manual dry run of weekly portfolio learning report

**Rollback plan:**
Restore the current single artifact-health classification.

**Notes:**
Keep missing data explicit. Do not hide unavailable artifacts.

### FR-007

**Title:** Revisit full parquet read/write scaling
**Category:** Data Engineering
**Priority:** LOW
**Status:** BACKLOG

**Why it matters:**
`ensure_price_panel` is acceptable now, but full parquet reads/writes may become expensive if universe size, history length, or report frequency increases.

**Risk if ignored:**
Hydration may become slower or memory-heavy again.

**Proposed approach:**
Evaluate partitioning by date or ticker, or maintain a compact coverage/index sidecar before attempting larger storage changes.

**Files likely involved:**

- `research/flow_detection/data.py`
- `scripts/hydrate_price_cache_only.py`
- `Tests/test_flow_detection_v1.py`

**Validation required:**

- existing flow detection tests
- cache-only hydration tests
- VM runtime/memory comparison

**Rollback plan:**
Keep the current single parquet as canonical until the replacement is fully validated.

**Notes:**
Do not over-engineer until there is repeated memory or runtime pressure.

### FR-008

**Title:** Clean git/VM deployment workflow
**Category:** Operations
**Priority:** HIGH
**Status:** READY_VALIDATED
**Blast Radius:** HIGH

**Why it matters:**
Recent production updates have used SCP due to dirty local and VM worktrees. This is pragmatic but not durable.

**Risk if ignored:**
Local and VM state can drift, source cron can diverge from installed cron, and rollback becomes harder.

**Current state:**
The 2026-05-08 reconciliation restored deterministic VM git deployment. The VM
was backed up, stashed, fast-forwarded to canonical `origin/main` at `3de68f8`,
and validated clean. Recovery patches and VM stashes remain intentionally
preserved. SCP is now exception-only. Local WIP still exists, so this item should
not be marked fully `DONE` or `DEPLOYED` until local WIP is resolved
intentionally and the governance docs are committed.

**Proposed approach:**
Define a clean deploy flow:

```text
commit -> push -> pull on VM -> validate -> rollback path
```

Also define the exceptional hotfix SCP path and required post-SCP verification.

**Files likely involved:**

- `AGENTS.md`
- `docs/deployment_workflow.md`
- `docs/documentation_governance.md`
- `docs/fr_execution_ledger.md`
- `docs/OPERATIONS.md`
- `docs/runbook.md`
- `scripts/deploy_*`
- `scripts/crontab.txt`

**Validation required:**

- dry-run deployment checklist
- VM `git status`
- targeted tests after pull
- cron source vs installed cron comparison

**Rollback plan:**
Preserve VM patches/stashes first. Prefer git revert for bad committed changes.
Do not use destructive reset/clean as normal rollback. If cron changed, restore
the prior tracked cron source and reinstall only as an explicit cron deployment.

**Notes:**
Operational reconciliation phase is effectively complete. Remaining work is
local WIP resolution, committing governance docs, and any future deployment
automation or checklist refinement.

## Friday Review Checklist

- [ ] Market closed
- [ ] No live execution window active
- [ ] Current VM status clean
- [ ] Latest trading confirmation reviewed
- [ ] Latest hydration status OK
- [ ] Current branch and git status recorded
- [ ] Backups or rollback path identified
- [ ] Tests selected before implementation
- [ ] Deployment plan clear
- [ ] Post-change validation complete

## Change Log

| Date | Item | Action | Result | Follow-up |
|---|---|---|---|---|
| 2026-05-04 | FR-003 | Implemented managed ticker exceptions with `MMC` ignored. | DONE | Monitor hydration status for additional provider failures. |
| 2026-05-08 | FR-008 | Reconciled VM to canonical `origin/main` and documented git-based deployment governance. | READY_VALIDATED | Resolve local WIP intentionally before marking fully DONE/DEPLOYED. |
