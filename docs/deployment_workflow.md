# Caerus Deployment Workflow

## Purpose

This document defines the canonical deployment workflow for Caerus after the
2026-05-08 VM reconciliation. It exists to prevent local/VM source drift and to
make every production-like change reproducible, auditable, and reversible.

Git-based deployment exists to ensure:

- deterministic operations
- reproducibility
- rollback safety
- operational auditability
- reduced configuration drift

## Canonical Deployment Philosophy

- `origin/main` is the canonical source of truth for deployable source.
- The scheduler VM is a deploy target, not a source of truth.
- Local development is where changes are made, tested, committed, and pushed.
- The VM should receive source changes by fast-forwarding from `origin/main`.
- VM-only source changes are production drift until reconciled back through git.
- Runtime artifacts, logs, outputs, broker snapshots, and generated dashboard
  payloads are operational evidence. They are not the canonical deployment source.

## Standard Deployment Sequence

Use this sequence for normal source deployments:

1. Confirm local working tree scope:
   - `git status`
   - `git diff --stat`
   - `git ls-files --others --exclude-standard`
2. Commit intentionally on local.
3. Push to `origin/main`.
4. SSH to the VM and audit before mutation:
   - `cd ~/quant-daily-report`
   - `git status`
   - `git log -1 --oneline`
   - `git diff --stat`
   - `git diff --cached --stat`
   - `git ls-files --others --exclude-standard`
5. Stop if unexplained VM source drift appears.
6. Fetch and verify canonical target:
   - `git fetch origin`
   - `git log origin/main -1 --oneline`
7. Fast-forward only:
   - `git merge --ff-only origin/main`
8. Validate:
   - `git status`
   - `git log -1 --oneline`
   - targeted syntax/tests based on changed files

Do not use VM merge commits, rebase, force push, `git reset --hard`, or broad
cleanup to force deployment state.

## VM Validation Steps

Minimum validation after a source fast-forward:

- `git status` reports a clean working tree.
- `git log -1 --oneline` matches the expected `origin/main` commit.
- `git branch --show-current` is `main`.
- Shell scripts touched by the deployment pass `bash -n`.
- Targeted pytest slices pass when Python behavior changed.

Do not run trading workflows, regenerate broker snapshots, or trigger cron jobs
as a deployment validation shortcut.

## Rollback Process

Rollback must preserve recoverability:

1. Record current VM state:
   - `git status`
   - `git log -1 --oneline`
   - relevant service/cron state if touched
2. Preserve any drift:
   - `git diff > recovery.patch`
   - `git diff --cached > recovery_staged.patch`
   - `git ls-files --others --exclude-standard > untracked_files.txt`
3. Prefer git revert for bad committed changes.
4. If returning the VM to an older commit is necessary, stop and make the rollback
   explicit. Do not use `git reset --hard` as an implicit cleanup tool.
5. If cron or service files changed, verify the installed state and preserve the
   prior known-good source.

Existing recovery patches and stash entries must not be deleted until explicitly
reviewed and declared obsolete.

## Fast-Forward Expectations

The normal VM state before deployment is:

- clean working tree
- no staged files
- no unexplained untracked production source
- `main` can fast-forward to `origin/main`

If `git merge --ff-only origin/main` fails, stop and report. Do not create a
merge commit on the VM.

## Drift Detection Expectations

Drift checks are required before VM mutation:

- staged changes: `git diff --cached --stat`
- unstaged changes: `git diff --stat`
- untracked files: `git ls-files --others --exclude-standard`
- current branch: `git branch --show-current`
- current HEAD: `git log -1 --oneline`

Classify drift as:

- already represented canonically
- VM-only source
- emergency hotfix
- incomplete work
- runtime/generated artifact
- logs
- unknown/risky

Stop if VM-only production source changes are unexplained.

## SCP Exception Process

SCP is exception-only. Valid uses are emergency hotfixes, recovery diagnostics,
or a bounded operational repair where git deployment is temporarily unsafe.

Any SCP use requires:

1. Reason for exception.
2. Source and destination paths.
3. Remote checksum or content verification.
4. Rollback path.
5. Follow-up git reconciliation.
6. Documentation if the SCP changed operating behavior.

SCP-only source must not remain the production truth.

## Pre-Deploy Checklist

- [ ] Market/execution window risk reviewed.
- [ ] Local working tree reviewed.
- [ ] Commit pushed to `origin/main`.
- [ ] VM current HEAD and status recorded.
- [ ] VM drift classified.
- [ ] Rollback path identified.
- [ ] Target validation selected before mutation.
- [ ] Cron/service changes explicitly called out if present.
- [ ] No trading workflow will be triggered by validation.

## Post-Deploy Checklist

- [ ] VM HEAD matches expected `origin/main`.
- [ ] VM working tree is clean.
- [ ] Relevant syntax checks passed.
- [ ] Targeted pytest slices passed if required.
- [ ] Cron/service state verified if changed.
- [ ] Recovery notes or FR ledger updated when applicable.
- [ ] Any SCP exception has a git reconciliation follow-up.

## Do Not Deploy Conditions

Do not deploy when:

- VM has unexplained staged or unstaged production source changes.
- VM has unclassified untracked source files.
- `origin/main` is not the expected commit.
- Fast-forward cannot be performed cleanly.
- Rollback path is unknown.
- The change touches execution, reconciliation, cron, or broker state and no
  targeted validation has been selected.
- Local WIP is mixed with unrelated production changes.
- The deployment would run trading workflows or regenerate broker artifacts as a
  side effect of validation.
