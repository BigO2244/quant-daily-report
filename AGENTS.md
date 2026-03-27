# AGENTS.md

## Project Overview

Caerus Quant is a quantitative equity trading and research platform. It
generates daily long-only US equity selections via a regime-switching
multi-sleeve strategy, executes paper trades through Alpaca's paper API,
and maintains full audit trails.

Constraints: - No live money - No short positions - No leverage

Agents working in this repository must preserve deterministic outputs,
protect reconciliation safety mechanisms, and avoid introducing changes
that could silently affect execution or reporting behavior.

------------------------------------------------------------------------

## Project Structure & Module Organization

Key components of the system:

-   daily_quant_report.py --- Main orchestrator for daily execution.

Production trading logic: - core/ - engine/ - regime/ - sleeves/ -
paper/ - reconciliation.py

Research and forward architecture: - research/ - alpha_stack/ -
scripts/research/

AIOps infrastructure: - aiops/ - specs/ - reports/ai_runs/

Testing: - Tests/ - Tests/alpha_stack/

Generated runtime artifacts: - outputs/ - data/

Never manually edit generated artifacts.

GitHub automation: - .github/workflows/

------------------------------------------------------------------------

## Common Development Commands

Environment setup:

python3 -m venv .venv source .venv/bin/activate pip install -r
requirements.txt

------------------------------------------------------------------------

Run locally (Shadow Mode --- No Broker):

REPORT_DATE=\$(TZ=America/New_York date +%F) MODE=shadow
TRADING_MODE=shadow python3 daily_quant_report.py

------------------------------------------------------------------------

Run locally (Alpaca Paper Mode):

REPORT_DATE=\$(TZ=America/New_York date +%F) MODE=alpaca
TRADING_MODE=alpaca ALPACA_PAPER=1 ALPACA_API_KEY_ID=...
ALPACA_API_SECRET_KEY=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets python3
daily_quant_report.py

------------------------------------------------------------------------

Run tests:

pytest -q

Targeted tests:

pytest Tests/test_aiops_contracts.py -v pytest Tests/test_allocation.py
-v

------------------------------------------------------------------------

Local smoke loop:

bash scripts/local_green_loop.sh

------------------------------------------------------------------------

AIOps execution:

aiops run-all --spec specs/`<spec>`{=html}.md --mode BUILD

------------------------------------------------------------------------

Diagnostics / research tools:

python3 scripts/alpha_report.py --apply-costs --cost-bps 25 python3
scripts/daily_alpha_run.py python3 scripts/diag_alpaca_auth.py python3
alpaca_smoke_test.py

------------------------------------------------------------------------

## Agent Working Style

Agents should follow this workflow:

1.  Inspect relevant files
2.  Summarize the current implementation
3.  Propose a concise plan
4.  Implement minimal changes
5.  Run the narrowest validation possible
6.  Summarize results

Prefer minimal, surgical changes over large refactors unless explicitly
requested.

Avoid broad rewrites.

------------------------------------------------------------------------

## Near-Term Focus (Updated 2026-03-24)

For the next several trading days, agents should prioritize improving
model measurement before adding new model complexity.

Primary sequence:

1.  Build the evidence layer
    - Persist daily research artifacts for factor inputs, normalized
      scores, regime state, sleeve selections, target weights, intended
      orders, and realized fills.
    - Make attribution inputs reproducible from artifacts rather than
      from ad hoc notebook logic.

2.  Upgrade attribution from metrics to decomposition
    - Extend attribution beyond IC / Sharpe / turnover.
    - Prioritize sleeve contribution, allocation vs. selection,
      benchmark-relative excess return, and execution-cost drag.

3.  Diagnose current signals before inventing new ones
    - For the quality sleeve, measure factor-level IC by factor, sector,
      and regime.
    - Test sector-neutralization, orthogonalization, and rolling
      reweighting before adding new data sources or ML layers.

4.  Add cost realism to mean reversion
    - Add a transaction-cost / slippage model for the
      mean-reversion sleeve.
    - Re-evaluate backtest quality after costs before promoting further
      live confidence.

5.  Delay "fun" complexity until the measurement loop is in place
    - Do not prioritize new sleeves, alternative data, or ML ranking
      until attribution and cost realism are credible.

Default working plan for the next 10 trading days:

