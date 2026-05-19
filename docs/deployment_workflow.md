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
   - preferred: `git pull --ff-only origin main`
   - equivalent when already fetched: `git merge --ff-only origin/main`
8. Validate:
   - `git status`
   - `git log -1 --oneline`
   - `python3 scripts/operational_validation.py`
   - targeted syntax/tests based on changed files
9. Observe wave-specific runtime artifacts before closing the deployment:
   - shadow orchestration: `outputs/workflow/<date>/shadow*.json`
   - self-heal recovery: `outputs/workflow/<date>/*self_heal.json`
   - bundle integrity: `outputs/workflow/<date>/*bundle_validation.json`
   - cache migration: first-run cache misses followed by regeneration

Do not use VM merge commits, rebase, force push, `git reset --hard`, or broad
cleanup to force deployment state.

## Wave Promotion Model

Caerus now uses wave deployment for related FR work:

- **Wave 1:** low-risk reporting and CI governance hardening.
- **Wave 2:** orchestration observability and cache namespace hardening.
- **Wave 3:** recovery integrity and degraded-state fail-closed behavior.

Wave rules:

- Keep wave commits isolated by rollback boundary.
- Deploy the lowest-risk independent wave first.
- Run local validation and smoke simulations before push.
- Stop between waves for VM validation and observation.
- Mark scheduler, cache, and recovery changes `DEPLOYED_OBSERVING` until the
  expected runtime artifacts are present and healthy.

## VM Validation Steps

Minimum validation after a source fast-forward:

- `git status` reports a clean working tree.
- `git log -1 --oneline` matches the expected `origin/main` commit.
- `git branch --show-current` is `main`.
- `python3 scripts/operational_validation.py` reports no `FAIL` checks.
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

Canonical source rollback:

```text
git revert <commit>
git push origin main
VM: git fetch origin
VM: git pull --ff-only origin main
VM: python3 scripts/operational_validation.py
```

Runtime artifacts created before rollback are evidence. Do not delete
`outputs/workflow/<date>/`, logs, broker snapshots, or generated reports as a
rollback shortcut.

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

## Operational Observation Surfaces

The following artifacts are produced by deployed orchestration/recovery
hardening and should be inspected when relevant:

| Artifact | Producer | Meaning | Blocking |
|---|---|---|---|
| `outputs/workflow/<date>/shadow_generate.json` | `scripts/run_shadow_candidates_daily.sh` | Shadow generation substep result. | Non-blocking |
| `outputs/workflow/<date>/shadow_latest.json` | `scripts/run_shadow_candidates_daily.sh` | Latest shadow publication result. | Non-blocking |
| `outputs/workflow/<date>/shadow_reconciliation.json` | `scripts/run_shadow_candidates_daily.sh` | Live-vs-shadow reconciliation substep result. | Non-blocking |
| `outputs/workflow/<date>/shadow.json` | `scripts/run_shadow_candidates_daily.sh` | Aggregate shadow wrapper status. | Non-blocking |
| `outputs/workflow/<date>/execution_bundle_validation.json` | `scripts/cron_execute.sh` | Full precompute bundle validation before execution continuation. | Blocking |
| `outputs/workflow/<date>/execution_self_heal.json` | `scripts/cron_execute.sh` | Execution recovery attempt, result, continuation decision, and suppressed side effects. | Blocking when validation fails |
| `outputs/workflow/<date>/precompute_bundle_validation.json` | `scripts/cron_precompute.sh` | Full bundle validation after precompute writes. | Blocking for precompute success |
| `outputs/workflow/<date>/precompute_self_heal.json` | `scripts/cron_precompute.sh` | Self-heal-only precompute status and suppressed side effects. | Feeds execution recovery |

Failure interpretation:

- Shadow artifacts are diagnostic; shadow failure must not block production
  execution.
- Bundle validation artifacts are execution-integrity gates; missing or invalid
  required precompute files must block execution continuation.
- Self-heal artifacts document degraded-state recovery. Repeated recovery
  attempts require operator review even if the final status is healthy.

Cache namespace migration:

- FR-012 added `github.repository_id` to precompute and canonical snapshot cache
  keys.
- First post-deploy GitHub workflow runs may miss old caches and regenerate new
  repository-scoped caches.
- Cache misses are expected during migration; stale or cross-repository restores
  are not.

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
- [ ] `python3 scripts/operational_validation.py` has no `FAIL` checks.
- [ ] Relevant syntax checks passed.
- [ ] Targeted pytest slices passed if required.
- [ ] Cron/service state verified if changed.
- [ ] Recovery notes or FR registry updated when applicable.
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
