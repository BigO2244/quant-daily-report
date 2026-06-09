---
last_reviewed: 2026-05-22
owner: governance
category: artifact_registry
criticality: high
canonical: true
related_systems: [governance, telemetry, provenance, attribution, dashboard, shadow]
---

# Artifact Registry

## Registry Philosophy

The artifact registry is an additive provenance layer for Caerus operating and
research artifacts. It exists to make ownership, freshness, trust surface, and
consumer expectations explicit before additional telemetry, attribution,
freshness enforcement, or MCP retrieval layers depend on those artifacts.

This registry is operator-readable first. It does not change trading behavior,
broker access, strategy selection, cron timing, dashboard publishing, artifact
producers, or execution gates.

Principles:

- Additive provenance layer, not a runtime control plane.
- No runtime mutation.
- Provenance before automation.
- Truth surfaces must be explicit.
- Dated artifacts are stronger evidence than convenience `latest` files.
- Generated summaries must not silently become source of truth.
- Missing or stale metadata means unknown, not healthy.

## Artifact Classification Model

| Category | Meaning | Examples | Trust Boundary |
|---|---|---|---|
| `canonical_runtime_artifact` | Source of truth for a runtime decision or durable operational state. | Precompute contract, execution payload, broker snapshot, pretrade reconciliation. | May gate or explain execution. Requires strict provenance and validation. |
| `execution_critical_artifact` | Artifact used directly in order submission, fail-closed recovery, or execution handoff. | `outputs/precompute/<date>/planned_execution_payload.json`, `outputs/broker/recon_pretrade_<date>.json`. | Highest runtime caution. New telemetry must not mutate it. |
| `additive_telemetry_artifact` | Read-only health, provenance, freshness, or diagnostic evidence. | Workflow status, hydration status, operational health summaries. | Supports interpretation; should not alter runtime behavior. |
| `research_only_artifact` | Research, attribution, backtest, challenger, or analysis artifact outside execution. | Attribution outputs, alpha lab reports, performance veracity audits. | Evidence for research decisions; not broker-authoritative. |
| `convenience_latest_publication` | Mutable pointer/copy to recent dated artifacts. | `outputs/latest_run.json`, `outputs/shadow_candidates/latest/*`, `*_latest.json`. | Never proof of freshness by itself. Must resolve to dated source metadata. |
| `generated_documentation_artifact` | Human-readable generated report or summary. | `comparison.md`, CIO-style reports, generated dashboard summaries. | Review surface only unless explicitly designated canonical. |
| `advisory_artifact` | Recommendation, warning, score, or review artifact that informs humans. | Promotion readiness audits, factor risk flags, operational recommendations. | Must not execute actions automatically. |

## Ownership Matrix

This registry is the canonical prose layer. The quick-reference table lives in
`docs/artifact_ownership_matrix.md`.