-   Days 1-2: define and write canonical daily research artifacts
-   Days 3-4: build attribution MVP outputs and operator-facing summaries
-   Days 5-6: run quality-sleeve factor diagnostics and rank weak
    factors
-   Days 7-8: implement mean-reversion cost / slippage assumptions and
    rerun research checks
-   Days 9-10: write a decision memo on what to promote, cut, or
    redesign

When choosing between infrastructure polish and modeling work, bias
toward the items above unless production safety is at risk.

------------------------------------------------------------------------

## HighâRisk Areas

Changes in these areas require extra caution:

-   reconciliation.py
-   broker / paper execution paths
-   canonical state under outputs/paper_state/
-   daily_quant_report.py orchestration
-   GitHub workflows
-   artifact schemas
-   CSV / JSON output contracts
-   email governance and reporting

Before modifying these areas, explain expected impact.

------------------------------------------------------------------------

## Research vs Production Discipline

Maintain strict separation:

Production code: - core - engine - sleeves - execution pipeline

Research code: - research - alpha_stack - experimental scripts

Rules:

-   Do not wire research sleeves into production without explicit
    promotion.
-   Do not alter production trading behavior unless requested.
-   Production engine changes should be limited to bug fixes and
    operational hardening.

------------------------------------------------------------------------

## Coding Style

Follow repository conventions:

-   Python style similar to PEP 8
-   4-space indentation
-   clear function boundaries
-   readable docstrings

Naming conventions:

modules: snake_case functions: snake_case variables: snake_case classes:
PascalCase constants: UPPER_SNAKE_CASE

Match surrounding style when editing existing code.

------------------------------------------------------------------------

## Change Hygiene

When modifying code:

-   Preserve deterministic artifact behavior.
-   Do not rename or move operational files without documenting the
    change.
-   If altering output schemas, document old vs new behavior.
-   Preserve public interfaces when possible.
-   Keep diffs focused.

------------------------------------------------------------------------

## Testing Guidelines

Tests live in:

Tests/

Rules:

-   Name files test\_`<feature>`{=html}.py
-   Keep fixtures deterministic
-   Add regression coverage for changes affecting:
    -   execution
    -   reconciliation
    -   reporting
    -   workflow gating

Validation expectations:

1.  Run the narrowest tests first.
2.  Run broader tests if touching orchestration or execution.
3.  Report commands executed.
4.  Report results.

If tests cannot run, state why.

------------------------------------------------------------------------

## Commit & Pull Request Guidelines

Commit messages should be:

-   short
-   imperative
-   scoped

Examples:

fix(execution): repair reconciliation guard feat: add execution summary
csv docs: update alpha stack architecture cleanup: remove archived
workflow research: add alpha factor experiment

Pull Requests should include:

-   purpose
-   risk/impact
-   test commands executed
-   affected paths

Include screenshots when UI or report artifacts change.

------------------------------------------------------------------------

## Security & Configuration

Never commit credentials.

Use:

-   environment variables
-   GitHub Actions secrets

Validate Alpaca credentials before workflow changes:

python3 alpaca_smoke_test.py

For reconciliation/bootstrap procedures see:

docs/OPERATIONS.md

------------------------------------------------------------------------

## Suggested Model Routing

Agents should prefer the following models:

Deep reasoning / audits  Claude Opus\
Engineering logic  Claude Sonnet\
Implementation  GPTâ5.3 Codex\
Hybrid reasoning + code  GPT5.4

------------------------------------------------------------------------

## Expected Response Format for Large Tasks

Agents should return:

1.  Summary
2.  Files changed
3.  Validation performed
4.  Risks or assumptions
5.  Recommended next steps

------------------------------------------------------------------------

## Ops Handoff (Updated 2026-03-27)

March 27, 2026 incident chain:

- 7:00 AM ET precompute succeeded and wrote a bundle.
- 9:35 AM ET execution failed first on precompute contract mode
  validation (`PAPER` bundle vs `ALPACA` executor expectation).
- After that validator mismatch was fixed, execution failed a second
  time because pretrade reconciliation returned `SELF_HEAL`, refreshed
  canonical state, and the executor halted instead of re-running
  reconciliation once against the repaired state.
- Manual rerun at 9:43 AM ET executed successfully after those two
  fixes and Phase 3 confirmation was rerun successfully afterward.

