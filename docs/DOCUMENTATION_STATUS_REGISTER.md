---
status: ACTIVE
owner: Caerus program owner
last_audit: 2026-08-19
scope: all files beneath docs/
---

# Documentation Status Register

## Purpose

This register prevents a historical report, proposal, or implementation snapshot
from being mistaken for current operating policy. It is the companion to
[`CAERUS_OPERATING_SYSTEM_REFERENCE.md`](CAERUS_OPERATING_SYSTEM_REFERENCE.md).

The 2026-08-18 audit inventoried **199 documents** under `docs/` (about 38,600
lines). Documents were classified by authority and temporal role; historical
records are preserved rather than rewritten. “Current” claims are admissible
only when the document is in the active canonical set below and agrees with the
machine policy and date-specific operational evidence.

## Status meanings

| Status | Meaning | How to use it |
|---|---|---|
| `CANONICAL` | Governs a defined class of decisions | Read before acting; amend through governed change control |
| `CURRENT_REFERENCE` | Current explanatory or operational guidance | Use only with the linked canonical source and current artifacts |
| `HISTORICAL_RECORD` | Accurate record of its stated date, incident, or decision | Never treat as current state without corroboration |
| `RESEARCH_OR_PROPOSAL` | Design, research, or implementation intent | Does not change runtime or governance state |
| `ARCHIVED` | Superseded, retired, or retained for audit | Historical context only |

## Canonical active set

