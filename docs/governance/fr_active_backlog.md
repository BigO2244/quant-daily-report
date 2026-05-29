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
| FR-002 price cache coverage sidecar | Data / Hydration | `DEPLOYED_OBSERVING` | LOW | Hydration artifact ownership review | observing | Read-only coverage sidecar preview diagnostic added; parquet remains canonical and unmodified. | Stop running `scripts.research.check_price_cache_coverage`; inspect parquet directly. |
| FR-005 self-heal recovery integrity | Wave 3 | `DEPLOYED_OBSERVING` | HIGH | Wave 3 deployment | observing | Execution continues only after full bundle validation. | Revert FR-005 commit; preserve recovery artifacts. |
| FR-012 CI cache namespace isolation | Wave 2 | `DEPLOYED_OBSERVING` | MEDIUM | Wave 2 deployment | observing | Cache keys include `github.repository_id`. | Revert cache namespace commit. |
| FR-014 shadow learning reliability | Shadow / Learning | `DEPLOYED_OBSERVING` | LOW | FR-015 and FR-017 preferred | observing | Read-only learning-health diagnostic added; learning logic and artifacts unchanged. | Stop running `scripts.research.check_shadow_learning_health`; ignore diagnostic output. |
| FR-016 semantic precompute contract validation | Phase 4 | `DEPLOYED_OBSERVING` | LOW | FR-015 | observing | Advisory read-only semantic validator added; not wired into cron or execution gates. | Stop running `scripts.research.check_precompute_semantic_validation`; keep current bundle validation unchanged. |
| FR-021 partial execution state normalization | Phase 4 | `BACKLOG` | HIGH | FR-015, FR-017 | not_started | Execution-adjacent semantic work; defer until lower-risk telemetry is established. | Leave current partial-failure interpretation unchanged. |
| FR-028 shadow execution timing semantics correction candidate | Accounting Correctness / Promotion Analytics | `DEPLOYED_OBSERVING` | HIGH | FR-024, FR-025, FR-026, FR-027, FR-030 | observing | Phase C adds research-only promotion readiness sidecars: `longitudinal_metrics.json`, `stability_surface.json`, `promotion_readiness.json`, and `promotion_readiness.md`; no historical migration, execution, broker, cron, strategy selection, or capital-allocation change. | Revert FR-028 Phase C commit; ignore new dated promotion-readiness sidecars and continue reading existing `shadow_evaluation.json` / `comparison.json`. |
| FR-029 promotion governance hardening for provenance, exposure, and timing confidence | Promotion Governance | `BACKLOG` | MEDIUM | FR-028 | not_started | Future promotion gates should consume provenance, exposure, and timing confidence after accounting semantics are governed. | Revert promotion-readiness checks to existing scorecard criteria. |
| FR-031 execution integrity contract | Execution Integrity | `DEPLOYED_OBSERVING` | HIGH | HOTFIX-2026-05-27, FR-021, broker/order evidence | observing | Additive validator deployed; writes `audit/execution_integrity.json` and compact operator-summary integrity fields without changing order routing. | Revert FR-031 implementation commits; ignore existing audit artifacts. |
| FR-032 execution lifecycle observability hardening | Execution Observability | `READY` | LOW | FR-031, 2026-05-28 recovery evidence | ready_for_low_risk_implementation | Backfill or generate lifecycle timeline artifacts from existing run artifacts when missing; improve latest-run/operator timeline usability without invoking execution. | Revert helper commit; delete only helper-generated timeline artifacts if they were created for validation. |
| FR-033 dashboard/operator asset alignment | Operator Surfaces | `BACKLOG` | LOW | FR-031, dashboard architecture review | not_started | Resolve stale dashboard execution-integrity asset tests or align them to the current dashboard architecture without redesigning dashboard UI. | Revert test/asset alignment commit; preserve dashboard publishing behavior. |
| FR-034 post-submit cash drift reconciliation review | Execution Accounting Review | `READY` | LOW | FR-031, latest paper execution artifacts | ready_for_audit | Determine whether `cash_target_drift` clears after fills/reconciliation, represents expected pending-fill drift, or indicates accounting/reconciliation mismatch. | Docs-only/audit rollback; no runtime behavior should change. |
| FR-035 execution contract documentation hardening | Execution Documentation | `PROMOTION_READY` | LOW | 2026-05-28 recovery, FR-031 | local_validation_pending | Canonicalize execution source, price basis, freshness scope, fail-closed boundaries, and operator provenance semantics. | Revert docs commit; runtime behavior unchanged. |
| FR-036 MCP Phase 7 — research-question capability router | Research MCP | `DEPLOYED_OBSERVING` | LOW | FR-015 / FR-017 / FR-018 / FR-024–FR-030 telemetry, registry semantics | observing | Read-only operator MCP shipped 2026-05-29: 20 tools, 9-capability registry, deterministic regex classifier + artifact pre-check + tool dispatch (OK / NEEDS_DATA / NEEDS_CAPABILITY / UNSUPPORTED_INTENT), operator gateway at `scripts/research_mcp_ask.py`. No transport beyond local stdio, no LLM, no execution-path coupling. | Revert MCP Phase 7 commits; delete `research_registry/research/`, `scripts/research_mcp_ask.{py,sh}`, and `outputs/research_mcp/`. Server reverts to 16-tool Phase 6 surface. |
| FR-036a MCP conformance audit vs frozen semantics layer | Research MCP | `BACKLOG` | LOW | FR-036 deployed, SEM-001..008 frozen | not_started | Produce a clause-by-clause audit doc mapping each frozen SEM contract to the MCP module that implements it (or to the gap). Pure documentation work; no implementation changes. | Delete audit doc only; implementation untouched. |
| FR-036b MCP `attribution_analysis` capability promotion | Research MCP | `BACKLOG` | LOW | FR-036 deployed, `outputs/attribution/` artifacts, existing `AttributionArtifactAdapter` ingestion family | not_started | Add a research_registry/research/attribution.py loader + `attribution_analysis` MCP tool; flip the capability from stub to implemented. | Revert attribution loader + tool commits; capability returns to NEEDS_CAPABILITY. |
| FR-036c MCP `stable_window_evaluation` capability promotion | Research MCP | `BACKLOG` | LOW | FR-036 deployed, `outputs/research/stable_window_evaluation/` artifacts | not_started | Add a loader + tool that summarises the rolling-window Sharpe / drawdown distribution from existing alpha-lab artifacts; flip the capability from stub to implemented. | Revert window loader + tool commits; capability returns to NEEDS_CAPABILITY. |
| FR-036d MCP strategy-aware promotion readiness drill-down | Research MCP | `BACKLOG` | LOW | FR-036 deployed, existing `promotion_readiness` tool | not_started | Accept a `strategy` argument so "Is Orion ready?" returns Orion's panel specifically rather than the generic challenger verdict. | Revert the strategy-arg patch; tool returns to generic mode. |

