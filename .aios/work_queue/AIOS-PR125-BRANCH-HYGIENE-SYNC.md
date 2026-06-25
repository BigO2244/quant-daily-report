# AIOS Work Item: AIOS-PR125-BRANCH-HYGIENE-SYNC

## Metadata

- Work ID: AIOS-PR125-BRANCH-HYGIENE-SYNC
- Project ID: caerus
- Project: Caerus
- Status: specification
- Role Assigned: Codex
- Queue Branch: aios/work-queue
- Implementation Branch: codex/AIOS-PR125-BRANCH-HYGIENE-SYNC
- Target Branch: main
- Durable Bus: GitHub repository files and pull requests
- Watcher: disabled
- Automation: disabled

## Objective

Fix the AIOS smoke-test PR branch hygiene and perform a Git synchronization audit across GitHub, local checkout, and VM checkout.

## Context

PR #125 proved the AIOS GitHub queue workflow: ChatGPT created a work item on `aios/work-queue`, Codex read it, created an implementation branch, wrote an output artifact, and opened a PR.

However, PR #125 also included the queue work item file itself in the implementation PR. Implementation PRs should generally include only implementation changes and `.aios/outputs/<work-id>/` artifacts, not re-add queue files from `aios/work-queue` to `main` unless explicitly approved.

Separately, Brett wants assurance that GitHub, the local Mac checkout, and the VM checkout are properly synchronized and that the branch/remote state is understandable before using this workflow for real Caerus work.

## Required Work

### Part 1 — PR #125 Branch Hygiene

Inspect PR #125: `[codex] Record AIOS branch smoke test output`.

Fix the PR so it does not include `.aios/work_queue/AIOS-BRANCH-SMOKE-TEST.md` in the implementation diff to `main`, unless there is a documented reason that the queue item should be merged to `main`.

Expected preferred state:

- `aios/work-queue` keeps the queue work item.
- `codex/AIOS-BRANCH-SMOKE-TEST` / PR #125 contains only `.aios/outputs/AIOS-BRANCH-SMOKE-TEST/` output artifact(s), plus any intentionally documented metadata if needed.
- No production code changes.

If the cleanest fix is to update/rebase/cherry-pick the branch, do so carefully. Do not rewrite unrelated history.

### Part 2 — Git Sync Audit

Perform a read-only synchronization audit across:

1. GitHub repository: `BigO2244/quant-daily-report`
2. Local Mac checkout: `/Users/brettolson/Documents/Caerus/quant-daily-report-main`
3. VM checkout: `/home/brettolson/quant-daily-report` on `caerus-vm`

Check and report:

- Current branch in each checkout.
- Remote URL in each checkout.
- `git rev-parse --short HEAD` and full SHA in each checkout.
- `git status --short` in each checkout.
- Whether local and VM are ahead/behind `origin/main`.
- Whether `origin/aios/work-queue` exists and is fetchable locally and on VM.
- Whether PR #125 branch `origin/codex/AIOS-BRANCH-SMOKE-TEST` exists and is fetchable locally and on VM.
- Whether `.aios/` exists locally and on VM after fetch.
- Any dirty/untracked files that could interfere with pulls, merges, or production runs.

Use read-only commands for the sync audit. Do not pull, reset, merge, delete, or modify files on local or VM unless separately approved.

## Non-Goals

- Do not change trading logic.
- Do not change Caerus runtime behavior.
- Do not modify scheduler/cron/live-paper execution configuration.
- Do not force-push unless absolutely required and explicitly justified.
- Do not clean dirty working trees.
- Do not merge PR #125.

## Validation Required

- Show changed files for PR #125 after branch hygiene fix.
- Confirm PR #125 no longer includes `.aios/work_queue/AIOS-BRANCH-SMOKE-TEST.md` in its implementation diff, or clearly explain why it remains.
- Confirm no production code files changed.
- Provide GitHub/local/VM sync table.
- Provide commands used.

## Expected Codex Response

Use this structure:

Summary
PR #125 branch hygiene result
Files changed
GitHub/local/VM sync table
Dirty working tree findings
Branch/remote findings
Commands run
Validation result
Risks
Recommended next action

## Status Log

- specification: Created by ChatGPT on `aios/work-queue` for Codex execution.
