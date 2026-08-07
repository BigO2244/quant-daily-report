# Codex Task Template

Use this template when ChatGPT delegates scoped implementation, audit,
documentation, or validation work to Codex.

## Objective

State the concrete outcome Codex should produce.

## Scope

List the intended work boundaries, including whether the task is documentation,
diagnostics, tests, research-only code, execution-adjacent code, or runtime
behavior.

## Non-Goals

List what Codex must not solve in this task.

## Files Likely Involved

- `path/to/file.py`
- `path/to/test_file.py`
- Documentation file to update, when applicable.

## Forbidden Changes

- Do not mutate trading, execution, allocation, broker, strategy, cron, or
  production runtime behavior unless this task explicitly authorizes it.
- Do not weaken reconciliation, freshness, cash gates, validation, or
  fail-closed semantics.
- Do not promote, retire, rename, or reweight strategies or sleeves.
- Do not deploy, push, or fast-forward the VM unless explicitly requested.

## Required Tests

- Documentation-only: `git diff --check` plus grep/link sanity checks.
- Python changes: targeted pytest for touched behavior and relevant
  `py_compile`.
- Execution-adjacent diagnostics: execution integrity, execution timeline,
  MCP/status tools, and focused fixtures.

## Expected Artifacts

List new or changed files, generated artifacts, MCP outputs, reports, or docs.
State whether generated outputs should be committed or only used for validation.

## Definition Of Done

- Scope is satisfied.
- Forbidden changes were avoided.
- Required validation passed or failures are explained.
- Unrelated dirty worktree changes were preserved.
- Final response includes files changed, behavior impact, tests run, and risks.

## Final Response Format

- Summary of diagnosis and implementation.
- Files added or modified.
- Confirmation of runtime/trading behavior impact.
- Tests and checks run.
- Remaining risks or assumptions.
