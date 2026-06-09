# Artifact Governance

## Purpose

This document is the Phase 4 foundation for Caerus artifact ownership,
freshness, retention, and trust semantics.

It is governance only. It does not change trading behavior, broker submission,
strategy selection, cron timing, dashboard publishing, or runtime artifact
producers.

## Operating Principles

- Every operational artifact should have an owner, producer, consumer, category,
  freshness rule, and retention class.
- Canonical artifacts should be few, explicit, and protected from accidental
  replacement by generated reports or convenience copies.
- Derived and diagnostic artifacts can aid operators, but they must not silently
  become source of truth.
- `latest` is a publication convenience, not proof of freshness.
- Stale state should be visible through metadata, not hidden by overwriting or
  deleting artifacts.
- Cleanup policy must be documented before cleanup automation exists.

## Artifact Taxonomy

| Category | Meaning | Examples | Trust Boundary |
|---|---|---|---|
| `canonical` | Source of truth for a workflow decision or persisted operational state. | `outputs/precompute/<date>/contract.json`, `outputs/paper_state/canonical_positions.json`, broker-authoritative snapshots. | May gate execution or operator decisions. Requires explicit validation. |
| `derived` | Recomputed from canonical or external source data. | Dashboard payloads, performance series, comparison tables. | Useful for display and review, but consumers should know the source chain. |
| `diagnostic` | Evidence for investigation, recovery, or operator review. | `outputs/workflow/<date>/*.json`, health checks, backfill diagnostics. | Should not alter execution behavior by itself. |
| `runtime` | Produced by scheduled or manual runtime operation. | Logs, run roots, execution emails, hydration status. | Operational evidence; preserve during incidents. |
| `generated_report` | Human-readable generated output. | `comparison.md`, weekly learning reports, dashboard exported summaries. | Review surface only unless explicitly designated canonical. |
| `historical` | Archived or research-era outputs retained for context. | Alpha lab reports, old research backtests, incident examples. | Not live operational state. |
| `ephemeral` | Temporary or local-only output that can be regenerated. | Test scratch outputs, temporary probe artifacts. | Must not be consumed as production evidence. |
| `latest_publication` | Pointer or copy to a recent artifact. | `outputs/latest_run.json`, `outputs/shadow_candidates/latest/*`, `*_latest.json`. | Must carry or be paired with freshness metadata before trusted. |

## Ownership Semantics

Each artifact family should answer:

- **Owner:** The system area accountable for the artifact contract.
- **Producer:** The script/module that writes the artifact.
- **Consumers:** Dashboards, emails, validators, operators, or downstream scripts.
- **Category:** One taxonomy category, plus optional secondary category.
- **Freshness rule:** How to decide whether the artifact is current enough.
- **Retention class:** How long to keep it and whether it needs backup.
- **Overwrite rule:** Whether existing artifacts can be overwritten, appended, or
  only superseded by a new dated artifact.

## Initial Artifact Registry

This is the first-pass static registry. It should be refined before any cleanup
or telemetry enforcement is built. Retention and backup semantics are governed
by `docs/artifact_retention_policy.md`; validation isolation semantics are
governed by `docs/validation_isolation_policy.md`. No cleanup automation is
implied by either policy.

