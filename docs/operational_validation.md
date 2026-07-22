# Operational Validation

## Purpose

`scripts/operational_validation.py` is the read-only governance validator used
before and after git-based deployments. It checks CI/CD governance and workflow
syntax without running trading workflows, submitting orders, reinstalling cron,
regenerating broker artifacts, or changing runtime state.

Use it as a deployment gate, not as a substitute for source ownership review or
FR-008 deployment governance.

## Canonical Command

Run from the repo root with the repo virtual environment:

```bash
.venv/bin/python3 scripts/operational_validation.py
```

Expected healthy result after Waves 1-3:

```text
[OPERATIONAL_VALIDATION][PASS] {'pass': 13, 'warn': 0, 'fail': 0}
```

## Current Check Set

The validator currently covers Wave 1 governance checks:

- GitHub workflow YAML parsing.
- GitHub Actions `uses:` references pinned to immutable 40-character SHAs.
- No workflow-scope `contents: write`.
- Dependabot monitoring configured for pip and GitHub Actions without auto-merge.

The validator is intentionally read-only and scoped. It does not currently
execute the Wave 2/3 smoke simulations. For those, run the targeted tests and
shell checks below.

## Deployment Use

Before push:

```bash
.venv/bin/python3 scripts/operational_validation.py
```

After the canonical VM deployment (`./scripts/deploy.sh`):

```bash
./scripts/ops/run_vm_validation.sh
```

From a local shell, run the same VM validation non-interactively with:

```bash
ssh caerus-vm 'cd ~/quant-daily-report && ./scripts/ops/run_vm_validation.sh'
```

When Python behavior changed, add the targeted test slice:

```bash
.venv/bin/python3 -m pytest Tests/test_feedback_loop_artifacts.py Tests/test_portfolio_learning_report.py -q
.venv/bin/python3 -m pytest Tests/test_shadow_daily_wrapper.py Tests/test_execution_pipeline_integration.py -q
.venv/bin/python3 -m pytest Tests/test_execution_pipeline_integration.py Tests/test_precompute_bundle_validation.py -q
```

On the VM, use the project virtual environment explicitly:

```bash
/home/brettolson/.venvs/quant-daily-report/bin/pytest <targeted-test-files> -q
```

`scripts/ops/run_vm_validation.sh` already uses the VM project virtual
environment:

- `/home/brettolson/.venvs/quant-daily-report/bin/python`
- `/home/brettolson/.venvs/quant-daily-report/bin/pytest`

It fails fast if either binary is missing, verifies the v2 deployment
attestation matches the exact full `HEAD`, prints git hash and working-tree
state, runs the read-only operational validator, compiles the FR-069 sleeve
validators, and runs a small no-broker targeted test slice. A raw pull with a
stale marker therefore fails validation. Candidate mode is accepted only from
the internal deployment command while validating a detached worktree; exporting
the candidate SHA on production `main` cannot bypass attestation verification.

For cron-adjacent changes:

```bash
bash -n scripts/cron_precompute.sh
bash -n scripts/cron_execute.sh
```

## Status Semantics

- `PASS`: check is clean.
- `WARN`: operator attention is useful, but the condition does not necessarily
  block deployment.
- `FAIL`: deployment should stop unless an operator explicitly accepts the risk
  and documents the reason.

Examples:

- Mutable GitHub Action tags are `FAIL`.
- Workflow-scope `contents: write` is `FAIL`.
- Missing pip or GitHub Actions Dependabot monitoring is `FAIL`.
- YAML parse errors are `FAIL`.

## Relationship To Runtime Artifacts

Operational validation checks source governance. Runtime health is observed
through artifacts:

- `outputs/workflow/<date>/shadow.json`
- `outputs/workflow/<date>/execution_bundle_validation.json`
- `outputs/workflow/<date>/execution_self_heal.json`
- `outputs/workflow/<date>/precompute_bundle_validation.json`
- `outputs/workflow/<date>/precompute_self_heal.json`

Do not infer runtime recovery health from the governance validator alone.
Inspect the relevant workflow artifacts and logs during the observation window.

## Known Follow-Ups

- Add optional FR-012 cache namespace checks to the validator if cache policy
  should become a formal deployment gate.
- Add optional FR-005 bundle-validator presence checks if recovery integrity
  should become a formal deployment gate.
- Keep dependency lockfile governance out of this validator until FR-010 is
  intentionally promoted.
