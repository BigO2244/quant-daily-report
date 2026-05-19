# FR Active Backlog

## Purpose

This document is the operator-readable roadmap for active Caerus Friday
Refactor (FR) work. It contains only work that is not fully closed:

- `BACKLOG`
- `READY`
- `READY_VALIDATED`
- `IN_PROGRESS`
- `PROMOTION_READY`
- `DEPLOYED_OBSERVING`

Fully deployed history and reviewed deferred items belong in
`docs/governance/fr_registry.md`. Governance methodology belongs in
`docs/governance/fr_governance_model.md`.

## Current Active Summary

| FR | Phase | Status | Blast Radius | Dependencies | Observation Status | Current State | Rollback Reference |
|---|---|---|---|---|---|---|---|
| FR-001 shadow wrapper decomposition | Wave 2 | `DEPLOYED_OBSERVING` | MEDIUM | Wave 2 deployment | observing | Shadow remains non-blocking and writes step status artifacts. | Revert wrapper decomposition commit. |
| FR-002 price cache coverage sidecar | Data / Hydration | `BACKLOG` | MEDIUM | Hydration artifact ownership review | not_started | Advisory sidecar proposal; parquet remains canonical. | Stop writing/reading sidecar and inspect parquet directly. |
| FR-005 self-heal recovery integrity | Wave 3 | `DEPLOYED_OBSERVING` | HIGH | Wave 3 deployment | observing | Execution continues only after full bundle validation. | Revert FR-005 commit; preserve recovery artifacts. |
| FR-012 CI cache namespace isolation | Wave 2 | `DEPLOYED_OBSERVING` | MEDIUM | Wave 2 deployment | observing | Cache keys include `github.repository_id`. | Revert cache namespace commit. |
| FR-014 shadow learning reliability | Shadow / Learning | `BACKLOG` | MEDIUM | FR-015 and FR-017 preferred | not_started | Useful follow-up once artifact taxonomy and health aggregation are clearer. | Keep current shadow learning behavior. |
| FR-015 artifact registry and ownership matrix | Phase 4 | `IN_PROGRESS` | LOW | None | not_required | Foundation documented in `docs/artifact_governance.md`; static registry structure still to formalize. | Ignore new governance docs or revert docs-only change. |
| FR-016 semantic precompute contract validation | Phase 4 | `BACKLOG` | MEDIUM | FR-015 | not_started | Future lightweight validation beyond existence and parseability. | Leave current bundle validation unchanged. |
| FR-017 operational health aggregator | Phase 4 | `IN_PROGRESS` | LOW | FR-015 preferred | not_required | Design documented in `docs/operational_health_aggregator.md`; no runtime producer changed. | Ignore design doc or revert docs-only change. |
| FR-018 latest publication freshness manifest | Phase 4 | `IN_PROGRESS` | LOW | FR-015 preferred | not_required | Freshness semantics documented as examples only; existing artifacts unchanged. | Ignore documented semantics until implemented. |
| FR-019 runtime artifact retention and backup policy | Phase 4 | `BACKLOG` | LOW | FR-015, FR-018 | not_started | Policy work only; no cleanup automation selected. | Leave current retention behavior unchanged. |
| FR-020 read-only validation isolation | Phase 4 | `BACKLOG` | MEDIUM | FR-015 | not_started | Future isolation of tests and smoke flows from repo-level runtime outputs. | Leave current validation layout unchanged. |
| FR-021 partial execution state normalization | Phase 4 | `BACKLOG` | HIGH | FR-015, FR-017 | not_started | Execution-adjacent semantic work; defer until lower-risk telemetry is established. | Leave current partial-failure interpretation unchanged. |
| FR-023 documentation and generated artifact separation | Phase 4 | `IN_PROGRESS` | LOW | FR-015 preferred | not_required | Taxonomy documented in `docs/documentation_taxonomy.md`; no large file moves yet. | Ignore taxonomy proposal or revert docs-only change. |

## Phase 4 Priority Order

Phase 4 focuses on artifact governance and operational telemetry. It is
non-trading, non-execution, additive, and low blast radius by default.

| Order | FR | Why This Order |
|---:|---|---|
| 1 | FR-015 | Establishes artifact ownership, taxonomy, and registry semantics before downstream telemetry depends on artifact interpretation. |
| 2 | FR-017 | Gives operators a single health synthesis surface while staying read-only and additive. |
| 3 | FR-018 | Reduces stale `latest` ambiguity after ownership semantics are clear. |
| 4 | FR-023 | Separates canonical docs from generated reports before more operational docs accumulate. |
| 5 | FR-019 | Uses the taxonomy and freshness model before defining cleanup, archive, and safe-to-delete rules. |
| 6 | FR-020 | Prevents validation/test pollution after runtime ownership boundaries are documented. |
| 7 | FR-016 | Adds deeper semantic checks after artifact ownership and freshness semantics exist. |
| 8 | FR-021 | Important but execution-adjacent; defer until telemetry and state language are stable. |

FR-022 remains `REVIEWED_DEFERRED` in the registry. Hash enforcement should not
be promoted until dependency baselines, clean installs, and emergency update
procedures are proven.

## Immediate Focus

1. Complete FR-015 by turning the documented taxonomy into a lightweight static
   artifact registry structure.
2. Keep FR-017 as read-only synthesis; do not let it mutate execution or
   runtime state.
3. Add FR-018 freshness manifests incrementally to `latest`-style artifacts only
   after trust semantics are reviewed.
4. Use FR-023 to reduce documentation entropy without large file moves in the
   same change as runtime producers.

## Roadmap Boundaries

Do not use Phase 4 as a vehicle for microservices, Kubernetes, Airflow, broad
scheduler rewrites, strategy promotion, broker changes, or cron timing changes.
The current bottleneck is operational clarity, not distributed compute scale.