| Family | Representative Paths | Category | Producer | Primary Consumers | Freshness Assumption | Overwrite Semantics | Retention Ambiguity |
|---|---|---|---|---|---|---|---|
| Precompute bundle | `outputs/precompute/<date>/contract.json`, `daily_snapshot.json`, `signals.json`, `planned_execution_payload.json` | `canonical` | `daily_quant_report.py`, `core/precompute_contract.py`, `scripts/cron_precompute.sh` | `scripts/cron_execute.sh`, bundle validators, operators | Must match execution trade date and pass bundle validation. | Dated directory may be rebuilt during self-heal; validation must prove completeness. | Retention window not formalized. |
| Workflow status | `outputs/workflow/<date>/*.json` | `diagnostic`, `runtime` | Cron wrappers, recovery validators, shadow wrapper | Operators, future health aggregator | Must match date and producer step. Missing status means unknown, not healthy. | Additive per date; may be overwritten by repeated same-day attempts. | Incident retention not formalized. |
| Execution run roots | `outputs/runs/<run_id>/` | `runtime`, `canonical` for run evidence | `scripts.run_precomputed_alpaca_execution`, paper runtime | Confirmation email, dashboards, operators | Run id and trade date must align with stage pointer. | New run root per run id. | Long-term archive rules not formalized. |
| Latest run pointers | `outputs/latest_run.json`, `outputs/latest.json` | `latest_publication` | Run pointer helpers, daily orchestrator | Legacy tooling, dashboards, repair helpers | Must not be trusted without trade date, stage, status, and source path checks. | Overwritten by most recent run activity. | Needs freshness manifest or strict stage pointer preference. |
| Stage pointers | `outputs/workflow/<date>/execution.json`, similar stage pointers | `canonical`, `runtime` | `core/run_pointer.py` | Confirmation/execution handoff, operators | Preferred over mutable latest pointer for date-scoped handoff. | One pointer per date/stage, overwritten by same-stage updates. | Retention tied to workflow date. |
| Shadow daily artifacts | `outputs/shadow_candidates/<date>/*.json`, `comparison.md` | `derived`, `generated_report`, `diagnostic` | `research.shadow_tracking.run` | Shadow CIO report, dashboard, operators | Dated artifact must match requested trade date or explicit fallback reason. | Dated outputs can be regenerated by recovery/backfill. | Backfilled vs native run provenance needs stronger metadata. |
| Shadow latest publication | `outputs/shadow_candidates/latest/*` | `latest_publication` | `scripts/run_shadow_candidates_daily.sh`, hydration refresh tooling | Operators, dashboard, health checks | Must be paired with source date and publication status before trusted. | Copies current dated files when available; can remain stale after suppressed side effects. | Needs manifest. |
| Shadow performance series | `outputs/shadow_candidates/performance/shadow_nav_series.csv`, `shadow_summary.json` | `derived`, `diagnostic` | Shadow tracking and refresh tooling | Shadow CIO report, promotion audits | Latest NAV date must align with evaluation date or carry stale reason. | Rewritten during refresh/backfill. | Retention and backup around backfills not formalized. |
| Broker snapshots | `outputs/broker/*.json`, `outputs/broker_snapshot/*.json` | `canonical`, `runtime` | Alpaca snapshot/export scripts, dashboard refresh | Dashboard, reconciliation, operators | Broker-authoritative only when source and captured time are explicit. | Latest files overwritten; dated snapshots append by date. | Backup policy for broker evidence not formalized. |
| Reconciliation artifacts | `outputs/broker/recon_*_<date>.json`, `outputs/reconciliation/live_vs_shadow/*` | `canonical` for gates, `diagnostic` for comparison | `reconciliation.py`, live-vs-shadow reconciliation | Execution gates, operators, health checks | Pretrade reconciliation can block execution; live-vs-shadow is diagnostic. | Dated artifacts may be overwritten by rerun. | Incident retention should be explicit. |
| Post-sell rebudget artifacts | `outputs/runs/<RUN_ID>/broker/post_sell_rebudget_<date>.json` | `diagnostic`, `runtime` | Execution runtime | Operators, execution-integrity review | Required for sell-leg runs; reports confirmed proceeds, refreshed account state, buy budget, final orders, and reason codes. | One per run/trade date; may be overwritten by same run replay. | Retain with run-root incident evidence. |
| Operational drag artifacts | `outputs/operational_drag/<date>/*` | `derived`, `diagnostic` | `research.operational_drag` | Research review, operators | Must expose alignment date, source paths/dates, stale/blocking components, decision-grade status, and confidence. | Dated outputs can be regenerated from source artifacts. | Generated analysis; retain dated decision evidence. |
| Target-attainment artifacts | `outputs/target_attainment/<date>/target_attainment_<date>.json` | `derived`, `diagnostic` | `research.target_attainment` | Operators, research review, deployment-integrity analysis | Must compare risk-adjusted target to actual portfolio and report deployment efficiency, excess cash, drift contributors, reason codes, and confidence. | Dated outputs can be regenerated from source artifacts. | Generated analysis; retain dated decision evidence. |
| Hydration status | `outputs/price_hydration/<date>/status.json` | `diagnostic`, `runtime` | `scripts.hydrate_price_cache_only` | Shadow health, CIO report, operators | `max_cache_date` must cover expected completed trading day. | Dated status per hydration date. | Cache and status retention not formalized. |
| Price cache | `outputs/research/flow_detection_v1/price_panel.parquet` | `canonical` for shadow/reporting cache | Flow detection data loader, hydration script | Shadow tracking, research/reporting | Current enough only if metadata/status confirms max date and coverage. | Rewritten or extended by hydration. | Needs coverage sidecar and backup rules. |
| Dashboard payloads | `web/dashboard*/dashboard_data.json`, deployed `/var/www/.../dashboard_data.json` | `derived`, `generated_report` | `scripts/refresh_quant_dashboard.py`, dashboard builder | Dashboard UI, operators | Must expose source trust and stale sections. | Overwritten on refresh. | Local tracked/untracked payload hygiene needs separation. |
| Generated emails/reports | `outputs/execution_email/<date>.json`, report markdown/html | `generated_report`, `runtime` | Email/report scripts | Email sender, operators | Should reflect source run id and execution status. | Dated, sometimes overwritten by rerun. | Archive policy not formalized. |
| Research outputs | `outputs/research/**`, weekly research markdown | `historical`, `generated_report`, sometimes `derived` | Research/backtest scripts | Research review, future strategy work | Not live operating state unless explicitly promoted. | Usually append by experiment; latest files may exist. | Needs separation from operator docs. |
| Test/smoke residue | Repo-level `outputs/` or `logs/` from tests | `ephemeral` | Tests and smoke scripts | Test assertions only | Should not be production evidence. | May overwrite ignored runtime files. | FR-020 defines isolation policy; code-level migration remains future work. |

