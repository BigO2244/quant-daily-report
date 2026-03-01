# Release Guardrails (Capital Safety) v0.1

## Purpose

Prevent accidental modification of critical trading execution pathways.

This contract defines protected surfaces.

---

## Protected File Categories

Changes to the following require explicit declaration in spec:

- Broker adapters
- Order execution logic
- Position reconciliation logic
- Capital allocation limits
- Cash management logic
- Execution scheduling logic
- Risk kill-switch logic

If modified, spec must include:

FILES:
modify:
- <explicit file paths>

Undeclared modifications to protected files violate contract.

---

## Deployment Candidate Definition

A RUN_ID may be considered deployment-ready only if:

- Strategy Change Protocol satisfied
- Evidence schema complete
- Diff reviewed
- No undeclared protected file modifications
- Exit code = 0

---

## Personal Approval Rule (Solo Operator)

Before deploying capital-impacting changes:

"I confirm this RUN_ID has passed verify, tests, audit review, and diff inspection."

Record approval in:
reports/ai_runs/<RUN_ID>/approval.md

---

## Separation Principle

AIOPS authorizes change.
Capital deployment is a separate conscious act.

---

## Version

v0.1