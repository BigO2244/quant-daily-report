---
last_reviewed: 2026-05-22
owner: governance
category: freshness_semantics
criticality: high
canonical: true
related_systems: [governance, telemetry, artifact_registry, operational_health, mcp]
---

# Freshness Semantics

## Freshness Philosophy

Freshness is interpretive metadata. It tells an operator whether an artifact is
current enough for the claim being made. Freshness is not the same as existence,
validity, confidence, or authority.

Principles:

- Publication trust is stronger than file existence.
- Stale does not mean missing.
- Latest does not mean authoritative.
- Convenience surfaces are not canonical sources.
- Freshness is part of provenance.
- Freshness summaries must preserve stale state rather than hide it.
- Freshness metadata should support interpretation, not mutate runtime state.

An artifact can exist, parse cleanly, and still be stale. A `latest` file can
have a current modification time while pointing to old underlying data. A
research artifact can be stale for live operations but still useful for a
weekly research review.

## Freshness Vocabulary

| Status | Operational Meaning | Operator Interpretation |
|---|---|---|
| `FRESH` | Artifact source date, data-through date, publication timestamp, and expected date align for the artifact family. | Current enough for its category and confidence limits. |
| `ACCEPTABLY_LAGGED` | Artifact is behind the expected date/time but within a documented SLA or review cadence. | Usable with the lag disclosed. |
| `STALE` | Artifact exists but is older than the expected source date, data-through date, or publication window. | Historical context only unless explicitly acceptable. |
| `MISSING` | Expected artifact does not exist at the expected location or publication surface. | No evidence is available from that artifact family. |
| `UNKNOWN` | Metadata is insufficient to determine freshness. | Do not infer freshness from existence or mtime alone. |
| `PARTIAL` | Some artifacts in a family are fresh and others are stale, missing, or unknown. | Use only the fresh subset; avoid summary-level certainty. |
| `SUPERSEDED` | Artifact was valid at one time but has been replaced by a newer dated or governed publication. | Preserve for audit; do not treat as current. |

## Publication Semantics

| Field | Meaning | Common Confusion |
|---|---|---|
| `produced_at` | UTC timestamp when the artifact content was created. | Does not prove publication or data currency. |
| `published_at` | UTC timestamp when the artifact was copied, linked, or exposed to a publication surface. | A current `published_at` can still publish stale source data. |
| `trade_date` | Market date the artifact describes. | Not always the same as wall-clock production date. |
| `data_through_date` | Last data date reflected in calculations or cache coverage. | May lag `trade_date`, especially after hydration failures. |
| `publication_lag` | Difference between expected publication time and actual `published_at`. | Lag can be acceptable if documented. |
| `freshness_sla` | Artifact-family-specific freshness expectation. | SLA varies by domain; research packets tolerate more lag than execution artifacts. |
| `publication_lineage` | Link from publication surface back to source artifacts. | Required to trust `latest`-style artifacts. |

Freshness interpretation should evaluate all relevant fields. For example, a
dashboard export with `published_at` today but `data_through_date` yesterday is
current as a publication but stale as a market-data claim.

## Latest Artifact Interpretation

`latest` artifacts are convenience publications. They are useful for operator
ergonomics and dashboards, but they are not canonical by themselves.

Limitations:

- `latest/*` can be overwritten.
- File modification time may reflect copy time, not source-data time.
- Latest files can remain stale after suppressed publication.
- Latest files may not reveal whether their dated source was repaired,
  backfilled, partial, or low confidence.
- Latest files can become ambiguous if multiple producers publish to nearby
  surfaces without source lineage.

Operator rules:

1. Resolve `latest` to a dated source artifact before using it as evidence.
2. Require `source_trade_date`, `published_at`, `producer`, and source path for
   trust.
3. Treat latest without lineage metadata as `UNKNOWN`.
4. Treat latest with old source date as `STALE`.
5. Do not use latest artifacts for promotion evidence unless their dated source
   and freshness status are explicit.
6. Prefer dated artifacts for audits, gates, and governance decisions.

Caerus examples:

| Surface | Interpretation |
|---|---|
| `outputs/shadow_candidates/latest/*` | Convenience copy/pointer for shadow review. Must resolve to `outputs/shadow_candidates/YYYY-MM-DD/*`. |
| Latest dashboard export | Display artifact. Must preserve broker/source freshness and stale sections. |
| Latest report | Human-readable convenience surface. Must identify source run/date before operational use. |
| Latest hydration output | Useful only when it reports cache max date and source status. Mtime is insufficient. |

## Freshness Confidence Model