| Artifact Family | Producer | Canonical Path | Consumers | Freshness Expectations | Criticality | Confidence Semantics |
|---|---|---|---|---|---|---|
| Execution run roots | `scripts.run_precomputed_alpaca_execution`, execution runtime | `outputs/runs/<RUN_ID>/*` | Confirmation email, posttrade review, operators | Run id, stage, and trade date must align with execution workflow evidence. | HIGH | HIGH only when run id, precompute source, execution status, and broker/reconciliation evidence align. |
| Trading day summary | Daily report / confirmation flow | `outputs/trading_day_summary.json` | Operators, reports, historical review | Must identify trade date and source run. Latest-only summary is insufficient for audit. | MEDIUM | MEDIUM when source run is clear; LOW if source run or trade date is ambiguous. |
| Shadow candidate dated artifacts | `research.shadow_tracking.run`, shadow wrapper | `outputs/shadow_candidates/YYYY-MM-DD/*` | Shadow CIO report, promotion review, operators | File date must match evaluation trade date or carry fallback/stale reason. | MEDIUM | MEDIUM for dated complete artifacts; LOW when repaired/backfilled/stale or timing assumptions remain unresolved. |
| Shadow latest publication | Shadow wrapper and hydration refresh tooling | `outputs/shadow_candidates/latest/*` | Dashboard, operators, health checks | Must identify source dated artifact and publication time before being treated as current. | LOW | LOW unless paired with freshness manifest and source date. |
| VIX/regime artifacts | Regime classification and VIX audit tooling | `outputs/vix_regime/*`, `outputs/regime_*/*` | Regime diagnostics, allocation review, research | Must identify data-through date and regime source. | MEDIUM | MEDIUM when date and source inputs are explicit; LOW if cached/stale inputs are unknown. |
| Research outputs | Research and analysis scripts | `outputs/research/*`, `research/analysis/*` | Research review, future strategy work | Experiment date and data-through date should be explicit. | LOW | LOW to MEDIUM depending on provenance, PIT safety, and reproducibility. |
| Dashboard generated artifacts | `scripts/refresh_quant_dashboard.py`, dashboard builder | `web/dashboard*/dashboard_data.json`, deployed dashboard payloads | Dashboard UI, operators | Must expose source trust, stale sections, and broker-authoritative fields separately. | MEDIUM | Derived confidence only; cannot exceed source artifacts. |
| Latest-style convenience artifacts | Pointer helpers and runtime publishers | `outputs/latest_run.json`, `outputs/latest.json`, `*_latest.json` | Legacy tooling, dashboards, operators | Requires `source_trade_date`, `published_at`, producer, and source path to be trusted. | LOW | UNKNOWN without freshness metadata; LOW if stale; MEDIUM only as a pointer to validated dated source. |
| Hydration status artifacts | `scripts.hydrate_price_cache_only` | `outputs/price_hydration/YYYY-MM-DD/status.json` | Shadow health, CIO report, operators | Must report cache max date, coverage, and refresh status for expected completed trading day. | MEDIUM | MEDIUM when coverage is explicit; LOW when refresh failed or max date lags. |
| Reconciliation artifacts | `reconciliation.py`, broker/recon scripts | `outputs/broker/recon_*_<date>.json`, `outputs/reconciliation/*` | Execution gates, dashboard, operators | Must match trade date and reconciliation phase. | HIGH | HIGH for broker-backed pre/posttrade recon with clean status; LOW on drift or missing broker evidence. |
| Post-sell rebudget artifacts | Execution runtime | `outputs/runs/<RUN_ID>/broker/post_sell_rebudget_<date>.json` | Operators, execution integrity review, target-attainment review | Required when sell orders are present; must match run id/trade date and report confirmed proceeds, refreshed cash/buying power, final buy budget, and reason codes. | HIGH | HIGH when produced by the run root and broker/account state is refreshed; LOW if missing for a sell-leg run or sell confirmation is unresolved. |
| Operational drag artifacts | `research.operational_drag` | `outputs/operational_drag/<date>/*` | Research review packet, operators, deployment-integrity analysis | Must expose intended, actual, benchmark, alignment date, source paths, source dates, stale components, blocking components, decision-grade status, and confidence. | MEDIUM | MEDIUM when current-date aligned and decision-grade; LOW when current-date unavailable or material blockers remain. |
| Target-attainment artifacts | `research.target_attainment` | `outputs/target_attainment/<date>/target_attainment_<date>.json` | Operators, research review packet, deployment-integrity analysis | Must match trade date and compare target portfolio, risk-adjusted target, intended orders, executed orders, broker holdings, and actual portfolio. | MEDIUM | HIGH for broker-backed current-date inputs with clean source diagnostics; MEDIUM when derived from deterministic target/order price basis; LOW when target or actual portfolio sources are missing. |
| Learning and feedback artifacts | Feedback loop and portfolio learning scripts | `outputs/portfolio_learning/*`, `outputs/feedback*`, `outputs/shadow_candidates/*/feedback*` | Weekly review, research learning, operators | Must identify source period and input performance artifacts. | LOW | Advisory; confidence inherits from source attribution/performance inputs. |
| Performance / attribution artifacts | Attribution and audit scripts | `outputs/attribution/*`, `outputs/audits/*` | CIO review, promotion governance, research | Must identify truth surface, data-through date, confidence, and limitations. | MEDIUM | Cannot exceed source surface and lineage confidence. |
| Broker snapshots | Alpaca snapshot/export tooling | `outputs/broker/*`, `outputs/broker_snapshot/*` | Dashboard, reconciliation, operators | Must include capture time and broker account source. | HIGH | HIGH when broker-authoritative and current; UNKNOWN if missing or source unavailable. |

## Freshness Semantics

Freshness requires both a market date interpretation and a publication-time
interpretation.

Required concepts:

- `produced_at`: UTC timestamp when the artifact was written.
- `published_at`: UTC timestamp when an artifact was promoted/copied to a
  publication surface, if different from production.
- `trade_date`: market date the artifact describes.
- `data_through_date`: last market-data date reflected in the artifact.
- `source_artifact`: dated source for a convenience/latest publication.
- `freshness_sla`: expected maximum lag for the artifact family.

Freshness status vocabulary:

| Status | Meaning | Operator Interpretation |
|---|---|---|
| `fresh` | Artifact date and expected completed trading date align. | Trust within the artifact category and source confidence limits. |
| `stale` | Artifact exists but is older than expected. | Use as historical context only. |
| `partial` | Some expected artifacts exist, others are missing. | Review missing items before trusting the workflow. |
| `suppressed` | Publication intentionally skipped due to recovery or degraded mode. | Prior latest artifacts may be stale by design. |
| `missing` | Expected artifact does not exist. | Treat state as unknown or failed depending on category. |
| `unknown` | Metadata is absent, unreadable, or insufficient. | Do not infer healthy state. |

