# Research Integrity Hardening Plan

## Purpose

This plan separates immediate additive research infrastructure from
FR-governed accounting semantics work. It is intended to preserve auditability
while improving attribution, exposure transparency, and promotion readiness.

## Track A: Immediate Additive Infrastructure

Track A is safe to execute without changing strategy logic, order submission,
cron, broker state, or published accounting semantics.

| FR | Scope | Deployment Safety | Output Surface |
|---|---|---|---|
| FR-024 | NAV surface registry and provenance labels | Immediate, additive | `outputs/attribution/<date>/nav_surface_registry.json` |
| FR-025 | Immutable daily holdings and weights snapshots | Immediate, additive | `outputs/portfolio_history/<date>/` |
| FR-026 | Exposure and concentration risk observability | Immediate, additive | `exposure_summary.json`, `factor_risk_flags.json`, `concentration_monitor.json` |
| FR-027 | Regime decomposition and fragility reporting | Immediate, additive | `regime_performance_breakdown.json`, `regime_fragility_report.json` |

Track A artifacts must label:

- `nav_surface_type`
- `confidence_level`
- `execution_realism`
- `point_in_time_validity`

No Track A artifact is allowed to reinterpret historical returns or alter
execution behavior.

## Track B: FR-Governed Accounting Semantics

Track B covers timing and accounting changes that may alter reported
performance interpretation.

| FR | Scope | Governance Requirement |
|---|---|---|
| FR-028 | Prior-day weights against next-session returns candidate | Friday maintenance, before/after comparison, rollback plan |
| FR-029 | Promotion gates for provenance, exposure, and timing confidence | Friday maintenance after FR-028 settles |

FR-028 must persist the following metadata before any promotion:

- `signal_as_of_timestamp`
- `price_as_of_timestamp`
- `execution_assumption`
- `execution_surface`

FR-028 must not migrate historical chains automatically. It should produce a
parallel comparison artifact first, then require operator review before any
reported-performance semantics change.

## Rollout Order

1. Publish FR-024 provenance registry artifacts.
2. Publish FR-025 immutable daily holdings/weights snapshots.
3. Publish FR-026 exposure intelligence artifacts.
4. Publish FR-027 regime fragility artifacts.
5. Design FR-028 before/after timing comparison under Friday governance.
6. Only after FR-028 is reviewed, design FR-029 promotion-readiness gates.

## Rollback

Track A rollback is source-level and artifact-interpretation only: stop writing
or reading the additive artifacts and preserve existing generated outputs as
evidence.

Track B rollback must preserve current published chains and remove only the
candidate comparison reader or governance gate that was added. Do not delete
runtime evidence as a rollback shortcut.
