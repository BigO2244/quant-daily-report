# Reconciliation And Reporting Audit

Role: Reconciliation and reporting auditor

## Findings

The reported combination was internally contradictory:

- `Filled: 0`
- unresolved partial-fill wording
- `NOT_COMPARABLE`
- `EXECUTED`
- no halt/skip reason
- planned SPG/UNH buys omitted

Accepted-only sell activity should not produce a clean execution label when required buy lifecycle phases are omitted. A missing buy phase requires a reason such as `sell_phase_timeout` or `sell_state_unresolved`.

## Local Code State

`scripts/run_precomputed_alpaca_execution.py` already downgrades paper summaries with pending buy legs to partial when summary fields expose the omission. The hotfix strengthens upstream lifecycle state so unresolved sell terminality emits an explicit reason before reporting consumes it.