| Freshness Confidence | Conditions |
|---|---|
| HIGH | Artifact has explicit `produced_at`, `trade_date`, `data_through_date` where relevant, `producer`, source lineage, and family SLA alignment. Dated source exists and matches publication. |
| MEDIUM | Artifact has dated source and producer context, but some optional lineage or SLA metadata is absent. Freshness can be reasonably interpreted but should be disclosed. |
| LOW | Artifact exists but is stale, latest-only, partially published, repaired/backfilled without full metadata, or dependent on stale upstream evidence. |
| UNKNOWN | Artifact is missing, malformed, lacks date metadata, lacks producer/source lineage, or has conflicting freshness signals. |

Freshness confidence does not override artifact confidence. A fresh shadow
artifact can still carry LOW performance confidence if timing semantics are
unresolved. A stale broker snapshot may remain authoritative for its capture
time but not for current-state claims.

## Freshness vs Health Semantics

| Comparison | Distinction |
|---|---|
| Stale vs degraded | Stale is about timeliness. Degraded is about impaired or warning state. A stale artifact may cause degraded health, but they are not identical. |
| Stale vs broken chain | Stale means old. Broken chain means continuity is missing or unverifiable. A fresh artifact can still have a broken chain. |
| Stale vs low-confidence | Stale concerns time alignment. Low confidence concerns evidentiary trust. Either can exist without the other. |
| Partial freshness vs missing publication | Partial means some expected artifacts are current. Missing means no artifact exists for that expected item. |
| Advisory freshness vs authoritative freshness | Advisory freshness supports human interpretation. Authoritative freshness supports operational claims only when source authority and date alignment are explicit. |

## Operator Guidance

How to interpret stale evidence:

- Use stale evidence for historical context, not current-state claims.
- Preserve the stale reason in summaries.
- Prefer dated canonical artifacts over latest publications.
- Avoid promotion or capital decisions from stale shadow evidence.

Acceptable lag examples:

- Weekly research packet using data through the prior Friday: acceptable for
  weekly review when stated.
- Post-close hydration delayed by a few hours: acceptable for intraday
  execution interpretation, but not for final shadow performance reporting.
- Dashboard payload lagging while broker snapshot is current elsewhere:
  display degraded; do not imply broker state is stale if source is current.

Escalation examples:

- Precompute bundle missing before order phase: execution-relevant escalation.
- Broker snapshot missing for dashboard broker-authoritative claims: confidence
  becomes UNKNOWN for current broker state.
- Shadow latest stale but dated shadow artifact fresh: publication health
  degraded, research source usable.
- Hydration cache max date behind expected completed trade date: shadow/reporting
  freshness degraded.

Shadow caveats:

- Shadow artifacts are research surfaces unless promoted by governance.
- Fresh shadow telemetry does not make operational shadow NAV broker-
  authoritative.
- Partial shadow telemetry should preserve strategy-level nuance, such as Orion
  missing attribution while Polaris and Lyra are complete.

Research continuity caveats:

- Research artifacts can remain useful after they are stale for operations.
- Research packets should disclose data-through date and freshness confidence.
- Stale research evidence should not be silently mixed with fresh operational
  evidence in the same claim.

## Future Additive Metadata Proposal

This is a proposed metadata structure only. FR-018 does not retrofit producers,
create validators, or implement aggregation scripts.

```json
{
  "schema_version": 1,
  "artifact_family": "shadow_latest_publication",
  "producer": "scripts/run_shadow_candidates_daily.sh",
  "produced_at": "2026-05-22T21:05:00Z",
  "published_at": "2026-05-22T21:06:00Z",
  "trade_date": "2026-05-22",
  "data_through_date": "2026-05-22",
  "freshness_status": "FRESH",
  "freshness_confidence": "HIGH",
  "source_surface": "OPERATIONAL_SHADOW_NAV",
  "source_artifacts": [
    "outputs/shadow_candidates/2026-05-22/comparison.json"
  ],
  "published_artifacts": [
    "outputs/shadow_candidates/latest/comparison.json"
  ],
  "supersedes": [
    "outputs/shadow_candidates/latest/comparison.json@2026-05-21T21:05:00Z"
  ],
  "freshness_sla": "same_completed_trading_day",
  "notes": "Convenience latest publication; dated source remains canonical."
}
```

## MCP Compatibility Guidance

Future MCP and retrieval systems should consume freshness metadata as part of
provenance. They should not infer freshness from filesystem layout or mtime
alone.

MCP-compatible behavior:

- Report freshness status next to artifact claims.
- Resolve latest publications to source lineage before answering.
- Distinguish `produced_at`, `published_at`, `trade_date`, and
  `data_through_date`.
- Preserve freshness confidence in responses.
- Refuse to present latest-only artifacts as authoritative evidence.
- Mark superseded publications as historical, not current.

MCP must not:

- Repair stale artifacts.
- Rewrite latest publications.
- Trigger producers.
- Promote freshness confidence silently.
- Collapse advisory freshness into operational authority.

## Validation Boundary

FR-018 formalizes semantics only. It introduces no runtime behavior changes, no
freshness validators, no producer modifications, no artifact mutation, no cron
changes, and no dashboard integration.
