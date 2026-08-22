---
last_reviewed: 2026-05-22
owner: governance
category: operational_health_examples
criticality: medium
canonical: false
related_systems: [operations, telemetry, shadow, reconciliation, hydration]
---

# Operational Health Examples

These examples show how the health model should read to an operator. They are
mock examples only and do not imply current runtime state.

## Healthy Trading Session

```text
Overall: HEALTHY
Trading-critical blockers: 0

Execution health: HEALTHY
- Precompute bundle validated for 2026-05-22.
- Execution run root exists and references the expected precompute contract.
- Posttrade reconciliation is clean.

Shadow telemetry health: HEALTHY
- Polaris, Orion, and Lyra dated shadow artifacts are present for 2026-05-22.
- Latest shadow publication resolves to the same dated source.

Interpretation:
Operational evidence is fresh and internally consistent. Shadow remains
non-blocking and research-only.
```

## Stale Latest Artifact

```text
Overall: DEGRADED
Trading-critical blockers: 0

Publication health: STALE
- outputs/shadow_candidates/latest/comparison.json points to 2026-05-21.
- Expected completed trade date is 2026-05-22.
- Dated source artifact for 2026-05-22 exists.

Interpretation:
Use the dated 2026-05-22 shadow artifact for research review. Do not use the
latest publication as promotion evidence until freshness metadata aligns.
```

## Partial Shadow Telemetry

```text
Overall: PARTIAL
Trading-critical blockers: 0

Shadow telemetry health: PARTIAL
- Polaris and Lyra attribution artifacts are present.
- Orion attribution is missing, but Orion comparison metrics are present.
- Shadow wrapper status reports non-blocking completion with partial telemetry.

Interpretation:
Execution health is unaffected. Challenger interpretation is incomplete. Avoid
ranking Orion on attribution diagnostics until the missing telemetry is
recovered or explicitly waived.
```

## Degraded Reconciliation

```text
Overall: DEGRADED
Trading-critical blockers: depends on phase

Reconciliation health: DEGRADED
- Pretrade reconciliation reported DRIFT_DETECTED.
- Broker snapshot is present and current.
- Planned positions differ from broker-held positions.

Interpretation:
The drift finding is credible because the broker source is available. If this
is pre-execution, the existing reconciliation gate owns the execution decision.
The health layer only summarizes and points to evidence.
```

## Hydration Lag

```text
Overall: DEGRADED
Trading-critical blockers: 0

Hydration health: STALE
- Hydration status was produced after close.
- Price cache max date is 2026-05-21.
- Expected completed trade date is 2026-05-22.

Interpretation:
Reporting and shadow diagnostics may be stale. Do not infer current shadow
performance from cache-dependent artifacts until hydration catches up.
```

## Provenance Ambiguity

```text
Overall: UNKNOWN
Trading-critical blockers: 0

Provenance confidence health: UNKNOWN
- A generated summary exists, but source artifact paths are absent.
- The report does not identify producer, trade date, or source truth surface.

Interpretation:
The summary may be useful as a human note, but it is not reliable operational
evidence. Prefer dated canonical artifacts or regenerate the report with
explicit provenance metadata in a future governed change.
```

## Low-Confidence Shadow Interpretation

```text
Overall: DEGRADED
Trading-critical blockers: 0

Shadow telemetry health: HEALTHY
Provenance confidence health: LOW
- Shadow artifacts are present and fresh.
- Operational shadow NAV remains LOW confidence pending timing semantics
  governance.
- In this dated Shadow-health example, Orion and Lyra are modeled Shadow
  challengers. This example does not establish current capital-lane authority.

Interpretation:
The shadow lane ran successfully, but performance claims should be treated as
research context rather than promotion-grade evidence. Use attribution and
exposure diagnostics to form hypotheses, not capital decisions.
```
