# AGENTS.md

## Project Overview

Caerus Quant is a quantitative equity trading and research platform. It
generates daily long-only US equity selections via a technical
trend-following strategy, executes paper trades through Alpaca's paper
API, and maintains full audit trails.

Constraints: - No live money - No short positions - No leverage

Agents working in this repository must preserve deterministic outputs,
protect reconciliation safety mechanisms, and avoid introducing changes
that could silently affect execution or reporting behavior.

------------------------------------------------------------------------

## Project Structure & Module Organization

Key components of the system:

-   daily_quant_report.py --- Main orchestrator for daily execution.

Production trading logic: - core/ - engine/ - sleeves/ - paper/ -
reconciliation.py

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