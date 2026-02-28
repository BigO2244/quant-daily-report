MODE: HARDEN
PROJECT_TYPE: Python CLI Governance
RISK_TIER: High
OBJECTIVE: Validate aiops starter kit gates and produce a hardened approval pack suitable for signoff.

## Context

This example demonstrates a high-rigor execution path.

## Planned Checks

- Parse required spec headers.
- Run full repository tests with `pytest -q`.
- Confirm harden risk checklist coverage for state mutation, idempotency, external IO, secrets, and rollback.

## Rollback

Revert additive files from this run if verification fails and no partial migrations were applied.
