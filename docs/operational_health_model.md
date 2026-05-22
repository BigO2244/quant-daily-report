---
last_reviewed: 2026-05-22
owner: governance
category: operational_health_model
criticality: high
canonical: true
related_systems: [operations, telemetry, governance, provenance, artifact_registry]
---

# Operational Health Model

## Health Aggregation Philosophy

The operational health layer is a read-only synthesis surface. It interprets
existing artifacts; it does not create, repair, overwrite, or promote source
artifacts.

Principles:

- Synthesis, not mutation.
- Observability, not orchestration.
- Provenance-aware interpretation.
- Confidence-aware interpretation.
- Additive operational overlays only.
- Preserve canonical truth surfaces.
- Make degraded and uncertain states visible.
- Never convert stale source evidence into fresh-looking summaries.

The health layer must not run precompute, submit orders, refresh dashboard data,
hydrate prices, regenerate shadow artifacts, install cron, deploy source, or
mutate production runtime state.

## Health Domains

| Domain | Purpose | Representative Evidence | Execution Coupling |
|---|---|---|---|
| Execution health | Explain execution readiness, execution outcome, and fail-closed recovery state. | Precompute bundle validation, execution results, execution workflow status. | Read-only interpretation only. |
| Reconciliation health | Interpret broker, planned, pretrade, posttrade, and live-vs-shadow alignment. | `outputs/broker/recon_*`, broker snapshots, reconciliation reports. | May describe gates but must not gate itself. |
| Hydration health | Interpret data freshness and price/cache coverage. | `outputs/price_hydration/<date>/status.json`, cache coverage metadata. | No hydration side effects. |
| Shadow telemetry health | Interpret non-blocking shadow generation and challenger diagnostics. | `outputs/workflow/<date>/shadow*.json`, `outputs/shadow_candidates/<date>/*`. | Non-blocking; never execution-critical by itself. |
| Freshness health | Interpret dated artifacts, latest publications, publication lag, and stale state. | Freshness manifests, source trade dates, latest pointers. | Advisory only. |
| Provenance confidence health | Interpret whether source, producer, confidence, and truth surface are explicit. | Artifact registry, ownership matrix, provenance metadata. | Advisory; does not rewrite confidence. |
| Publication health | Interpret whether dated source artifacts were published to convenience surfaces. | `latest` artifacts, publication status, source path metadata. | Display/advisory only. |
| Artifact completeness health | Interpret missing, partial, malformed, or internally inconsistent artifact sets. | Expected artifact family lists and validation outputs. | Advisory unless a source execution gate already owns the check. |
| Research continuity health | Interpret attribution, exposure, regime, learning, and audit artifact continuity. | Attribution outputs, exposure flags, regime reports, audit summaries. | Research-only; not broker-authoritative. |

## Health Severity Semantics

| Status | Meaning | Operator Interpretation |
|---|---|---|
| `HEALTHY` | Required evidence for the domain is present, fresh enough, internally consistent, and confidence-appropriate. | Trust within the domain's truth-surface limits. |
| `DEGRADED` | Evidence exists but indicates drift, warning, failed optional diagnostic, recovery, or non-blocking limitation. | Review before relying on downstream interpretation. Not automatically unsafe. |
| `STALE` | Evidence exists but source date, data-through date, or publication timestamp is older than expected. | Use as historical context only unless stale condition is explicitly acceptable. |
| `PARTIAL` | Some expected artifacts exist and others are missing, malformed, or suppressed. | Interpret only the available subset; avoid summary-level certainty. |
| `UNKNOWN` | Evidence is absent, unreadable, ambiguous, or not governed by known ownership semantics. | Do not infer healthy state. |
| `BROKEN_CHAIN` | A continuity chain has a missing, broken, or unverifiable prior link. | Block promotion-style interpretation until repaired and governed; preserve evidence. |

Overall health should be a synthesis, not a hidden numeric score. A single
`DEGRADED` shadow diagnostic should not imply execution failure. A single
`UNKNOWN` broker snapshot should prevent broker-authoritative claims.

## Confidence-Aware Interpretation

Health and confidence are related but distinct.

