---
last_reviewed: 2026-06-14
owner: governance
category: artifact_ownership_matrix
criticality: high
canonical: true
related_systems: [governance, telemetry, provenance, operations]
---

# Artifact Ownership Matrix

Quick-reference ownership and trust matrix for major Caerus artifact families.
Detailed semantics live in `docs/artifact_registry.md`.

| Artifact Family | Canonical Owner | Producer | Primary Consumer | Criticality | Freshness SLA | Confidence Expectations | Notes |
|---|---|---|---|---|---|---|---|
| Precompute bundle | Execution planning | `daily_quant_report.py`, `core/precompute_contract.py`, `scripts/cron_precompute.sh` | `scripts/cron_execute.sh`, bundle validators | HIGH | Must match execution trade date before order phase. | HIGH when full bundle validation passes; UNKNOWN if incomplete. | Execution-critical. Do not replace with generated summaries. |
| Execution run roots | Execution runtime | `scripts.run_precomputed_alpaca_execution` | Confirmation email, operators, posttrade review | HIGH | Same trade date and run stage as execution workflow. | HIGH when run id, status, and broker/recon evidence align. | Dated run evidence is stronger than latest pointers. |
| Trading day summary | Reporting / operations | Daily confirmation/reporting flow | Operators, historical review | MEDIUM | Same completed trading day. | MEDIUM when source run is explicit; LOW if source is ambiguous. | Summary only; not a substitute for execution artifacts. |
| Workflow status artifacts | Workflow observability | Cron wrappers, recovery validators, shadow wrapper | Operators, health aggregator | MEDIUM | Same workflow date. | MEDIUM when step, producer, and status are explicit. | Missing status means unknown, not healthy. |
| Shadow dated artifacts | Shadow research | `research.shadow_tracking.run`, shadow wrapper | Shadow CIO report, promotion review | MEDIUM | Same evaluated trade date or explicit stale/fallback reason. | MEDIUM for complete dated artifacts; LOW for repaired/backfilled/stale outputs. | Shadow-only; not broker-authoritative. |
| Shadow latest publication | Shadow publication | `scripts/run_shadow_candidates_daily.sh`, hydration refresh tooling | Dashboard, operators | LOW | Must identify dated source and publication timestamp. | LOW without freshness manifest; MEDIUM only as pointer to validated dated source. | Convenience publication only. |
| Shadow performance series | Shadow research | Shadow tracking, refresh tooling, governed restatement tooling | CIO report, promotion audits | MEDIUM | Latest NAV date should align with evaluation date or state stale reason. | MEDIUM when the active operational series is continuous under `dated_same_day_close_to_close_v1`; LOW for legacy mixed-convention lineage, stale, or unreconciled repairs. | Do not blend with broker NAV or legacy mixed-convention Shadow history. |
| VIX / regime artifacts | Regime intelligence | Regime/VIX audit scripts | Regime diagnostics, research review | MEDIUM | Data-through date should cover expected market date. | MEDIUM when input dates and source are explicit. | Research/diagnostic surface. |
| Hydration status | Data hydration | `scripts.hydrate_price_cache_only` | Shadow health, operators | MEDIUM | Same post-close hydration date; cache max date must be explicit. | MEDIUM if coverage and max date are clear; LOW on failure/lag. | Telemetry only; should not mutate execution. |
| Price cache | Data/hydration | Data loader and hydration scripts | Shadow, research, reporting | MEDIUM | Coverage should reach expected completed trading date. | MEDIUM when coverage metadata exists; LOW/UNKNOWN without it. | Parquet/cache is data substrate, not operator summary. |
| Broker snapshots | Broker state | Alpaca snapshot/export tooling | Dashboard, reconciliation, operators | HIGH | Current broker capture time for reporting date. | HIGH when broker-authoritative and current; UNKNOWN if missing. | Broker source is authoritative for paper/live account state. |
| Reconciliation artifacts | Reconciliation | `reconciliation.py`, broker recon scripts | Execution gates, operators | HIGH | Same trade date and phase: pretrade/posttrade. | HIGH on clean broker-backed recon; LOW on drift. | Pretrade recon may gate execution. |
| Post-sell rebudget artifacts | Execution runtime | Execution runtime | Operators, execution integrity review | HIGH | Same run id/trade date for sell-leg runs. | HIGH when confirmed proceeds and refreshed account state are present; LOW if missing or unresolved. | Confirms buy-leg rebudgeting after sells; observability only. |
| Sleeve numeric trace artifacts | Sleeve validity / daily report runtime | Sleeve validity / daily report runtime | Operators, allocation cash-route review, target-attainment review | MEDIUM | Same run id, sleeve id, and trade date as the invalid-sleeve event. | MEDIUM when reason code and first-event metadata are present; LOW if the trace is missing or first-event metadata is unavailable. | Explains non-finite sleeve invalidation and cash-routing events; diagnostics only. |
| Operational drag artifacts | Performance provenance | `research.operational_drag` | Research review, operators | MEDIUM | Latest aligned date should reach requested trade date or explain blockers. | MEDIUM when current-date decision-grade with source diagnostics; LOW when material blockers remain. | Intended-vs-actual-vs-SPY attribution; no execution effect. |
| Target-attainment artifacts | Deployment integrity | `research.target_attainment` | Operators, research review | MEDIUM | Same trade date as execution/reconciliation evidence. | HIGH/MEDIUM when target, broker, and order sources are aligned; LOW if target/actual sources are missing. | Answers actual-vs-risk-adjusted-target, not broker expected-vs-actual. |
| Dashboard payloads | Dashboard/reporting | `scripts/refresh_quant_dashboard.py`, dashboard builder | Dashboard UI, operators | MEDIUM | Current enough for displayed trade date; stale sections visible. | Derived only; cannot exceed source artifacts. | Do not hide missing canonical data. |
| Generated emails/reports | Reporting | Email/report scripts | Operators, email recipients | LOW-MEDIUM | Same report date and source run id. | MEDIUM when source artifacts are referenced; LOW otherwise. | Human-readable review surface. |
| Attribution artifacts | Research attribution | Attribution scripts | CIO review, promotion governance | MEDIUM | Data-through date explicit. | MEDIUM when surface, inputs, and limitations are explicit; LOW otherwise. | Research clarity layer. |
| Exposure intelligence artifacts | Portfolio intelligence | Exposure/risk scripts | CIO review, operators | MEDIUM | Same attribution/evaluation date. | MEDIUM when derived from dated holdings; LOW from inferred holdings. | Advisory risk surface. |
| Performance veracity audits | Research audit | Audit scripts | Governance, CIO review | MEDIUM | Generated date and evaluated source range explicit. | MEDIUM for deterministic audit; LOW if source artifacts are sparse. | Challenges reported performance claims. |
| Learning / feedback artifacts | Portfolio learning | Feedback loop and learning scripts | Weekly review, research | LOW | Source period explicit. | Advisory; inherits source confidence. | Should not drive execution automatically. |
| Research outputs | Research | Research/backtest scripts | Research review | LOW | Experiment date and data-through date explicit. | LOW to MEDIUM depending on PIT safety and reproducibility. | Not operational state unless promoted. |
| Latest-style convenience artifacts | Publication helpers | Various runtime/reporting publishers | Legacy tooling, dashboard, operators | LOW | Must resolve to dated source artifact. | UNKNOWN without source metadata. | Latest is not freshness proof. |
| Generated documentation artifacts | Docs/reporting | Documentation/report generators | Operators, reviewers | LOW | Generated timestamp if operationally relevant. | LOW unless sources are linked and dated. | Keep separate from canonical docs. |
| Test / smoke residue | Validation | Tests and local smoke scripts | Tests only | LOW | Not production evidence. | UNKNOWN for operations. | FR-020 defines isolation policy; prefer bounded temporary roots. |
