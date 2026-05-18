# Operational Health Aggregator

## Purpose

This document is the Phase 4 foundation for a lightweight, read-only daily
health synthesis surface.

The aggregator is a proposed telemetry layer only. It must not run precompute,
submit orders, refresh dashboard data, hydrate prices, regenerate shadow
artifacts, install cron, deploy source, or mutate production runtime state.

## Design Goals

- Give operators one daily summary of platform state.
- Preserve links to underlying evidence artifacts.
- Make degraded, stale, partial, and unknown states explicit.
- Keep implementation local, deterministic, and easy to roll back.
- Avoid distributed systems, services, databases, scheduler rewrites, or
  background daemons.

## Proposed Outputs

Future implementation should write:

```text
outputs/operations/daily_health_summary.json
outputs/operations/daily_health_summary.md
```

The JSON is the machine-readable summary. The markdown is the operator-readable
view. Both are derived diagnostics; neither is a trading gate unless a later FR
explicitly promotes a specific check.

## Aggregation Philosophy

The aggregator should read existing artifacts and classify them. It should not
create missing source artifacts.

Aggregation rules:

- Prefer dated artifacts over mutable `latest` files.
- Treat missing artifacts as `unknown` unless the artifact is required for a
  specific phase.
- Distinguish trading-critical failures from non-blocking diagnostic failures.
- Include evidence paths for every check.
- Do not hide stale state by replacing it with a newer derived summary.
- Preserve raw statuses from source artifacts, then add a normalized health
  classification.

## Health Vocabulary

| Status | Meaning |
|---|---|
| `green` | Required evidence is present, fresh, and internally consistent. |
| `yellow` | Degraded or stale condition exists, but trading-critical state is not known to be unsafe. |
| `red` | Required trading-critical evidence is missing, failed, or inconsistent. |
| `unknown` | Evidence is absent or unreadable, and no safe inference should be made. |
| `suppressed` | A side effect was intentionally skipped, such as shadow publication during self-heal. |

## Proposed Input Categories

| Category | Representative Inputs | Initial Interpretation |
|---|---|---|
| Precompute | `outputs/precompute/<date>/contract.json`, `outputs/workflow/<date>/precompute_bundle_validation.json` | Required for execution. Missing or failed validation is red for execution readiness. |
| Execution | `outputs/workflow/<date>/execution_bundle_validation.json`, `outputs/workflow/<date>/execution_self_heal.json`, `outputs/runs/<run_id>/execution_results.json` | Distinguish no-action, executed, halted, partial, and recovery continuation. |
| Shadow | `outputs/workflow/<date>/shadow*.json`, `outputs/shadow_candidates/<date>/comparison.json`, latest publication metadata | Non-blocking. Failure is yellow unless it contaminates promotion decisions. |
| Hydration | `outputs/price_hydration/<date>/status.json`, price cache metadata | Shadow/reporting health. Stale cache should be visible. |
| Dashboard | Dashboard payload source checks, dashboard generated timestamps, deployed payload freshness | Display health. Must not imply broker state if broker source is stale. |
| Recovery | `execution_self_heal.json`, `precompute_self_heal.json`, backfill manifests | Repeated recovery attempts require operator review. |
| Validators | `scripts/operational_validation.py` output if captured, cron command validation, targeted check artifacts | Governance health. Missing validator output is unknown, not pass. |
| Latest freshness | Proposed freshness manifests, latest source dates, publication status | Detect stale/latest ambiguity. |
| Dependency governance | Dependabot status if available, dependency governance docs, advisory audit output if later added | Advisory until FR-022 is promoted. |

## Proposed JSON Shape

```json
{
  "schema_version": 1,
  "trade_date": "2026-05-17",
  "generated_at": "2026-05-17T22:00:00Z",
  "status": "yellow",
  "read_only": true,
  "summary": {
    "red": 0,
    "yellow": 2,
    "green": 6,
    "unknown": 1
  },
  "checks": [
    {
      "name": "precompute_bundle",
      "category": "precompute",
      "status": "green",
      "blocking": true,
      "detail": "Bundle validation status OK.",
      "evidence_paths": [
        "outputs/workflow/2026-05-17/precompute_bundle_validation.json"
      ],
      "operator_action": "none"
    }
  ],
  "operator_recommendation": "Review yellow shadow freshness before using promotion diagnostics."
}
```

## Proposed Markdown Shape

The markdown should be compact and operator-focused:

```text
# Daily Operational Health

- Trade date: 2026-05-17
- Overall: YELLOW
- Trading-critical blockers: 0
- Degraded diagnostics: 2

| Category | Status | Detail | Evidence |
|---|---|---|---|
| Precompute | GREEN | Bundle validation OK | outputs/workflow/... |
| Shadow | YELLOW | latest publication stale | outputs/workflow/... |
```

## Degraded-State Semantics

Degraded does not always mean unsafe. The aggregator should preserve the
operational boundary:

- Precompute bundle invalid before execution: `red`.
- Execution self-heal attempted and continued after full validation: `yellow`.
- Execution self-heal attempted and failed closed: `red`.
- Shadow generation failed but execution bundle is valid: `yellow`.
- Hydration stale after market close: `yellow` for reporting and shadow, not
  automatically red for trading.
- Missing dashboard payload: `yellow` for operator display, not execution state.

## Stale-State Semantics

Stale state should be explicit. A stale artifact should include:

- expected trade date
- source trade date
- produced or published timestamp
- producer
- stale reason
- whether the stale condition is blocking

The aggregator should never convert stale source artifacts into a fresh-looking
summary. If the source is stale, the summary can be current while reporting that
the source is stale.

## Initial Rollout Plan

1. Implement read-only artifact readers for the smallest useful set:
   precompute validation, execution validation, shadow wrapper status, hydration
   status, and dashboard payload freshness.
2. Write JSON and markdown summaries under `outputs/operations/`.
3. Add tests using isolated temporary directories only.
4. Keep the aggregator non-blocking and advisory.
5. Add optional use by runbook/dashboard only after operators trust the summary.

## Non-Goals

- No scheduler rewrite.
- No Airflow or distributed orchestration.
- No database service.
- No broker calls.
- No strategy calculations.
- No artifact cleanup.
- No promotion decisions.

## Open Questions For Implementation

- Should the aggregator run post-confirmation, post-close, or manually first?
- Which checks should be `blocking` vs `diagnostic` in the first version?
- How should missing validator output be represented when validators were not
  run that day?
- Should dependency governance warnings be manually entered at first or read
  from future advisory audit artifacts?