| Condition | Interpretation |
|---|---|
| LOW confidence with otherwise present artifacts | The artifact may be available and fresh, but its claim should carry limited evidentiary weight. |
| DEGRADED health with HIGH-confidence source | The source is trustworthy enough to believe the degraded condition. |
| STALE artifact | The artifact exists, but timeliness is insufficient for current-state claims. |
| Missing artifact | No current evidence exists; classify as UNKNOWN or domain-specific failure. |
| Shadow research artifact | May be useful for challenger interpretation but is not canonical execution state. |
| Advisory surface | May guide human review but must not trigger workflows or execution. |
| Authoritative surface | Can support operational claims only within its date, freshness, and validation boundaries. |

Examples:

- LOW confidence shadow NAV is not the same as failed shadow generation.
- Stale `latest` is not the same as missing dated source artifacts.
- Clean broker reconciliation is stronger evidence than a generated dashboard
  panel derived from it.
- A partial Orion telemetry packet should be visible without implying Polaris
  execution degradation.

## Freshness Interpretation Rules

Freshness should be evaluated against the artifact family and expected workflow
time.

Rules:

1. Prefer dated source artifacts over mutable `latest` publications.
2. Treat `latest` without source date and publication metadata as UNKNOWN.
3. Publication lag is acceptable only when the source artifact is dated, intact,
   and the lag is documented.
4. A current publication timestamp does not prove current data.
5. A current trade date does not prove complete artifact generation.
6. Suppressed publication should be explicit and should not delete prior latest
   files.
7. Freshness summaries must preserve the source artifact's freshness state.

Acceptable lag examples:

| Artifact Family | Typical Acceptable Lag | Notes |
|---|---|---|
| Precompute bundle | Same execution trade date before order phase. | Late or missing bundle is execution-relevant. |
| Execution confirmation | Same trading session. | Must reference source run id. |
| Hydration status | Post-close for expected completed trading day. | Lag affects shadow/reporting interpretation. |
| Shadow dated artifacts | Same evaluation trade date or explicit fallback reason. | Non-blocking for execution. |
| Shadow latest publication | Same source trade date as dated shadow artifact. | Latest alone is insufficient. |
| Dashboard payload | Current enough for displayed report date; stale sections visible. | Display health, not execution truth. |
| Weekly research packet | Weekly review window. | Research cadence, not runtime freshness. |

## Operator-Facing Synthesis Principles

- Concise over verbose.
- No false certainty.
- Degraded visibility over silent failure.
- Preserve nuance between execution, shadow, research, and display health.
- Surface uncertainty explicitly.
- Include evidence paths or artifact families behind every status.
- Do not collapse all warnings into a single red/green score.
- Keep operator action language advisory unless an existing runtime gate owns
  the decision.

Suggested operator summary shape:

```text
Overall: DEGRADED
Trading-critical blockers: 0
Primary issue: Shadow latest publication stale; dated shadow artifacts available.
Confidence note: Operational shadow NAV remains LOW confidence pending timing semantics governance.
Action: Use dated shadow artifact for research review; do not use latest for promotion evidence.
```

## Future Additive JSON Schema Proposal

This schema is a proposal for a future read-only aggregation artifact. FR-017
does not implement a producer.

```json
{
  "schema_version": 1,
  "generated_at": "2026-05-22T22:00:00Z",
  "trade_date": "2026-05-22",
  "read_only": true,
  "overall_status": "DEGRADED",
  "domains": [
    {
      "domain": "shadow_telemetry_health",
      "status": "PARTIAL",
      "confidence": "LOW",
      "freshness": {
        "status": "STALE",
        "source_trade_date": "2026-05-21",
        "expected_trade_date": "2026-05-22"
      },
      "producer": "scripts/run_shadow_candidates_daily.sh",
      "trade_date": "2026-05-22",
      "evidence": [
        "outputs/workflow/2026-05-22/shadow_generate.json",
        "outputs/shadow_candidates/latest/comparison.json"
      ],
      "interpretation": "Latest shadow publication is stale; use dated artifacts for review."
    }
  ]
}
```

## Future Integration Guidance

Future integrations should remain additive and read-only unless a later FR
explicitly promotes a behavior.

Integration points:

- FR-018 freshness manifests: provide source trade date, publication timestamp,
  and stale/suppressed status.
- FR-024 provenance enforcement: supply truth surface, execution realism, and
  confidence metadata for performance claims.
- MCP retrieval layers: query health status with provenance, not as an action
  trigger.
- Research packets: use health synthesis to explain evidence limitations.
- Dashboard surfaces: display health summaries only after canonical evidence and
  stale/degraded states remain visible.

Non-goals:

- No cron integration.
- No execution gating.
- No broker calls.
- No self-healing.
- No artifact repair.
- No automatic promotion decisions.