| Document or artifact | Status | Authority |
|---|---|---|
| `CAERUS_OPERATING_SYSTEM_REFERENCE.md` | `DRAFT_FOR_OWNER_RATIFICATION` | Entry point and source hierarchy; becomes canonical only after owner ratification |
| `docs/architecture/generic_deployment_accounting_migration_plan.md` | `RESEARCH_OR_PROPOSAL` | Controlling implementation plan candidate and implemented-vs-pending register for the generic operating model; all implemented contracts remain advisory and do not change active deployment authority |
| `docs/architecture/generic_migration_compatibility_inventory.md` | `CURRENT_REFERENCE` | Explicit read-only preservation rules for legacy PAPER schema 3, exact-plan v3, WAL v1, causal ownership, FR-104, and historical run artifacts during migration |
| `docs/governance/decision_records/owner_directive_2026-08-18_generic_lane_migration.json` | `CANONICAL` | Hash-bound owner decision: generic PAPER is not yet cut over and legacy runtime remains unchanged; Orion is a frozen comparison fixture only; local Lyra edits remain candidate state; adaptive allocation starts in Shadow; generic Live cutover remains conditional |
| `docs/baselines/orion_legacy_paper_fixture_capture_status_20260818.json` | `HISTORICAL_RECORD` | Initial repository-only search was blocked; later read-only VM evidence below recovered the complete factual fixture |
| `docs/baselines/orion_legacy_paper_factual_fixture_20260818.json` | `CANONICAL_FOR_ITS_DATE_AND_COMPARISON_SCOPE` | Frozen 2026-08-18 factual Orion legacy comparison input with same-session decision, broker state, target, and intended orders; no current deployment or execution authority |
| `docs/baselines/orion_legacy_paper_factual_vm_sources_20260818.json` | `CANONICAL_FOR_ITS_CAPTURE_STATUS` | Byte-hash and lineage provenance for the read-only VM factual fixture; remote state remained unchanged and kill switch armed |
| `docs/baselines/orion_generic_factual_replay_20260818.json` | `HISTORICAL_PARITY_EVIDENCE` | First factual generic Stage 4–8 replay against the frozen Orion fixture; target parity is exact but order parity is `REVIEW_REQUIRED` because per-symbol flooring sells LRCX and omits the legacy WDC buy; no deployment, execution, or activation authority |
| `docs/baselines/orion_generic_factual_replay_cash_aware_20260818.json` | `CANONICAL_FOR_ITS_DATE_AND_PARITY_SCOPE` | Corrected factual Stage 4–8 replay using the governed cash-aware whole-share realization proof; target and order parity are both exact, projected cash is within the sealed tolerance, and the artifact remains advisory/no-submit with no cutover authority |
| `docs/baselines/orion_legacy_synthetic_replay_20260812.json` | `RESEARCH_OR_PROPOSAL` | Deterministic committed-code replay proving structural reproducibility only; explicitly not historical broker evidence, factual parity, return evidence, target authority, or cutover evidence |
| `docs/baselines/adaptive_shadow_evidence_readiness_20260818.json` | `CANONICAL_FOR_ITS_CAPTURE_STATUS` | Hash-bound factual readiness: adaptive Shadow observation is blocked because policy, causal signals, decision batch, and deployment-policy inputs are absent |
| `docs/governance/decision_records/adaptive_shadow_v1_owner_approval_20260818.json` | `CANONICAL` | Owner approval binding candidate hash `0ee486...` for Shadow observation only; all readiness gates and static-Polaris fallback remain mandatory, with no Paper/Live, promotion, execution, or activation authority |
| `docs/baselines/adaptive_shadow_v1_activation_readiness_20260818.json` | `CANONICAL_FOR_ITS_CAPTURE_STATUS` | Owner-approved observation enable was requested but fails closed to static Polaris because six governed input groups remain missing; emits no adaptive performance evidence or executable target |
| `docs/baselines/ORION_PARITY_AND_ADAPTIVE_SHADOW_RECOVERY.md` | `CURRENT_REFERENCE` | Exact read-only recovery requirements and stdout-only collectors; prohibits reconstructing missing factual inputs from returns, fills, or dashboards |
| `docs/runbooks/GENERIC_LIVE_CUTOVER.md` | `CURRENT_REFERENCE` | Generic-only, no-submit rehearsal and future cutover sequence; legacy Live remains disabled and active config unchanged |
| `docs/evidence/generic_live_vm_preflight_2026-08-18.json` | `CANONICAL_FOR_ITS_CAPTURE_STATUS` | Redacted, hash-bound read-only VM evidence: clean deployed SHA and legacy Live disabled; generic staging is possible, but cutover is blocked and no remote state changed |
| `docs/evidence/generic_live_v1_active_source_deployment_2026-08-19.json` | `CANONICAL_FOR_ITS_DEPLOYMENT` | Attested active-VM source-only deployment at `7d6caf99...`: checkout clean and aligned, 179 deploy/operational checks pass, generic config and cron remain absent, kill switch remains armed, and activation/submission remains `NO_GO` on seven named P0 blockers |
| `docs/evidence/generic_live_v1_disabled_installation_2026-08-19.json` | `CANONICAL_FOR_ITS_DEPLOYMENT` | Reviewed generic Live v1 source at exact SHA `2d12a3c...` with P0-2 through P0-7 closed; installs only an account-pinned `0600` disabled config and inert date-bound cron while both gates remain armed, PAPER stays unchanged, and activation/submission remains `NO_GO` solely on the missing current-session factual Lyra chain |
| `docs/evidence/generic_live_v1_p0_1_chronology_no_go_2026-08-19.json` | `CANONICAL_FOR_ITS_CAPTURE_STATUS` | Immutable `NO_GO`: Wednesday 2026-08-19 carries Lyra's Monday 2026-08-17 target, which predates the prospective governed-universe freeze; no retroactive certification or opportunistic Wednesday rebalance is allowed |
| `docs/governance/proposals/generic_live_v1_owner_decision_2026-08-24.pending.json` | `RESEARCH_OR_PROPOSAL` | Same-terms Lyra-only Live v1 decision proposal for the first eligible prospective Monday rebalance; status `PENDING_OWNER_APPROVAL`, with no approval, activation, or execution authority |
| `docs/evidence/generic_live_no_submit_staging_deployment_2026-08-18.json` | `CANONICAL_FOR_ITS_DEPLOYMENT` | Hash-bound isolated VM staging record for commit `13f07fdd...`; 41 tests pass and scheduler defaults disabled; active checkout/config/cron/kill switch/broker remain unchanged |
| `docs/evidence/generic_live_account_observation_2026-08-18.json` | `CANONICAL_FOR_ITS_CAPTURE_STATUS` | Redacted GET-only Live account observation: $460.90 equity/cash, account hash matches the legacy pin, no credentials or raw account ID persisted, and no broker write occurred |
| `docs/evidence/generic_live_disabled_candidate_config_2026-08-18.json` | `RESEARCH_OR_PROPOSAL` | Disabled account-pinned Live candidate capped at $460 with the stricter $100 minimum trade, one-order maximum, 95% gross cap, whole shares, long-only, no leverage, and no shorting; no execution, submission, schedule, or activation authority |
| `docs/evidence/generic_paper_live_no_write_rehearsal_2026-08-18.json` | `CANONICAL_FOR_ITS_REHEARSAL_SCOPE` | Source-bound structural rehearsal proving Paper and Live use the same generic adapter and both return `VALIDATED_NO_WRITE`; not broker-factual and grants no cutover authority |
| `docs/evidence/generic_live_disabled_candidate_preflight_2026-08-18.json` | `CANONICAL_FOR_ITS_CAPTURE_STATUS` | Candidate preflight binds the shared Paper/Live rehearsal and isolated deployment; parity passes, but eight active configuration, checkout/schedule, and approval gates keep cutover blocked |
| `docs/evidence/generic_live_disabled_consumption_staging_deployment_2026-08-18.json` | `CANONICAL_FOR_ITS_DEPLOYMENT` | Hash-bound isolated VM deployment of disabled scheduled-v2, truth-dashboard, no-send owner-outbox, and Live-candidate consumers; 103 tests and six no-action proofs pass while active state remains unchanged |
| `docs/runbooks/GENERIC_LIVE_DISABLED_CONSUMPTION.md` | `CURRENT_REFERENCE` | Observation-only operating instructions for the disabled scheduled pipeline, truth consumer, no-send outbox, and candidate preflight; cannot enable execution or publication |
| `docs/governance/proposals/adaptive_shadow_v1_policy_candidate.json` | `APPROVED_FOR_SHADOW_OBSERVATION_ONLY` | Immutable proposed terms remain hash-stable; the separate owner decision approves that exact candidate for gated Shadow observation and grants no promotion, execution, activation, or Paper/Live authority |
| `docs/governance/proposals/ADAPTIVE_SHADOW_V1_DECISION_BRIEF.md` | `CURRENT_REFERENCE` | Plain-language decision and current blocked/static-Polaris activation status for the hash-bound adaptive Shadow v1 candidate |
| `config/research/strategy_registry.json` | `CANONICAL` | Machine-readable sleeve and lane policy |
| `docs/architecture/caerus_as_built_data_flow.md` | `CANONICAL` | Straight-through operating, accounting, and audit contract |
| `docs/governance/caerus_investment_doctrine.md` | `CANONICAL` | Strategy, promotion, retirement, and construction principles |
| `docs/governance/CURRENT_RESEARCH_ROADMAP.md` | `CURRENT_REFERENCE` | Research priorities and reconciled state as of its stated date |
| `docs/governance/fr_registry.md` | `CANONICAL` | FR lifecycle status |
| Hash-bound session, execution, reconciliation, ledger, valuation, and audit artifacts | `CANONICAL_FOR_THEIR_DATE_AND_LANE` | What actually happened |