Operational hardening that must remain in place:

- Treat precompute contract `PAPER` and `ALPACA` as equivalent for
  execution validation unless and until the contract writer and executor
  are fully normalized to one canonical value.
- In the execution wrapper, if pretrade reconciliation returns
  `SELF_HEAL`, immediately run reconciliation once more in the same run
  and proceed when the follow-up result is `PASS` or `WARN`.
- Cron wrappers for scheduler-host production paper flow must force:
  `MODE=alpaca`, `TRADING_MODE=alpaca`, `ALPACA_PAPER=1`.
  Do not rely on inherited `.env` mode flags for morning automation.
- Same-day retry locks should continue to block duplicate executions
  after a successful run, but known pre-execution failures must release
  the lock so a recovery rerun is possible.

First checks for the next incident review:

1.  Confirm remote `~/quant-daily-report` is on a git-backed commit, not
    only SCP overlays.
2.  Verify `outputs/precompute/<date>/contract.json` writes
    `"mode": "ALPACA"` under cron.
3.  If 9:35 halts again, inspect in this order:
    - `outputs/latest_run.json`
    - `logs/execute_<date>.log`
    - `outputs/broker/recon_pretrade_<date>.json`
    - `outputs/precompute/<date>/contract.json`
4.  If execution succeeds after a manual rerun, rerun
    `scripts/cron_confirm.sh` immediately so operator emails and trade
    confirmation artifacts match the real execution.

Cleanup backlog after a clean March 28, 2026 cycle:

- Push all scheduler fixes and repin the VM to a clean git checkout.
- Remove environment ambiguity between `MODE`, `TRADING_MODE`, and
  paper/live intent.
- Add an explicit scheduler smoke command that fails if precompute and
  executor disagree on bundle mode.
- Add a reliability test for the exact 7:00 -> 9:35 -> 10:00 cron path,
  not just unit tests around helper functions.

------------------------------------------------------------------------

## Operational Handoff (Updated 2026-03-26)

The scheduler host was clean-swapped on 2026-03-26 after a partial /
dirty deploy incident. Tomorrow's starting assumption should be:

-   Scheduler host path: `~/quant-daily-report`
-   Expected live git head: `f3a768b`
-   Expected remote `origin`: GitHub, not a local filesystem path
-   Expected cron source: `scripts/crontab.txt`
-   Expected canonical snapshot state: refreshed from broker after clean
    deploy

### First checks for the next session

1.  Verify remote repo state before changing code
    - `ssh brettolson@34.61.147.38`
    - `cd ~/quant-daily-report`
    - `git rev-parse --short HEAD`
    - `git remote -v`
    - `git status --short`
    - `crontab -l`

2.  Verify the 2026-03-27 precompute phase
    - confirm the 7:00 AM ET precompute email arrived
    - confirm `outputs/precompute/2026-03-27/` exists and bundle files are
      complete
    - inspect `logs/precompute_2026-03-27.log`

3.  Verify the 2026-03-27 execution phase
    - confirm the 9:35 AM ET execution submitted intended Alpaca paper
      orders
    - inspect `logs/execute_2026-03-27.log`
    - inspect `outputs/latest_run.json`
    - inspect the latest `execution_results.json`

4.  Verify the 2026-03-27 confirmation phase
    - confirm the 10:00 AM ET operator confirmation email arrived
    - inspect `logs/confirm_2026-03-27.log`
    - verify operator summary / confirmation artifacts were written

### If tomorrow's run is clean, next cleanup priorities

1.  Replace ad hoc VM deployment with a scripted clean deploy plus
    rollback path.
2.  Separate runtime state from code checkout
    - `.env`
    - `venv/`
    - `data/`
    - `outputs/`
    - `logs/`
3.  Audit ignored-but-required runtime files so git remains the real
    source of truth.
4.  Add a standard deploy smoke gate
    - compile critical modules
    - broker bootstrap
    - plan-only precompute
    - pretrade email dry-run
    - confirmation email dry-run against a real execution artifact
    - execution module import / `--help`
5.  Review residual-position behavior for names removed from targets
    (example: `MU`) and confirm full exits occur when intended.
6.  Use `docs/mu_trade_trace_2026-03-26.md` as the reference memo for the
    `MU` post-mortem and liquidation-scaling follow-up.
