# Dependency Governance

## Purpose

This document records the FR-010 dependency governance baseline for Caerus.
Phase 1 creates deterministic dependency reference files without changing
runtime imports, trading logic, workflow behavior, or deployment automation.

## Phase 1 Scope

Implemented in Phase 1:

- Preserve the original abstract dependency intent in `requirements.in`.
- Preserve the original research-agent dependency intent in
  `quant_research_agent/requirements.in`.
- Pin `requirements.txt` to exact versions observed in the VM production
  environment.
- Pin `quant_research_agent/requirements.txt` to exact versions where an
  installed baseline exists.
- Add `constraints.txt` as an advisory union of the VM runtime baseline and
  local research-agent-only baseline.

Not implemented in Phase 1:

- No dependency upgrades or downgrades were installed.
- No hash enforcement was added.
- No pip strict mode was enabled.
- No `pip-audit` gate was added.
- No workflow install command was changed to enforce constraints.
- No runtime import or execution path was changed.

## Baseline Sources

Primary runtime baseline:

- Source: scheduler VM `~/quant-daily-report` virtual environment
- Captured: 2026-05-12
- Applies to: root `requirements.txt`

Research-agent-only baseline:

- Source: local development `.venv`
- Captured: 2026-05-12
- Applies to packages not installed in the VM baseline, such as `anthropic`,
  `arxiv`, `feedparser`, `fredapi`, and `python-dotenv`.

## Known Phase 1 Exception

`APScheduler>=3.10.0` existed in the previous
`quant_research_agent/requirements.txt`, but `APScheduler` was not installed in
the local development venv or the VM production venv at the time of the Phase 1
baseline capture.

To avoid inventing a version or silently removing the declared dependency, Phase
1 leaves this line as a documented exception:

```text
APScheduler>=3.10.0
```

Resolve this before Phase 2 hash enforcement by confirming whether the research
agent still requires APScheduler and then pinning an explicitly reviewed version
or removing the dependency in a separate, intentional change.

## Operating Rules

- Do not upgrade dependencies as part of governance-only changes.
- Do not change workflow install behavior without a rollback plan.
- Dependabot is advisory-first. It may open review PRs for pip and GitHub
  Actions updates, but unattended auto-merge is not part of the approved
  operating model.
- Treat local and VM dependency drift as an operational finding, not an automatic
  reason to install packages.
- Keep `constraints.txt` advisory until Phase 2 explicitly enables enforcement.
- If a package must be changed, create a separate dependency-update change with
  targeted tests and VM validation.

## Phase 2 Candidates

Remaining FR-010 work:

- Compile locked requirements with hashes.
- Add pip hash verification where operationally safe.
- Add clean-environment install validation.
- Decide whether workflow installs should use `constraints.txt`.
- Add `pip-audit` as an advisory check first.
- Define a dependency review cadence and emergency update path.