## Directory classification

| Location | Default status | Notes |
|---|---|---|
| `docs/governance/fr_active/` | `RESEARCH_OR_PROPOSAL` unless the FR registry says deployed | Folder placement alone is not authority |
| `docs/governance/fr_archive/` | `ARCHIVED` | Preserve; do not rewrite as current policy |
| `docs/governance/change_index/` and `docs/governance/operational_reviews/` | `HISTORICAL_RECORD` | Date-bounded evidence |
| `docs/architecture/semantics/` | `CANONICAL` only where explicitly versioned and adopted | Otherwise design/reference material |
| `docs/runbooks/`, `docs/ops/` | `CURRENT_REFERENCE` only after runtime-path validation | Commands and artifacts can become stale |
| Date-stamped root documents and incident/review reports | `HISTORICAL_RECORD` | Retain date and scope prominently |
| Undated root architecture/model guides | `CURRENT_REFERENCE` only when they defer to the canonical active set | Do not use their strategy-state paragraphs as authority |

## Audit findings and corrections

1. The straight-through 13-stage architecture already exists in
   `docs/architecture/caerus_as_built_data_flow.md`; the gap was discoverability,
   not the absence of a design.
2. The repository had multiple undated guides that described older
   Polaris-paper / Orion-shadow operating states as “current.” Those guides are
   now explicitly subordinate to the active canonical set.
3. `CURRENT_RESEARCH_ROADMAP.md` and `ORCHESTRATOR_CONTEXT.md` contain useful
   dated state snapshots. They must not override the registry or runtime facts
   after their stated as-of date.
4. Dashboard, scorecard, and email documents are presentation references. They
   cannot establish factual sleeve performance without the ledger and valuation
   lineage required by the operating-model contract.
5. The earlier committed-Orion versus local-Lyra PAPER ambiguity is governed
   for this migration by the corrected 2026-08-18 owner directive. Generic
   PAPER is `NOT_YET_CUT_OVER`; the legacy runtime remains unchanged, local
   Lyra edits are candidate-only, and Orion survives only as immutable
   comparison evidence. The directive records and invalidates the earlier
   erroneous content hash that inferred Lyra authority.

## Ongoing review rule

Every documentation-affecting change must update this register when it changes
the canonical set, status of a document family, or a source-conflict finding.
Each quarter, and after any execution/ledger migration, re-run the inventory and
review active `CURRENT_REFERENCE` documents against the machine policy and
runtime artifact contracts.