Recently closed Phase 4 work now lives in `docs/governance/fr_registry.md`:
FR-015, FR-017, FR-018, FR-023, FR-024, FR-025, FR-026, FR-027, and FR-030
are deployed/current. FR-019 and FR-020 are also deployed as docs-only
retention, backup, and validation isolation policies. These remain additive
telemetry, provenance, governance, and research interpretation infrastructure,
not execution, promotion, accounting, or timing semantic changes.

## Phase 4 Priority Order

Phase 4 focuses on artifact governance and operational telemetry. It is
non-trading, non-execution, additive, and low blast radius by default.

| Order | FR | Why This Order |
|---:|---|---|
| 1 | FR-015 | Establishes artifact ownership, taxonomy, and registry semantics before downstream telemetry depends on artifact interpretation. |
| 2 | FR-017 | Gives operators a single health synthesis surface while staying read-only and additive. |
| 3 | FR-018 | Reduces stale `latest` ambiguity after ownership semantics are clear. |
| 4 | FR-023 | Separates canonical docs from generated reports before more operational docs accumulate. |
| 5 | FR-019 | Deployed docs-only policy for cleanup, archive, evidence hold, and backup rules; no cleanup automation. |
| 6 | FR-020 | Deployed docs-only policy for bounded validation output and test/smoke isolation; code-level migration remains future work. |
| 7 | FR-016 | Adds deeper semantic checks after artifact ownership and freshness semantics exist. |
| 8 | FR-021 | Important but execution-adjacent; defer until telemetry and state language are stable. |
| 9 | FR-024 | Establishes explicit performance provenance before additional research metrics are surfaced. |
| 10 | FR-025 | Daily immutable holdings history depends on surface ownership and becomes the base for realized attribution. |
| 11 | FR-026 | Exposure intelligence can then consume stable holdings and provenance. |
| 12 | FR-027 | Regime fragility is more interpretable after exposure and concentration are visible. |
| 13 | FR-030 | Daily packet consumes existing telemetry without changing execution, accounting, timing, dashboard, or promotion behavior. |
| 14 | FR-028 | Accounting semantics are high blast-radius and must wait for provenance, history, observability, and packet interpretation baselines. |
| 15 | FR-029 | Promotion hardening should follow timing semantics review so gates do not encode unstable accounting assumptions. |

FR-022 remains `REVIEWED_DEFERRED` in the registry. Hash enforcement should not
be promoted until dependency baselines, clean installs, and emergency update
procedures are proven.

## Immediate Focus

1. Implement FR-032 first because it is read-only/observability work and
   directly closes the operator visibility gap from the 2026-05-28 recovery:
   existing successful runs may predate timeline artifact generation.
2. Keep FR-031 in `DEPLOYED_OBSERVING` until multiple execution runs show
   integrity status and findings are accurate, visible, and not conflated with
   broker/order-routing outcomes.
3. Observe FR-028 Phase C for deterministic sidecar generation, conservative
   readiness classification, no prior-day mutation, and MCP consumption of
   artifact-backed evidence. Keep FR-029 in Friday-governed Track B until Phase
   C evidence is stable.
4. Treat FR-030 as deployed telemetry consumption, not promotion logic.
5. Focus the next research-operations bottleneck on post-close hydration and
   source readiness, not packet rendering.
6. Observe FR-036 (MCP Phase 7) for stability: deterministic routing,
   honest NEEDS_DATA / NEEDS_CAPABILITY returns, no scope creep into
   transport or autonomous orchestration. Keep MCP work separated from
   FR-028 accounting/timing semantics except for read-only MCP
   consumption of Phase C research artifacts. The four FR-036a..d
   follow-on items are scoped backlog work, not in-flight builds.
7. Use FR-015/017/018/023-027/030 outputs as inputs to future attribution,
   source-readiness, and governance reviews without changing execution paths.

## Roadmap Boundaries

Do not use Phase 4 as a vehicle for microservices, Kubernetes, Airflow, broad
scheduler rewrites, strategy promotion, broker changes, or cron timing changes.
The current bottleneck is operational clarity, not distributed compute scale.