## Proposed Manifest Structure

Future producers should be able to emit a small manifest beside generated
artifact families. The manifest should be local JSON, not a database or service.

```json
{
  "schema_version": 1,
  "artifact_family": "shadow_latest_publication",
  "category": "latest_publication",
  "owner": "shadow_orchestration",
  "producer": "scripts/run_shadow_candidates_daily.sh",
  "produced_at": "2026-05-17T21:05:00Z",
  "trade_date": "2026-05-17",
  "source_artifacts": [
    "outputs/shadow_candidates/2026-05-17/comparison.json"
  ],
  "published_artifacts": [
    "outputs/shadow_candidates/latest/comparison.json"
  ],
  "freshness": {
    "source_trade_date": "2026-05-17",
    "freshness_status": "fresh",
    "staleness_policy": "must_match_expected_completed_trade_date",
    "reason": null
  },
  "retention": {
    "class": "runtime_evidence",
    "minimum_days": 90,
    "delete_only_with_manifest": true
  }
}
```

## Freshness Semantics

Freshness status should use a small shared vocabulary:

| Status | Meaning | Operator Interpretation |
|---|---|---|
| `fresh` | Source date and expected date align, and producer reports complete publication. | Trust within category limits. |
| `stale` | Artifact exists but source date is older than expected. | Use as historical context only. |
| `partial` | Some expected artifacts published, others missing. | Review missing list before trusting. |
| `suppressed` | Producer intentionally skipped publication due to recovery/degraded mode. | Prior latest may be stale by design. |
| `missing` | Expected artifact does not exist. | Treat health as unknown or failed based on category. |
| `unknown` | Metadata is absent or unreadable. | Do not infer healthy state. |

`latest` artifacts should not be interpreted as fresh unless the artifact itself
or a sidecar manifest provides `source_trade_date`, `published_at`, `producer`,
`freshness_status`, `staleness_policy`, and `source_artifact_path`.

## Latest Publication Rules

1. A `latest` file is a convenience pointer or copy, not a canonical dated
   artifact.
2. Dated source artifacts remain the primary audit evidence.
3. Latest publication must never delete stale files to create an illusion of
   health.
4. Suppressed publication during self-heal or recovery should be explicit.
5. Consumers should prefer dated artifacts when performing gates, audits, or
   promotion decisions.
6. Dashboards may read latest artifacts for display only when freshness metadata
   is visible to the operator.

## Retention Classes

| Class | Examples | Initial Guidance |
|---|---|---|
| `critical_runtime_evidence` | Broker snapshots, pretrade recon, execution run roots, execution payloads. | Retain through audit window; back up before cleanup. |
| `workflow_evidence` | `outputs/workflow/<date>/*.json`, cron logs. | Retain long enough to investigate scheduler incidents. |
| `diagnostic_rebuildable` | Health summaries, shadow diagnostics. | Can be regenerated only if source artifacts remain. |
| `generated_display` | Dashboard payloads, generated markdown reports. | Retain latest plus dated reports according to operator needs. |
| `research_historical` | Backtests, alpha lab outputs. | Archive by project milestone, not daily runtime policy. |
| `ephemeral_test` | Test outputs and smoke residue. | Should live outside repo-level runtime paths. |

FR-019 now defines retention classes, backup boundaries, evidence holds, and
future cleanup automation requirements in `docs/artifact_retention_policy.md`.
No cleanup automation has been added.

## Current Gaps To Address Later

- Several latest-style artifacts lack sidecar manifests.
- Runtime and test artifacts can share ignored `outputs/` and `logs/` paths.
- Research markdown, generated reports, and canonical docs are not fully
  separated.
- Bundle validation is strong on existence but shallow on semantics.
- Runtime producers do not yet emit retention manifests.
- Cleanup automation remains intentionally unimplemented.
- Some tests and smoke checks still need code-level migration to the FR-020
  validation isolation policy.
