---
last_reviewed: 2026-05-22
owner: governance
category: freshness_examples
criticality: medium
canonical: false
related_systems: [telemetry, freshness, shadow, dashboard, hydration]
---

# Freshness Examples

These are mock operator-facing examples. They do not imply current runtime
state.

## Healthy Same-Day Publication

```text
Freshness: FRESH
Confidence: HIGH

Artifact: outputs/shadow_candidates/latest/comparison.json
Source: outputs/shadow_candidates/2026-05-22/comparison.json
Trade date: 2026-05-22
Produced at: 2026-05-22T21:04:00Z
Published at: 2026-05-22T21:05:00Z

Interpretation:
The latest shadow comparison resolves to the same-day dated artifact. Use the
dated source for audit and the latest publication for operator convenience.
```

## Stale Latest Dashboard Export

```text
Freshness: STALE
Confidence: LOW

Artifact: /var/www/caerus-dashboard/dashboard_data.json
Displayed trade date: 2026-05-22
Broker source date: 2026-05-21
Published at: 2026-05-22T22:00:00Z

Interpretation:
The dashboard payload was published today, but its broker source is stale.
Display health is degraded. Do not use the dashboard headline as current
broker-authoritative evidence.
```

## Delayed Hydration Artifact

```text
Freshness: ACCEPTABLY_LAGGED
Confidence: MEDIUM

Artifact: outputs/price_hydration/2026-05-22/status.json
Expected data-through date: 2026-05-22
Current cache max date: 2026-05-21
Produced at: 2026-05-22T23:10:00Z

Interpretation:
Hydration is behind the expected completed trading day. This may be acceptable
before final post-close reporting, but shadow performance and reporting should
carry a freshness caveat until cache coverage catches up.
```

## Partially Fresh Shadow Telemetry

```text
Freshness: PARTIAL
Confidence: MEDIUM for complete strategies, LOW for incomplete strategy

Artifacts:
- outputs/shadow_candidates/2026-05-22/caerus_polaris.json: FRESH
- outputs/shadow_candidates/2026-05-22/caerus_lyra.json: FRESH
- outputs/shadow_candidates/2026-05-22/caerus_orion.json: MISSING attribution section

Interpretation:
Shadow telemetry is usable for Polaris and Lyra. Orion interpretation is
partial. Do not use a cross-strategy attribution ranking without disclosing the
Orion gap.
```

## Stale But Useful Research Artifact

```text
Freshness: STALE for operations, ACCEPTABLY_LAGGED for weekly research
Confidence: MEDIUM

Artifact: outputs/research/weekly_factor_review_2026-05-15.md
Research window: 2026-05-11 through 2026-05-15
Current date: 2026-05-22

Interpretation:
This artifact is stale for current operational state but still useful for a
weekly research continuity review. Do not mix it with current broker evidence
without labeling the research window.
```

## Superseded Publication

```text
Freshness: SUPERSEDED
Confidence: LOW for current-state claims

Artifact: outputs/shadow_candidates/latest/comparison.json@2026-05-21T21:05:00Z
Superseded by: outputs/shadow_candidates/latest/comparison.json@2026-05-22T21:06:00Z
Canonical dated source: outputs/shadow_candidates/2026-05-22/comparison.json

Interpretation:
The older publication remains audit context only. Current operator review
should use the newer publication or, preferably, the dated source artifact.
```

## Conflicting Latest Publication

```text
Freshness: UNKNOWN
Confidence: UNKNOWN

Artifacts:
- outputs/latest_run.json references run_id A for 2026-05-22.
- outputs/workflow/2026-05-22/execution.json references run_id B.

Interpretation:
Latest publication is ambiguous. Prefer the date-scoped workflow pointer and
inspect run roots before making execution or reporting claims.
```

## Unknown Freshness State

```text
Freshness: UNKNOWN
Confidence: UNKNOWN

Artifact: outputs/reports/latest_summary.md
Missing metadata:
- produced_at
- trade_date
- source artifacts
- producer

Interpretation:
The report may be readable, but freshness cannot be established. Treat it as
advisory text only until source lineage is available.
```