Freshness interpretation rules:

1. `latest` is a convenience publication, not a freshness guarantee.
2. Dated artifacts are the audit source unless superseded by an explicit
   governance process.
3. Publication timestamp does not prove market-data freshness.
4. Trade-date match does not prove complete artifact generation.
5. Missing freshness metadata should downgrade interpretation to UNKNOWN.
6. Stale state should be visible through metadata, not hidden by deletion.

## Provenance / Truth Surface Definitions

| Truth Surface | Definition | Examples | Interpretation Rule |
|---|---|---|---|
| Authoritative source | Primary artifact or external source used as ground truth for a domain. | Broker account snapshot, pretrade reconciliation, precompute contract. | Highest confidence only when source, timestamp, and validation state are explicit. |
| Derived summary | Artifact computed from one or more authoritative or research inputs. | Dashboard payload, trading day summary, attribution report. | Confidence cannot exceed input confidence. |
| Advisory overlay | Human or machine-readable warning, recommendation, or diagnostic. | Promotion audit, factor risk flags, health recommendations. | Inform decisions; never execute automatically. |
| Convenience publication | Mutable latest copy or pointer to dated artifacts. | `latest_run.json`, `shadow_candidates/latest/*`. | Must resolve to dated source before trusted. |
| Research candidate | Candidate strategy, backtest, shadow analysis, or experimental evidence. | Orion/Lyra artifacts, alpha lab outputs, timing-corrected research surfaces. | Research-only unless promoted by governance. |
| Shadow interpretation | Model-computed shadow evidence with no broker fills. | Shadow performance, challenger comparison, shadow attribution. | Never broker-authoritative; label timing and execution assumptions. |

## Confidence Semantics

These registry confidence labels are operator-facing. They are intentionally
coarser than the formal research registry confidence lattice.

| Confidence | Conditions |
|---|---|
| HIGH | Canonical source is available, current, validated, date-aligned, and backed by broker or execution-critical evidence where applicable. No unresolved drift, stale, or repair condition is present. |
| MEDIUM | Artifact is dated, complete, deterministic, and traceable, but derived, diagnostic, research-only, or not broker-authoritative. Known limitations are documented. |
| LOW | Artifact is stale, repaired, backfilled, inferred, shadow-only with unresolved timing assumptions, missing some provenance, or dependent on low-confidence inputs. |
| UNKNOWN | Artifact is absent, malformed, lacks required metadata, cannot be date-aligned, or source ownership is unclear. |

Operational downgrade examples:

- Missing dated source behind `latest` -> UNKNOWN or LOW.
- Broker snapshot missing -> UNKNOWN.
- Reconciliation drift -> LOW until resolved.
- Shadow timing semantics unresolved -> LOW for operational shadow performance
  claims.
- Backfilled/repaired artifact without explicit repair metadata -> LOW.
- Generated report without source artifact references -> LOW.

## Optional Future Metadata Schema

Future producers may emit this lightweight metadata block beside artifact
families. This is a proposal only; no runtime producer is retrofitted by FR-015.

```json
{
  "schema_version": 1,
  "artifact_family": "shadow_candidate_dated",
  "producer": "research.shadow_tracking.run",
  "produced_at": "2026-05-22T21:05:00Z",
  "published_at": "2026-05-22T21:06:00Z",
  "trade_date": "2026-05-22",
  "data_through_date": "2026-05-22",
  "confidence": "MEDIUM",
  "source_surface": "OPERATIONAL_SHADOW_NAV",
  "freshness_sla": "same_completed_trading_day",
  "source_artifacts": [
    "outputs/shadow_candidates/2026-05-22/shadow_performance.json"
  ],
  "notes": "Shadow-only; not broker-authoritative."
}
```

## MCP Compatibility

Future MCP and research retrieval layers should consume this registry as a
semantic map, not as permission to mutate artifacts.

MCP-compatible behavior:

- Read artifact family ownership and confidence semantics.
- Resolve latest publications to dated source artifacts.
- Preserve provenance metadata in responses.
- Report freshness, source surface, and confidence next to claims.
- Refuse to treat generated summaries as authoritative unless the registry says
  they are canonical.
- Distinguish research candidates, shadow interpretation, and broker
  authoritative evidence.

MCP must not:

- Rewrite artifact history.
- Promote confidence silently.
- Trigger workflows.
- Access broker credentials.
- Repair artifacts automatically.
- Replace FR governance.

## Validation Boundary

FR-015 is documentation and governance infrastructure only. It introduces no
runtime behavior changes, no workflow execution, no artifact mutation, and no
producer retrofits.
