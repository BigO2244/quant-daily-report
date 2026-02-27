# Brett AI OS Contract

This repository uses a lightweight AI operations workflow to keep implementation work scoped, auditable, and deterministic.

## Core Principles

- Spec-first execution with required headers (`MODE`, `PROJECT_TYPE`, `RISK_TIER`, `OBJECTIVE`).
- Mode-gated verification (`EXPLORE`, `BUILD`, `HARDEN`) before approval.
- Approval Pack generated for every verification run.
- Read-only verification against business logic code. Runtime artifacts may be written only under `reports/ai_runs/`.
- No secret value exposure in logs or reports. Only `SET`/`MISSING` status is allowed.

## Mode Definitions

- `EXPLORE`: Parse and sanity checks only. No test gate required.
- `BUILD`: Parse + full `pytest -q` gate.
- `HARDEN`: Parse + full `pytest -q` + risk checklist and explicit harden status.

## Required Artifacts

Each `aiops verify` run creates a run folder:

- `reports/ai_runs/<run_id>/approval.md`
- `reports/ai_runs/<run_id>/commands.log`
- `reports/ai_runs/<run_id>/spec_parsed.json` (debug aid)

## Approval Expectations

Approval packs must include:

- Summary
- Git Metadata (or unavailable)
- Parsed Spec Headers
- Commands Run + Results
- Gate Outcomes
- Risk Checklist (required in `HARDEN`)
- Next Actions
- Rollback Notes
