# FR Active Backlog

## Purpose

This document is the operator-readable roadmap for active Caerus Friday
Refactor (FR) work. It contains only work that is not fully closed:

- `BACKLOG`
- `READY`
- `READY_VALIDATED`
- `PROPOSED`
- `IN_PROGRESS`
- `PROMOTION_READY`
- `DEPLOYED_OBSERVING`

Fully deployed history and reviewed deferred items belong in
`docs/governance/fr_registry.md`. Governance methodology belongs in
`docs/governance/fr_governance_model.md`.

Doctrine canon: `docs/governance/caerus_investment_doctrine.md` is the
canonical strategic doctrine for Caerus. Future FRs, strategy specs, sleeve
designs, promotion reviews, portfolio-construction decisions, and allocation
architecture should align with it unless explicitly amended.

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
| FR-028 shadow execution timing semantics correction candidate | Accounting Correctness / Promotion Analytics | `DEPLOYED_OBSERVING` | HIGH | FR-024, FR-025, FR-026, FR-027, FR-030 | observing | Phase C adds research-only promotion readiness sidecars. After the Shadow NAV recovery, promotion windows must use only the canonical operational series: `dated_same_day_close_to_close_v1`, observation inception 2026-05-12, 23 NAV rows at recovery. Legacy mixed-convention Shadow history is lineage-only and non-decision-grade; no historical migration, execution, broker, cron, strategy selection, or capital-allocation change. | Revert FR-028 Phase C commit; ignore new dated promotion-readiness sidecars and continue reading existing `shadow_evaluation.json` / `comparison.json`. |
| FR-029 promotion governance hardening for provenance, exposure, and timing confidence | Promotion Governance | `IN_PROGRESS` | MEDIUM | FR-028, FR-037, FR-038 | tracked_via_children | Partially implemented through FR-037 (Tier 3 promotion governance surfaces) and FR-038 (governance blocker audit + diagnostic surfaces). Both are `DEPLOYED_OBSERVING`. FR-029 itself transitions to `DEPLOYED` only after both children satisfy their observation criteria. Historical intent (consume provenance, exposure, and timing confidence in promotion gates) is preserved and now realized via deterministic six-gate evaluation, regime attribution, research-only allocation evaluation, blocker classification, and deterministic maturity scoring. | Revert promotion-readiness checks to existing scorecard criteria; see FR-037 and FR-038 rollback references for code-level reversal. |
| FR-031 execution integrity contract | Execution Integrity | `DEPLOYED_OBSERVING` | HIGH | HOTFIX-2026-05-27, FR-021, broker/order evidence | observing | Additive validator deployed; writes `audit/execution_integrity.json` and compact operator-summary integrity fields. June 9 execution-integrity patches are deployed: fractional quantities now survive executable-order/shadow-order/broker-payload construction (`e249f61`), and sell-leg runs rebuild buys after confirmed sell proceeds with `post_sell_rebudget_<date>.json` evidence (`aaf5961`). June 12 target-attainment MCP diagnostic (`4b426a0`) is deployed and classifies target-attainment status/warnings without changing execution. Cron timing, model logic, risk caps, and broker safeguards remain unchanged. | Revert FR-031 implementation commits; ignore existing audit artifacts. For June 9 patches, revert `e249f61` or `aaf5961` only if the specific execution invariant regresses. |
| FR-032 execution lifecycle observability hardening | Execution Observability | `DEPLOYED_OBSERVING` | LOW | FR-031, 2026-05-28 recovery evidence, 2026-06-12 artifact timing investigation | observing | Backfill or generate lifecycle timeline artifacts from existing run artifacts when missing; improve latest-run/operator timeline usability without invoking execution. June 12 post-buy artifact timing patch delays post-buy snapshot capture until buy fills terminal/timeout, which resolved the cash-discrepancy investigation as `ARTIFACT_TIMING_FAILURE` rather than a failed sell-first rebudget. Next live-run validation must confirm the new `post_buy` capture stage. | Revert helper commit; delete only helper-generated timeline artifacts if they were created for validation. |
| FR-033 dashboard/operator asset alignment | Operator Surfaces | `BACKLOG` | LOW | FR-031, dashboard architecture review, dashboard auth recovery | not_started | Resolve stale dashboard execution-integrity asset tests or align them to the current dashboard architecture without redesigning dashboard UI. Dashboard auth recovery now has a documented `scripts/reset_dashboard_auth.sh` path backed by nginx basic auth and local curl validation; no dashboard runtime behavior changed. | Revert test/asset alignment commit; preserve dashboard publishing behavior. |
| FR-034 post-submit cash drift reconciliation review | Execution Accounting Review | `READY` | LOW | FR-031, latest paper execution artifacts | ready_for_audit | Determine whether `cash_target_drift` clears after fills/reconciliation, represents expected pending-fill drift, or indicates accounting/reconciliation mismatch. | Docs-only/audit rollback; no runtime behavior should change. |
| FR-035 execution contract documentation hardening | Execution Documentation | `PROMOTION_READY` | LOW | 2026-05-28 recovery, FR-031 | local_validation_pending | Canonicalize execution source, price basis, freshness scope, fail-closed boundaries, and operator provenance semantics. | Revert docs commit; runtime behavior unchanged. |
| FR-036 MCP Phase 7 — research-question capability router | Research MCP | `DEPLOYED_OBSERVING` | LOW | FR-015 / FR-017 / FR-018 / FR-024–FR-030 telemetry, registry semantics | observing | Read-only operator MCP shipped 2026-05-29 and has since expanded to 27 registered tools. Current deployed capabilities include deterministic regex classifier + artifact pre-check + tool dispatch (OK / NEEDS_DATA / NEEDS_CAPABILITY / UNSUPPORTED_INTENT), operator gateway at `scripts/research_mcp_ask.py`, `execution_target_attainment`, FR-069 sleeve inventory, attribution, stable-window, and strategy-aware promotion readiness. No transport beyond local stdio, no LLM, no execution-path coupling. | Revert MCP Phase 7 commits; delete `research_registry/research/`, `scripts/research_mcp_ask.{py,sh}`, and `outputs/research_mcp/`. Server reverts to 16-tool Phase 6 surface. |
| FR-036a MCP conformance audit vs frozen semantics layer | Research MCP | `BACKLOG` | LOW | FR-036 deployed, SEM-001..008 frozen | not_started | Produce a clause-by-clause audit doc mapping each frozen SEM contract to the MCP module that implements it (or to the gap). Pure documentation work; no implementation changes. Registry status normalized to match this backlog state on 2026-06-16. | Delete audit doc only; implementation untouched. |
| FR-036b MCP `attribution_analysis` capability promotion | Research MCP | `DEPLOYED_OBSERVING` | LOW | FR-036 deployed, `outputs/attribution/` artifacts, existing attribution ingestion family | observing | `attribution_analysis` is implemented and registered in the deployed MCP schema/tool router. It remains read-only and consumes existing attribution artifacts. | Revert attribution loader + tool commits; capability returns to NEEDS_CAPABILITY. |
| FR-036c MCP `stable_window_evaluation` capability promotion | Research MCP | `DEPLOYED_OBSERVING` | LOW | FR-036 deployed, `outputs/research/stable_window_evaluation/` artifacts | observing | `stable_window_evaluation` is implemented and registered in the deployed MCP schema/tool router. It remains read-only and summarizes existing alpha-lab stable-window artifacts. | Revert window loader + tool commits; capability returns to NEEDS_CAPABILITY. |
| FR-036d MCP strategy-aware promotion readiness drill-down | Research MCP | `DEPLOYED_OBSERVING` | LOW | FR-036 deployed, existing `promotion_readiness` tool | observing | `promotion_readiness` now accepts explicit `strategies` input and returns strategy-aware panels while preserving backward-compatible top-level fields. It remains read-only and advisory. | Revert the strategy-arg patch; tool returns to generic mode. |
| FR-037 Tier 3 promotion governance surfaces | Promotion Governance | `DEPLOYED_OBSERVING` | LOW | FR-028 Phase C, FR-024..027, FR-030, planned consumer FR-038 | observing | Three additive research-only surfaces deployed 2026-06-02: `research/promotion_governance.py` evaluates six gates (observation window / performance / differentiation / risk / universe / execution timing) → `PROMOTE` / `WATCH` / `HOLD` / `DEMOTE` / `BLOCKED`; `research/regime_attribution.py` classifies seven SPY-derived regimes with no look-ahead; `research/dynamic_strategy_allocation.py` evaluates five candidate policies as research-only, never writing production weights. Wired into `research/review_packet.py` with a conservative final control summary (`No promotion recommended` unless every tier agrees and governance explicitly names a candidate). Implementing commits: `c52ae6c` and `fbd4f6a`. | Revert commits `c52ae6c` and `fbd4f6a`; delete `outputs/research/{promotion_governance,regime_attribution,dynamic_strategy_allocation}/`; final control summary falls back to Tier 2 controls. No production allocations or execution behavior change either way. |
| FR-038 governance blocker audit + diagnostic surfaces | Promotion Governance | `DEPLOYED_OBSERVING` | LOW | FR-037 outputs, Tier 1/2 research artifacts | observing | Six additive research-only diagnostic surfaces deployed 2026-06-02: `research/governance_blocker_audit.py` classifies every governance blocker as `REAL` / `DATA_QUALITY` / `CONFIGURATION` / `OBSERVATION_WINDOW`; `research/security_master_reconciliation.py` reconciles holdings/planned/attribution/timing symbols vs the security master; `research/execution_payload_audit.py` diagnoses `planned_execution_payload` state across five hypotheses; `research/differentiation_diagnostic.py` per-pair breakdown with verdicts `TRUE_WEAK_DIFFERENTIATION` / `POSSIBLE_DATA_LIMITATION` / `INSUFFICIENT_HISTORY`; `research/concentration_diagnostic.py` distinguishes actual violations from equal-weight design floors; `research/governance_maturity.py` produces a deterministic 7-component score → `IMMATURE` / `EMERGING` / `DEVELOPING` / `MATURE` / `PROMOTION_READY`. Wired into `research/review_packet.py` final control summary as `blockers_eliminated` / `blockers_remaining` / `data_quality_issues` / `actual_strategy_issues` / `governance_maturity_tier`. Implementing commits: `436cbdf` and `fbd4f6a`. | Revert commits `436cbdf` and `fbd4f6a`; delete `outputs/research/{governance_blocker_audit,security_master_reconciliation,execution_payload_audit,differentiation_diagnostic,concentration_diagnostic,governance_maturity}/`; final control summary falls back to Tier 3-only roll-up (FR-037). No production allocations or execution behavior change either way. |
| FR-055 Intended Portfolio NAV & Operational Drag Attribution | Performance Provenance / Operational Telemetry | `DEPLOYED_OBSERVING` | LOW | 2026-06-04 operational drag audit, existing planned portfolio/execution artifacts, broker/reconciliation/performance artifacts | observing | Read-only intended/counterfactual NAV, normalized actual NAV, SPY benchmark alignment, operational drag attribution, stable-window analysis, CLI generation, and research-packet consumption are deployed. June 9 freshness repairs (`d13e804`, `c55f2ba`, `67911c9`) restore current-date decision-grade output; 2026-06-09 latest aligned date is 2026-06-09 with MEDIUM confidence. | Revert FR-055 implementation and June 9 repair commits; ignore/delete generated `outputs/operational_drag/<date>/` artifacts. No broker, execution, cron, strategy selection, allocation, or order-routing behavior changes are in scope. |
| FR-056 Operational Drag Source Discovery Patch | Performance Provenance / Operational Telemetry | `DEPLOYED_OBSERVING` | LOW | FR-055, canonical VM price/NAV/broker/reconciliation/SPY artifacts | observing | Source discovery/readers now locate canonical VM price, holdings, reconciliation, NAV, and SPY sources with explicit source-selection diagnostics. June 9 repairs add source paths, source dates, stale components, blocking components, and visible degradation for missing inputs. | Revert FR-056 reader patch; FR-055 artifacts remain read-only and degraded with missing-data reason codes. No execution, broker, cron, strategy, allocation, or promotion behavior changes are in scope. |
| FR-057 Current Price Hydration for Operational Drag | Performance Provenance / Operational Telemetry | `IN_PROGRESS` | LOW | FR-055, FR-056, existing price hydration/export infrastructure | implementation_started | Hydrate or expose current trade-date price data for intended/actual holdings and SPY so operational drag can mark holdings without stale price gaps. status_review_needed; no deployment evidence in this backlog. | Revert FR-057 hydration/read-order patch; ignore/delete date-scoped `outputs/operational_drag/<date>/price_hydration.*` artifacts. No execution, broker, cron, strategy, allocation, promotion, or live trading behavior changes are in scope. |
| FR-058 Actual NAV Refresh for Operational Drag | Performance Provenance / Operational Telemetry | `DEPLOYED_OBSERVING` | LOW | FR-055, FR-056, FR-057, broker-authoritative live-overlay NAV producer, run-scoped NAV snapshots | observing | Operational-drag actual-NAV discovery selects the freshest, highest-confidence broker NAV source, merges current broker/run artifacts, and emits explicit `actual_nav_from_*` / `actual_nav_stale` / `actual_nav_missing` reason codes. June 9 current-date attribution no longer stops at stale historical NAV when current broker/run evidence exists. | Revert FR-058 discovery patch; actual-NAV discovery falls back to FR-055 fixed-order readers. No execution, broker, cron, strategy, allocation, promotion, or live trading behavior changes are in scope. |
| FR-059 Broker Telemetry Failure Detection | Operational Telemetry / Service Health | `IN_PROGRESS` | LOW | FR-058A audit (Alpaca 401 silent freeze 2026-05-20→2026-06-04), `scripts/refresh_quant_dashboard.py`, `deploy/caerus-dashboard-refresh.service` | implementation_started | Make Alpaca live-broker telemetry/auth failures loud and alertable: classify failures into reason codes (`alpaca_auth_failed`, `live_broker_required_failed`), add deterministic stale-artifact checks (`nav_artifact_stale`, `broker_snapshot_stale`, `recon_artifact_stale`), surface a structured `live_status` in the refresh output, and enable `--require-live-broker` in the systemd service so bad credentials exit non-zero instead of exit-0 with a swallowed warning. status_review_needed; no deployment evidence in this backlog. | Revert FR-059 patch + remove `--require-live-broker` from the service unit (sudo cp + daemon-reload); refresh reverts to warn-and-continue. No order execution, broker submission, allocation, strategy, or promotion behavior changes are in scope. |
| FR-060 Intended NAV True Mark-to-Market | Performance Provenance / Operational Telemetry | `IN_PROGRESS` | LOW | FR-055, FR-058, daily precompute plan snapshots, hydrated/historical price store | implementation_started | Fix intended-NAV so `intended_return_daily` is not mechanically 0.0: mark the carried intended holdings to market at each day's prices to derive the rebalance basis, so price moves since the last rebalance flow into the intended return instead of being erased by a same-day target reconstruction. Carry holdings forward between rebalances; no look-ahead; no fabricated prices; keep the same-day reconstruction as a clearly-labeled fallback when carried holdings cannot be priced. status_review_needed; no deployment evidence in this backlog. | Revert FR-060 patch; intended NAV reverts to same-day reconstruction (drag = 0 − actual). No execution, broker, allocation, strategy, or promotion behavior changes are in scope. |
| FR-061 Operational Drag Reporting Cleanup | Performance Provenance / Operational Telemetry | `DEPLOYED_OBSERVING` | LOW | FR-055..FR-060, `research/review_packet.py` operational-drag section | observing | Operational-drag output now classifies reason codes into current-date, historical, window, material, and non-material groups, plus `current_date_status`, `decision_grade`, and a decision-grade explanation. June 9 output is decision-grade with MEDIUM confidence while preserving historical caveats. | Revert FR-061 patch; consumers fall back to the flat `reason_codes` list. No execution, broker, allocation, strategy, or promotion behavior changes are in scope. |
| FR-062 Reconciliation Drift Investigation and Patch | Performance Provenance / Operational Telemetry | `DEPLOYED_OBSERVING` | LOW | FR-059..FR-061, run-scoped broker/reconciliation artifacts | observing | Current-date operational-drag reconciliation blockers now distinguish true broker/model drift from artifact selection/parser over-classification. Split account/position artifacts and `normalized_positions` dictionaries are parsed; gross exposure can be derived from cash/equity when direct exposure is absent. | Revert FR-062 diagnostic/parser patch; ignore the date-scoped reconciliation drift diagnostic artifact. No execution, broker submission, allocation, strategy, or promotion behavior changes are in scope. |
| FR-050 Phoenix Phase B Historical Behavior Review | Investment Confidence / Research Evidence | `NOT_VIABLE_CURRENT_PHASE_B` | LOW | Existing Phoenix research artifacts, VIX/regime data, price panels, shadow snapshots, FR-069 Phase C onboarding, Sharadar SEP OHLCV cache, PIT liquidity panel | liquidity_capacity_failed | Phoenix is differentiated and risk-shaped, and the prior Nasdaq Data Link `QELx06` blocker is cleared. The 2026-06-18 OHLCV rebuild hydrated 1,600 PIT large-cap tickers with 7,845,012 rows and no empty/failed tickers; Phase C measured 80/80 candidate rows but classified Phoenix `NOT_VIABLE` for `capacity_below_5pct_adv_policy` at `$1M` reference capital. Phoenix remains Research-stage; no logic, output, Shadow, allocation, execution, broker, risk, cron, or promotion behavior changes. | Revert the Phase B/Phase C research modules/CLI/tests/onboarding docs; ignore generated Phoenix evidence artifacts. No strategy, execution, broker, cron, allocation, or promotion behavior changes are in scope. |
| FR-053 Argo Phase B Regime Selection Validation | Investment Confidence / Research Evidence | `ACTIVE_RESEARCH` | LOW | Existing Argo selection artifacts, model tournament, promotion readiness, VIX/regime data, FR-069 Phase C onboarding, Phase A evidence framework, Phase B research-priority framework | phase_b_research_priority_added | Validate Argo as a research-only regime overlay/model-selection layer, including stability, transition diagnostics, input freshness, no-lookahead checks, evidence consumption, and advisory research prioritization. Argo is now represented as a governed FR-069 Research-stage meta-model candidate via `docs/governance/fr_active/fr_069_argo_onboarding_packet.md`, evidence template `docs/governance/fr_active/fr_069_argo_evidence_envelope_template.json`, Phase A evidence-consumer framework `docs/governance/fr_active/fr_069_argo_phase_a_evidence_framework.md`, and Phase B research-priority framework `docs/governance/fr_active/fr_069_argo_phase_b_research_priority_framework.md`; this does not activate allocation switching, promotion, retirement, or runtime behavior. | Revert the Phase A/Phase B research frameworks, Phase B validation module/CLI/tests/onboarding docs; ignore generated `argo_phase_a_evidence_framework.*`, `argo_phase_b_research_priority.*`, and `argo_phase_b_validation.*` artifacts. No capital routing, promotion, retirement, or production selection behavior changes are in scope. |
| FR-052 Cassiopeia Event-Driven Spec | Research / Event-Driven Strategy Spec | `ACTIVE_RESEARCH` | LOW | `docs/governance/fr_archive/fr_052_cassiopeia_research_spec.md`, FR-069 Phase C onboarding | phase_c_research_onboarded | Canonical event-driven strategy spec remains spec-only and active backlog maintenance is tracked here until an explicit implementation decision is made. Cassiopeia is now represented as a governed FR-069 Research-stage event-driven candidate via `docs/governance/fr_active/fr_069_cassiopeia_onboarding_packet.md` and evidence template `docs/governance/fr_active/fr_069_cassiopeia_evidence_envelope_template.json`; this does not activate Shadow or runtime behavior. | Revert documentation links/onboarding docs only. No strategy, execution, broker, cron, allocation, or promotion behavior changes are in scope. |
| FR-063 Strategy Differentiation Deep Dive | Investment Confidence / Research Evidence | `ACTIVE_RESEARCH` | LOW | Shadow snapshots, attribution, model tournament, promotion readiness, strategy registry, FR-069 sleeve architecture | active_supporting_evidence | Active supporting differentiation evidence under FR-069. Historical and current artifacts suggest Orion and Lyra are highly correlated/redundant, with Lyra the current low-confidence watch-list leader, but no final retain/retire decision is approved. Future conclusions require sufficient canonical new-series history under `dated_same_day_close_to_close_v1`; disposition belongs inside FR-069's data-driven promotion/retirement framework. Research-only redundancy study spec added at `docs/governance/fr_active/fr_063_orion_lyra_redundancy_study.md`; current FR-069 governance packet lives at `docs/governance/fr_active/fr_069_orion_lyra_redundancy_packet.md`. | Revert the deep-dive module/CLI/tests/spec docs; ignore generated `strategy_differentiation_deep_dive.*` artifacts. No strategy retirement, promotion, Lyra-name reuse, or execution behavior changes are in scope. |
| FR-064 Multi-Asset Research Framework | Investment Confidence / Research Design | `DRAFT_RESEARCH` | LOW | Strategy registry, data inventory, existing price artifacts | design_audit_started | Create a non-executional audit framework for evaluating Treasury duration, cash/T-bills, gold, commodities, managed-futures proxies, defensive equity ETF proxies, and deferred options-overlay design questions. | Revert the framework doc/module/CLI/tests; ignore generated `multi_asset_research_framework.*` artifacts. No trading, allocation, or production order generation is in scope. |
| FR-065 Dashboard Decision-Grade Consolidation | Investment Confidence / Operator Surface | `ACTIVE_RESEARCH` | LOW | Model-quality artifacts, dashboard data builder, terminal dashboard assets | implementation_started | Add a compact dashboard data-model and terminal panel section summarizing decision-grade readiness, research confidence, latest model-quality evidence, blockers, and source paths. | Revert dashboard data/UI/test changes; dashboard falls back to existing broker-authoritative panels. No broker truth, execution, planned-trade, or warning behavior changes are in scope. |
| FR-066 Canonical NAV Track Record Integrity | Operational Telemetry / Performance Provenance | `DEPLOYED_OBSERVING` | LOW (telemetry-only) | FR-059 reason codes, Alpaca portfolio-history endpoint, existing `build_portfolio_history.py` / benchmark CSV / broker snapshots | vm_backfill_and_cron_installed | Backfill dry-run/write completed on VM using VM `.env` without printing credentials; canonical rows are continuous from 2026-03-03 through the current builder date, SPY/beta columns are populated, and the 7:15 PM ET builder/escalation cron is installed to `logs/portfolio_history.cron.log`. This is canonical portfolio/broker-history NAV work and is distinct from the recovered operational Shadow NAV observation series. Caveat: Alpaca portfolio-history Apr 8 canonical NAV is `$9,751.97`, not the older `$9,715.45` baseline; broker snapshot reconciliation remains non-clean because those snapshots are point-in-time account captures, while `nav.csv` overlap is clean. | Revert the four module changes + crontab line; delete `backfill_manifest.json`, `checksum_manifest.json`, and added NAV columns; existing nav rows are never deleted. No execution, broker submission, allocation, strategy, or promotion behavior is in scope. |
| FR-051 Cygnus Wave 1 (v0 event-reaction) | Research / Earnings Drift | `SHELVED` | LOW (research-only) | FR-051 spec + 2026-06-10 addendum, EDGAR submissions API, `cik_mapping_results.csv`, `paper/trading_calendar.py`, FR-069 Phase C onboarding | phase_c_research_onboarded_v0_shelved | Stage 1 delivered the PIT EDGAR event tape. Stage 2 v0 validation failed 4/6: Rank IC 10D IC 0.0318 with t-stat 1.59 (FAIL), IC 20D/60D decay PASS, net IR vs SPY at 25 bps 0.44 PASS, excess correlation vs Polaris proxy 0.043 PASS, event coverage 1.05 PASS, cost sensitivity at 50 bps IR -0.32 FAIL. Tune window also failed. v0 is shelved, not re-tuned; 2025-forward holdout remains untouched; v1 requires EPS-surprise / consensus data. Cygnus is now represented as a governed FR-069 Research-stage candidate via `docs/governance/fr_active/fr_069_cygnus_onboarding_packet.md` and evidence template `docs/governance/fr_active/fr_069_cygnus_evidence_envelope_template.json`; this does not reactivate v0, activate Shadow, or change runtime behavior. | Delete `research/cygnus/`, `scripts/research/run_cygnus_research.py`, `Tests/test_cygnus_events.py`, dated `outputs/research/cygnus/` artifacts, and onboarding docs if reverting FR-069 Phase C. No execution, broker, registry, allocation, or paper/live behavior is in scope. |
| FR-068 PIT Universe + Polaris/Orion/Lyra Rebaseline | Research / Survivorship Remediation | `PHASES_1_4_COMPLETE` | LOW (research-only) | Sharadar (FR-067), PIT universe + caerus_large_cap family + SEP cache, Orion/Lyra matched PIT artifact | orion_lyra_matched_pit_generated | Phase 1 PIT universe (20,618 secs, 14,790 delisted) + `Universe(as_of_date)`; Phase 2 impact (SEVERE; 71.7% delisted); Phase 2.5 caerus_large_cap family (1,600; 354 delisted) + full SEP price hydration; Phase 3 Polaris priced rebaseline on the committed momentum harness = **MATERIAL** (Sharpe 1.054→0.851, MaxDD −43%→−54%; CAGR 28.83%→30.68%). Legacy = non-decision-grade; promotion evidence must carry `universe_method=pit_universe`. Phase 4 Orion/Lyra matched PIT artifact at `outputs/research/pit_rebaseline/orion_lyra_matched_2026-06-17.json` finds no statistically meaningful Lyra lead over 2,767 matched pre-holdout observations. Date-effective certified large-cap membership and index membership families are later phases. | Revert rebaseline research modules + artifacts and the Orion/Lyra packet if needed; PIT data is gitignored/regenerable. No execution, model, cron, registry, or holdout change in scope. |
| FR-069 Research Lab / Modular Sleeve Architecture | Architecture / Design | `PHASE_B_IMPLEMENTED_RESEARCH_ONLY` | NONE (research-only scaffolding) | FR-068 PIT foundation, existing sleeve specs (FR-050..053, 067), alpha_lab harness, `docs/governance/caerus_investment_doctrine.md` | phase_c_readiness_documented | Next major architecture/research workstream after FR-070 observation. Phase B research-only scaffolding is documented in `docs/governance/fr_active/fr_069_phase_b_scaffolding.md` and implemented through `research_registry/sleeves/manifest.json`, the manifest validator, read-only MCP `fr069_sleeve_inventory`, and the Phase B2 static evidence-envelope validator. Phase C readiness is documented in `docs/governance/fr_active/fr_069_phase_c_readiness.md` as lifecycle gates, sleeve gap matrix, evidence-envelope minimums, and onboarding acceptance criteria. No production refactor; no runtime strategy-registry behavior change; Orion/Lyra continue evaluation; FR-063 remains active supporting evidence, not retired. Phase C implementation still requires separate owner approval. | Revert the Phase B scaffold/readiness docs commit or delete the research-only manifest/evidence/docs/tests/tool registration. No execution, broker, allocation, portfolio construction, model, strategy, cron, live-capital, or secret behavior is in scope. |

**Pilot evidence lane status:** FR-100/101/102/103 remain valid blockers for
Level 3 pilot-capital conclusions, scaling, and production-adjacent deployment.
They are not global stop-work orders for FR-104 Level 2.5 pilot evidence
collection. A Level 2.5 run may continue only when it is manually approved,
tightly capped, dry-run-first, artifact-isolated, broker-truth captured, no
cron, no dynamic allocation, and explicitly non-promotional. FR-068 incomplete
historical replay blocks decision-grade historical conclusions and promotion;
it does not by itself block forward evidence collection under FR-104 controls.

| FR-070 Cash Gating and Post-Sell Buy Budget Reconciliation | Execution Integrity / Cash Deployment | `DEPLOYED_OBSERVING` | HIGH | Sell-first execution path, posttrade state capture, capital-budget rebudgeting, execution contract guardrails, target-attainment telemetry, FR-031 target-attainment diagnostic | observation_monitoring | The June 12 remediation implementation is deployed: MCP target-attainment diagnostics, post-buy artifact timing, and explicit validation gates are in place. The June 12 execution cash discrepancy was `ARTIFACT_TIMING_FAILURE`, not failed sell-first rebudgeting, and is separate from the resolved Shadow NAV incident. FR-070 remains the highest immediate operational observation priority but no longer owns an active implementation lane. Reopen only for a stale/pre-buy posttrade snapshot, buy timeout/failure, unclassified cash drift, reconciliation/target-attainment contradiction, or achieved cash materially outside tolerance without a classified reason. Validation criteria for the next live run: `buy_phase_status=BUY_PHASE_COMPLETED` or a terminal timeout/fail state, `posttrade_snapshot_stage=post_buy`, `pending_buy_count=0` when buys fill, `achieved_cash_weight` within tolerance of `target_cash_weight`, and MCP target-attainment status `OK_TARGET_ATTAINED` or a properly classified warning. | Revert the post-buy timing patch and target-attainment diagnostic if the specific execution invariant regresses; preserve execution safeguards and sell-first rebudgeting. |
| HOTFIX-2026-06-15-FR070 execution fill observation | HOTFIX / Execution Integrity | `DEPLOYED_OBSERVING` | HIGH | FR-070, FR-031, broker-authoritative Alpaca order state, sell-first lifecycle | observing_next_buy_capable_run | 2026-06-15 paper run `2026-06-15T093505-0400_c68a22d` reported submitted=2, accepted=2, filled=0, `NOT_COMPARABLE`, no halt/skip reason, and `EXECUTED`; operator-supplied Alpaca truth shows C SELL 1 filled at 09:36:55 ET and MNST SELL 2 filled at 09:38:27 ET. Root cause: sell observation used a 90s primary window with no bounded recovery window before lifecycle/reporting decisions; incomplete sell terminality could suppress buys while reporting semantics remained too green. Patch is deployed through execution fix `2e1c3f1` and VM validation passed; observe the next buy-capable run for terminal sell/buy state, post-buy artifacts, and target-attainment consistency. | Revert the hotfix commit only if the observation invariant regresses; restore prior sell observation timeout behavior. Do not delete incident artifacts or execution history. |
| FR-071 Governance Doctrine Integration | Governance Documentation | `READY` | NONE | `docs/governance/caerus_investment_doctrine.md` | not_started | Documentation-only task to ensure doctrine is referenced from README, roadmap, registry, AGENTS.md, and future FR workflow. | Revert documentation links only. |
| FR-072 Governance Hygiene Agent | Governance Automation / Operational Risk Reduction | `DEPLOYED_OBSERVING` | NONE | `docs/governance/caerus_investment_doctrine.md`, `docs/governance/fr_registry.md`, `docs/governance/fr_active_backlog.md`, `docs/governance/CURRENT_RESEARCH_ROADMAP.md`, `docs/governance/README.md`, `AGENTS.md` | read_only_observing | Read-only governance audit is implemented with deterministic checks and tests. It proposes findings only, has no automatic writes, no auto-patch, and no cron install. Phase B scheduling still requires separate explicit approval. | Revert docs and script only; delete output artifacts if needed. |
| FR-073 Sleeve Numeric Diagnostics and Cash-Routing Explainability | Allocation Observability / Numeric Diagnostics | `DEPLOYED_OBSERVING` | LOW | FR-070 target-attainment distinction, sleeve validity checks, run-root audit artifacts | observing_next_invalid_sleeve_event | Diagnostic-only patch deployed at `efd193d` after the 2026-06-17 trend-sleeve terminal-equity NaN incident. Invalid sleeve numeric states now write run-root traces at `outputs/runs/<RUN_ID>/audit/sleeve_numeric_trace_<sleeve_id>_<trade_date>.json` when a run context is available, and cash-routing diagnostics/reporting include invalid sleeve, reason, routed weight, and trace path. The patch preserves cash routing, allocation decisions, execution, broker behavior, sizing, ranking, and risk thresholds. | Revert `efd193d` only if trace generation or reporting causes runtime failure; cash-routing safety should remain governed by sleeve validity. |
| FR-074 Execution Reliability Framework | Execution Reliability / Operational Invariants | `DEPLOYED_OBSERVING` | LOW | FR-031 execution integrity, FR-070 target-attainment diagnostics, FR-073 sleeve numeric traces, June 19 planned-payload handoff invariant | observing_phase_a | Phase A observe-first framework centralizes operational invariants in `core/operational_invariants.py` and writes `outputs/runs/<RUN_ID>/audit/execution_reliability_report_<TRADE_DATE>.json`. Report rows carry invariant id, status, severity, reason code, human summary, operator action, and evidence; the deterministic score is surfaced in operator summary but is not used for trading decisions. No strategy, sleeve, sizing, allocation, cash policy, broker submission, or runtime selection semantics changed beyond preserving the already-required planned-payload fail-closed invariant. | Revert FR-074 module, execution-path report write, tests, and governance rows; preserve FR-031/FR-070/FR-073 safeguards and artifacts. |
| FR-085 Shadow Scorecard Publication Integrity Gates | Reporting Integrity / Governance | `DEPLOYED_OBSERVING` | LOW | `scripts/send_shadow_cio_report.py`, `shadow_nav_series.csv`, `shadow_evaluation.json`, price-hydration `status.json`; provenance context FR-024/FR-066; HOTFIX-2026-06-24 recovery/restatement | observing_tonight_scorecard_and_next_cycle | Durable publication gates for the Shadow CIO scorecard so it cannot publish a leaderboard from stale or internally inconsistent research artifacts. Freshness gate withholds rankings/leader/promotion/CIO-takeaway on failed shadow refresh, NAV age beyond tolerance, or corrupt NAV integrity; internal-consistency gate excludes any sleeve from ranking/leader unless its period return is NAV-derived and `valid_day_count >= 10`. Deployed through PR #117 and `main` `993ee4d`; incident recovery/restatement lives in HOTFIX-2026-06-24. | Revert PR #117 for the scorecard gate regression only; scorecard reverts to prior publish-on-stale behavior. No execution, broker, allocation, cron, or price-loading behavior changes either way. |
| HOTFIX-2026-06-24 shadow scorecard stale/misattributed leaderboard | HOTFIX / Research Reporting Integrity | `DEPLOYED_OBSERVING` | MEDIUM | FR-085 durable scorecard gates, PR #117, PR #118, Shadow NAV restatement evidence | observing_tonight_refresh_and_next_cycle | Reclassified from FR-086 to the registry Hotfix Records. Incident remediation is deployed in `main` `993ee4d`: FR-085 publication gates plus PR #118 NAV refresh fix/date-aware alpha inception handling and one-time restatement. Canonical hotfix record lives in `docs/governance/fr_registry.md`. | Parent durable contract is FR-085. Revert PR #117 and/or PR #118 for specific regressions; restore the preserved pre-repair active NAV file for artifact rollback; preserve recovery/incident artifacts. |

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

## Current Priority Order

1. Observe FR-070 target-attainment and post-buy artifact timing through the
   next live/paper execution gates, and observe FR-073 trace coverage on any
   future invalid-sleeve event. These are operational observation lanes, not
   implementation lanes, unless they produce classified failure evidence.
2. FR-069 modular sleeve / research-lab architecture aligned to the Caerus
   Investment Doctrine is the next major architecture workstream.
3. FR-070 implementation work only if diagnostics produce a classified failure.
4. Orion/Lyra continued evaluation as part of sleeve architecture, not as a
   standalone immediate retirement decision.
5. FR-063 remains active supporting differentiation evidence and must use
   sufficient canonical new-series history before retirement conclusions.
6. Phoenix is no longer externally blocked by Nasdaq Data Link `QELx06`;
   Sharadar SEP OHLCV access was restored and rebuilt into PIT liquidity
   evidence on 2026-06-18. The current Phase B candidate is
   `NOT_VIABLE_CURRENT_PHASE_B` because Phase C capacity failed the 5% ADV
   policy; Cassiopeia, Cygnus, and Argo remain Research-stage under FR-069
   onboarding packets. Argo Phase A may consume evidence for research
   classifications only.

## Priority Decision Note — 2026-06-12

- FR-070 remediation is deployed and moves to observation/monitoring. It is not
  complete until next-run evidence satisfies the validation gates and remains
  the highest immediate operational observation priority.
- FR-069 is the next major architecture workstream because the Investment
  Doctrine requires a modular sleeve architecture.
- Orion and Lyra are likely redundant / highly correlated, but the final
  retain/retire decision remains data-driven.
- FR-063 remains active supporting differentiation evidence; it is not a
  retirement action and cannot combine legacy mixed-convention Shadow history
  with the canonical operational observation series.
- No immediate retirement of Orion or Lyra is approved.
- No reuse of the Lyra name is approved yet.
- This note supersedes informal discussion but does not override future
  data-driven evidence.

## FR-055 Intended Portfolio NAV & Operational Drag Attribution

- **FR number:** FR-055
- **Title:** Intended Portfolio NAV & Operational Drag Attribution
- **Date started:** 2026-06-04
- **Status:** `DEPLOYED_OBSERVING`
- **Problem statement:** Caerus reports actual portfolio performance versus SPY
  but does not persist an intended/counterfactual NAV series. When broker
  holdings diverge from the model's intended holdings, strategy performance and
  operational drag cannot be separated directly.
- **Scope:** Add a read-only deterministic generator for intended NAV, actual
  NAV normalization, SPY benchmark alignment, operational drag calculations,
  incident attribution, stable-window summaries, a CLI entry point, and
  research-packet integration when artifacts are present.
- **Non-goals:** No live broker calls, execution behavior changes, broker
  submission changes, cron changes, strategy promotion/demotion, allocation
  changes, look-ahead price use, silent imputation, or fabricated post-April
  data.
- **Planned artifacts:** `outputs/operational_drag/<trade_date>/intended_nav.json`,
  `intended_nav_timeseries.csv`, `actual_nav.json`,
  `actual_nav_timeseries.csv`, `benchmark_nav.json`,
  `operational_drag.json`, `operational_drag_timeseries.csv`,
  `operational_drag_attribution.json`, `stable_window_analysis.json`, and
  `stable_window_analysis.md`.
- **Validation plan:** Add fixture-based tests for intended NAV, actual NAV,
  SPY date alignment, intended-minus-actual drag math, under-deployment
  attribution, missing-data reason codes, unavailable stable windows, and CLI
  artifact writes. Run targeted pytest, py_compile for changed Python files,
  and `git diff --check`.
- **Risks / assumptions:** Local artifact coverage may be sparse after April;
  intended holdings may come from target weights or planned execution payloads
  with incomplete price coverage; outputs must expose these gaps instead of
  imputing values. MCP integration may be deferred if the existing research MCP
  surface would require a larger change than the artifact generator.
- **Current June 9 state:** Deployed and observing. Current-date operational
  drag reaches 2026-06-09 and is decision-grade with MEDIUM confidence after
  the June 9 freshness and source-lineage repairs. The artifact remains
  read-only and does not affect execution, broker submission, cron, model
  logic, risk controls, allocation, or promotion.

## FR-056 Operational Drag Source Discovery Patch

- **FR number:** FR-056
- **Title:** Operational Drag Source Discovery Patch
- **Date started:** 2026-06-04
- **Status:** `DEPLOYED_OBSERVING`
- **Problem statement:** FR-055 operational drag runs and writes the expected
  artifact family, but it cannot locate canonical VM price, holdings,
  reconciliation, NAV, and SPY artifacts reliably, producing `LOW` confidence
  and avoidable missing-data reason codes.
- **Scope:** Source discovery and artifact readers only in
  `research/operational_drag.py`, plus fixture coverage in
  `Tests/test_operational_drag.py`.
- **Non-goals:** No execution changes, strategy changes, broker/API calls,
  cron changes, allocation changes, fabricated data, SPY imputation, or
  promotion-rule changes.
- **Planned artifacts:** Improved `source_diagnostics` fields in FR-055
  outputs, selected-source paths for price/NAV/SPY/broker/reconciliation
  readers, and unchanged `outputs/operational_drag/<date>/` artifact family.
- **Validation plan:** Run the FR-055 operational-drag test suite, affected
  research packet/MCP tests, py_compile on changed Python files, and
  `git diff --check`; then run `python3 -m research.operational_drag --date
  2026-06-04` against VM artifacts.
- **Risks / assumptions:** Canonical artifacts may vary by VM date or storage
  family. Readers must report candidate paths tried and selected paths rather
  than silently falling back or hiding true missing data.
- **Current June 9 state:** Deployed and observing. Diagnostics now expose
  source paths, source dates, stale components, blocking components, freshness
  status, and explicit missing-input reason codes.

## FR-057 Current Price Hydration for Operational Drag

- **FR number:** FR-057
- **Title:** Current Price Hydration for Operational Drag
- **Date started:** 2026-06-04
- **Status:** `IN_PROGRESS`
- **Problem statement:** FR-055/FR-056 operational drag telemetry can now find
  NAV, broker, reconciliation, and stale price sources, but it still lacks
  requested trade-date prices for many intended/actual symbols and SPY. This
  prevents decision-grade intended-vs-actual-vs-SPY attribution for the
  requested date.
- **Scope:** Reuse existing price hydration/export infrastructure where
  possible to hydrate or expose current trade-date prices for intended
  holdings, actual holdings, planned trades, and SPY; emit deterministic
  date-scoped hydration metadata and let operational drag prefer fresh
  date-scoped prices before stale historical fallbacks.
- **Non-goals:** No execution behavior changes, broker submission changes,
  strategy allocation changes, promotion/demotion logic changes, fabricated
  prices, current-day forward-fill, look-ahead price use, or live trading calls.
- **Planned artifacts:**
  `outputs/operational_drag/<trade_date>/price_hydration.json` and
  `outputs/operational_drag/<trade_date>/price_hydration.md`, plus unchanged
  FR-055 operational drag artifacts consuming the hydrated source when present.
- **Validation plan:** Inspect existing hydration/export code, add fixture tests
  for hydration metadata, date-scoped price priority, SPY inclusion, explicit
  missing symbols, and no forward-fill; run targeted operational-drag pytest,
  affected research packet/MCP tests, py_compile, `git diff --check`, and VM
  generation for 2026-06-04 after hydration.
- **Risks / assumptions:** Current trade-date prices may be unavailable until
  post-close data is hydrated. The hydration source must report coverage and
  missing symbols honestly; stale canonical matrices must remain available as a
  lower-priority fallback without masking freshness gaps.

## FR-058 Actual NAV Refresh for Operational Drag

- **FR number:** FR-058
- **Title:** Actual NAV Refresh for Operational Drag
- **Date started:** 2026-06-04
- **Status:** `DEPLOYED_OBSERVING`
- **Problem statement:** After FR-057 fixed current-date price and SPY
  hydration, intended NAV and benchmark NAV extend through the requested trade
  date (2026-06-04), but actual NAV stops on 2026-05-20, so
  `operational_drag_timeseries.csv` and `operational_drag.json.latest.date`
  also stop on 2026-05-20. Intended-vs-actual-vs-SPY drag therefore cannot be
  computed through the requested date. Evidence: `outputs/perf/live_overlay_nav_series.csv`
  is produced by `scripts/refresh_quant_dashboard.py::refresh_live_performance_artifacts()`
  from the Alpaca account portfolio-history API on a 5-minute systemd timer
  (`deploy/caerus-dashboard-refresh.{service,timer}`). The legacy runtime
  GitHub Actions that previously maintained live broker artifacts were
  deprecated/archived under
  `docs/archive/github_workflows/deprecated_runtime_workflows_2026-05-20/`
  (directory mtime 2026-05-20) — the same date actual NAV stops. The actual-NAV
  stop is therefore a producer/data-production event at the 2026-05-20 runtime
  cutover, not (primarily) an operational-drag reader bug; however, the reader
  also silently truncates at the freshest available date with no staleness
  signal and selects by fixed file order rather than by freshness/confidence.
- **Scope:** (1) Harden operational-drag actual-NAV discovery in
  `research/operational_drag.py` to merge all candidate NAV sources by date,
  resolving same-date conflicts by source confidence (broker-authoritative
  live-overlay > run-scoped snapshots > legacy portfolio_history) while taking
  the union of dates so the series extends to the freshest available date.
  (2) Add `source_diagnostics.actual_nav` with `candidate_paths`,
  `selected_paths`, `max_available_date`, and `failed_paths`. (3) Emit explicit
  reason codes `actual_nav_from_run_snapshot` / `actual_nav_from_live_overlay` /
  `actual_nav_from_portfolio_history` (which class supplied the latest date),
  `actual_nav_stale` (latest available actual date < requested trade date), and
  `actual_nav_missing` (no actual NAV source resolved).
- **Non-goals:** No execution behavior changes, broker submission changes, cron
  or systemd timer changes, strategy/allocation/promotion changes, fabricated or
  forward-filled NAV, look-ahead, or live trading calls. This FR does not itself
  re-run the VM producer or backfill broker NAV; reaching 2026-06-04 actual NAV
  requires the broker-authoritative producer to have generated fresh data. Does
  not start FR-059.
- **Canonical source recommendation (evidence-based):** `live_overlay_nav_series.csv`
  is broker-authoritative (Alpaca portfolio-history API), continuous, and
  self-healing (each producer run rewrites the trailing window), so it is the
  highest-confidence continuous actual-NAV series. Run-scoped
  `snapshots/nav_timeseries.csv` is authoritative per run but fragmentary, and is
  preferred only to *extend* coverage past the last live-overlay refresh.
  `portfolio_history/nav.csv` is the stalest legacy series (lowest confidence).
  Hierarchy: live-overlay (per-date authority) with run-snapshot/portfolio-history
  filling any dates live-overlay lacks; series coverage = union of all sources.
- **Planned artifacts:** No new files. Enriched `source_diagnostics.actual_nav`
  block and new actual-NAV reason codes inside the existing FR-055
  `outputs/operational_drag/<trade_date>/{actual_nav.json,operational_drag.json,*_timeseries.csv}`
  artifacts.
- **Validation plan:** Add fixture tests for freshest-source selection,
  run-snapshot coverage extension, same-date confidence tie-break,
  `source_diagnostics.actual_nav` shape (incl. `max_available_date`), and the
  `actual_nav_from_*` / `actual_nav_stale` / `actual_nav_missing` reason codes;
  run `pytest Tests/test_operational_drag.py`, `py_compile research/operational_drag.py`,
  `git diff --check`. VM acceptance: after the broker-authoritative producer
  refreshes NAV, `python3 -m research.operational_drag --date 2026-06-04` should
  show actual and operational-drag timeseries through 2026-06-04 and
  `operational_drag.json.latest.date == 2026-06-04`.
- **Risks / assumptions:** The reader change cannot create broker NAV that does
  not exist; if all sources end on 2026-05-20 (the expected post-cutover state),
  the patch correctly surfaces `actual_nav_stale` and the residual blocker is VM
  data production (re-run/repair `refresh_quant_dashboard.py` and confirm the
  systemd timer). Confidence ranking must not let a stale source override a
  fresher authoritative value on overlapping dates; the union must not drop the
  freshest dates.
- **Current June 9 state:** Deployed and observing. Actual NAV can extend from
  current broker/run artifacts when live-overlay coverage alone is stale, so
  current-date attribution no longer stops at 2026-04-08/2026-05-20 when
  current run evidence exists.

## FR-059 Broker Telemetry Failure Detection

- **FR number:** FR-059
- **Title:** Broker Telemetry Failure Detection
- **Date started:** 2026-06-04
- **Status:** `IN_PROGRESS`
- **Problem statement:** `scripts/refresh_quant_dashboard.py` runs the live Alpaca
  broker refresh inside a try/except and, unless `--require-live-broker` is set,
  logs a warning and exits 0 on failure. The systemd service
  (`deploy/caerus-dashboard-refresh.service`) does not pass that flag, so when
  the Alpaca paper credentials began returning HTTP 401 on 2026-05-20 the NAV,
  benchmark, broker-snapshot, and posttrade-recon artifacts froze for ~2 weeks
  while systemd reported `Result=success` every 5 minutes (FR-058A audit:
  4,181 consecutive `unauthorized` failures, all silent).
- **Scope:** (1) Classify live-broker failures into explicit reason codes
  (`alpaca_auth_failed` for 401/403/unauthorized, else `live_broker_refresh_failed`;
  `live_broker_required_failed` when `--require-live-broker` is set and the step
  fails). (2) Add deterministic stale-artifact telemetry comparing the latest
  date in `live_overlay_nav_series.csv`, latest `broker_snapshot_*.json`, and
  latest `recon_posttrade_*.json` against the report date with a calendar
  tolerance, emitting `nav_artifact_stale` / `broker_snapshot_stale` /
  `recon_artifact_stale`. (3) Surface a structured `live_status` +
  `live_telemetry_staleness` block in the refresh JSON output and make the
  `--require-live-broker` path exit non-zero with the reason codes instead of a
  bare traceback. (4) Enable `--require-live-broker` in the systemd service unit
  (safe: VM credentials are currently valid and NAV is fresh through 2026-06-04).
- **Non-goals:** No order execution, broker order submission, allocation,
  strategy selection, or promotion changes. No change to how artifacts are
  fetched/written beyond failure visibility. No secret values logged.
- **Planned artifacts:** No new output files. Added `live_status` and
  `live_telemetry_staleness` keys in the existing `refresh_quant_dashboard`
  stdout result; `--require-live-broker` added to
  `deploy/caerus-dashboard-refresh.service` (requires `sudo cp` +
  `systemctl daemon-reload` on the VM to install).
- **Validation plan:** Unit tests for failure classification and stale-artifact
  evaluation; a `main()`-level test that a failing live-broker step under
  `--require-live-broker` returns non-zero and emits `live_broker_required_failed`,
  while the default path returns 0 with a `failed` `live_status`. `py_compile`,
  `git diff --check`. VM: confirm the service still succeeds with current valid
  credentials and `live_overlay_nav_series.csv` tail stays current; install the
  updated unit and `daemon-reload`.
- **Risks / assumptions:** Enabling `--require-live-broker` means a genuine
  Alpaca outage marks the unit failed and skips that cycle's dashboard rebuild —
  this is the intended alertable behavior. Staleness tolerance must accommodate
  weekends/holidays (calendar-day tolerance, default 4) to avoid false positives.

## FR-060 Intended NAV True Mark-to-Market

- **FR number:** FR-060
- **Title:** Intended NAV True Mark-to-Market
- **Date started:** 2026-06-04
- **Status:** `IN_PROGRESS`
- **Problem statement:** The system writes a daily plan snapshot
  (`outputs/precompute/<date>/`), so `build_intended_nav` treats every day as a
  rebalance and rebuilds the intended portfolio from same-day target weights ×
  the prior equity, marked at the same day's prices. That reconstruction returns
  exactly the prior equity, so `intended_return_daily` is mechanically 0.0 (VM:
  `intended_equity_value` is identical 10855.03 across 2026-06-01..06-04; 0/75
  rows non-zero). Operational drag therefore compares actual return against a
  flat 0.0 intended return instead of a true counterfactual.
- **Scope:** In `research/operational_drag.py`, build intended NAV as a
  day-over-day counterfactual: on each rebalance day, first mark the carried
  (prior) intended holdings to market at the current day's prices and use that
  marked equity as the rebalance basis; carry holdings forward between
  rebalances and mark daily. This makes `intended_return_daily` reflect price
  moves on the held intended portfolio. No look-ahead (only prices ≤ date), no
  fabricated prices, missing-price reason codes preserved.
- **Non-goals:** No execution, broker, allocation, strategy-selection, or
  promotion changes. No change to plan-snapshot discovery or to actual/benchmark
  NAV. Does not start FR-061.
- **Planned artifacts:** No new files. Same `intended_nav.json` /
  `intended_nav_timeseries.csv` with corrected day-over-day returns and new
  reason codes (`intended_rebalance_marked_to_market`,
  `intended_rebalance_carry_unpriced_fallback`, `intended_nav_marked_to_market`,
  plus existing `intended_base_equity_inferred_from_plan_notional` /
  `missing_price:*`).
- **Validation plan:** Tests proving non-zero intended returns when prices move
  between days; rebalance-day behavior; missing-price degradation (labeled
  fallback, no fabrication); no look-ahead (future prices never used). Targeted
  + broader pytest, `py_compile`, `git diff --check`. VM: hydrate + run
  operational_drag for 2026-06-04, confirm all four timeseries reach 2026-06-04
  and `intended_return_daily` is no longer mechanically zero.
- **Risks / assumptions:** When carried holdings cannot be fully priced on a
  rebalance day, the basis falls back to the prior equity (same-day
  reconstruction, daily return 0 for that day) — preserved as a clearly-labeled
  fallback rather than fabricating a mark. The reported latest-row holdings are
  the current day's intended target (post-rebalance), valued at the
  marked-to-market equity.

## FR-061 Operational Drag Reporting Cleanup

- **FR number:** FR-061
- **Title:** Operational Drag Reporting Cleanup
- **Date started:** 2026-06-04
- **Status:** `DEPLOYED_OBSERVING`
- **Problem statement:** The top-level operational-drag `reason_codes` flatten
  every component/row reason into one list, so broad `missing_price:*` codes from
  historical and non-trading-day rows (and current-date residue like
  `missing_price:BK`) make a run whose requested-date intended/actual/SPY data is
  clean look broken. CIOs cannot tell whether the requested date is usable.
- **Scope:** In `research/operational_drag.py`, classify reason codes by recency
  and materiality without deleting any: `current_date_reason_codes` (from the
  requested-date rows + series-level reasons), `historical_reason_codes` (from
  rows dated before the requested date), `window_reason_codes` (stable-window
  caveats), and the materiality cross-cut `material_reason_codes` /
  `non_material_reason_codes`. Add `current_date_status`
  (`current_date_ok` / `current_date_available_with_historical_caveats` /
  `current_date_available_with_caveats` / `current_date_unavailable`),
  `decision_grade`, and `decision_grade_explanation`. Surface these in
  `operational_drag.json`, the stable-window markdown header, and the
  review-packet operational-drag section. Keep the flat `reason_codes`
  backward-compatible.
- **Non-goals:** No execution, broker, allocation, strategy, or promotion
  changes. No missing data deleted or suppressed — only classified. No change to
  drag math, NAV construction, or price discovery.
- **Planned artifacts:** No new files. New classification keys in
  `operational_drag.json` and the combined analysis payload; richer
  `stable_window_analysis.md` header; cleaned review-packet section fields.
- **Validation plan:** Tests that a clean current date with historical missing
  prices yields `current_date_ok`/`..._with_historical_caveats` and
  `decision_grade=True` while still listing the historical codes; that a
  current-date material gap yields a non-decision-grade status; that flat
  `reason_codes` stays populated. Targeted + broader pytest, `py_compile`,
  `git diff --check`. VM: run operational_drag for 2026-06-04 and confirm the
  JSON/markdown clearly separate current-date health from historical caveats.
- **Risks / assumptions:** Classification is heuristic (recency by row date,
  materiality by token match); it must never drop a code from the flat list.
  `missing_price:BK` stays visible and is bucketed by the date(s) on which it
  actually occurs.
- **Current June 9 state:** Deployed and observing. Operational-drag output now
  separates current-date health from historical caveats and reports
  `decision_grade`, `current_date_status`, current-date reason codes, historical
  reason codes, and confidence.

## FR-062 Reconciliation Drift Investigation and Patch

- **FR number:** FR-062
- **Title:** Reconciliation Drift Investigation and Patch
- **Date started:** 2026-06-06
- **Status:** `DEPLOYED_OBSERVING`
- **Problem statement:** Operational-drag artifacts now reach 2026-06-04 after
  FR-059 through FR-061, but the current-date readout remains non-decision-grade
  because `missing_broker_position` and `reconciliation_not_clean` are material
  blockers. The root cause must be proven from the full precompute, execution,
  broker, reconciliation, and operational-drag chain before any patch.
- **Scope:** Audit 2026-06-04 run-scoped and precompute artifacts; inspect
  reconciliation schema, reason-code production, alias normalization, partial
  fill/timing handling, and operational-drag source selection; then implement the
  narrowest read-only correction or diagnostic needed. Valid outcomes include an
  alias normalization fix, reconciliation parser fix, source-selection/staleness
  fix, materiality rebucketing fix, or explicit true-drift diagnostic.
- **Non-goals:** No live order submission changes, no broker API mutation, no
  allocation or strategy-selection changes, no promotion/demotion, no fabricated
  broker positions, fills, prices, NAV, or clean statuses, and no dashboard
  redesign. True reconciliation drift must remain visible and material.
- **Planned artifacts:**
  `outputs/operational_drag/2026-06-04/reconciliation_drift_diagnostic.json`
  or `outputs/reconciliation_diagnostics/2026-06-04/reconciliation_drift_diagnostic.json`,
  including selected sources, mismatches, alias resolutions, stale-artifact
  warnings, reason codes, decision-grade impact, and recommended action.
- **Validation plan:** Add targeted tests covering alias resolution (BK/BNY),
  true missing broker positions, clean reconciled artifacts, stale recon source
  selection, sells removed from posttrade expectations, partial/unfilled order
  materiality, and current-date reason-code bucketing. Run targeted
  operational-drag/reconciliation tests, broader safe operational-drag
  regression, `py_compile` on changed Python/scripts, and `git diff --check`;
  then regenerate 2026-06-04 operational-drag artifacts locally and on the VM.
- **Risks / assumptions:** Local artifacts may be incomplete or already include
  unrelated WIP. BK/BNY aliasing is only a hypothesis and must not be assumed.
  A clean-looking status from stale artifacts is unsafe; the selected run, source
  timestamps, and stale warnings must be explicit. If true drift is confirmed,
  the correct patch is diagnostic clarity and an explicit remediation path, not
  suppression.
- **Current June 9 state:** Deployed and observing. The current-date blockers
  from stale/misread reconciliation evidence are repaired for 2026-06-09:
  split account/position artifacts and `normalized_positions` dictionaries are
  parsed, current run-scoped reconciliation is selected when available, and
  gross exposure can be derived from cash/equity when direct exposure is absent.

## Investment-Confidence Numbering Note

The next research wave was requested as FR-058 strategy differentiation, FR-059
multi-asset research, and FR-060 dashboard consolidation. Those FR numbers are
already assigned above to operational-drag and broker-telemetry work. This
backlog therefore preserves the existing operational lineage and assigns the new
investment-confidence work to the next open IDs: FR-063, FR-064, and FR-065.

## FR-050 Phase B Phoenix Historical Behavior Review

- **FR number:** FR-050 Phase B
- **Title:** Phoenix Historical Behavior Review
- **Date started:** 2026-06-08
- **Status:** `NOT_VIABLE_CURRENT_PHASE_B`
- **Problem statement:** Phoenix has an implemented research generator and an
  evidence tracker, but Caerus still needs a broader historical behavior review
  before Phoenix can be treated as a differentiated crisis-reversal candidate.
- **Scope:** Build a research-only review of active/inactive days, activation
  reasons, candidate count distribution, top candidates, overlap versus
  Polaris/Orion/Lyra, regime summaries, drawdown/recovery context when
  available, confidence, and conservative decision-grade status.
- **Current hold state:** Phoenix is differentiated and risk-shaped, but
  decision-grade PIT liquidity/capacity evidence now fails. Nasdaq Data Link
  `QELx06` is cleared; the 2026-06-18 OHLCV rebuild hydrated 1,600 PIT
  large-cap tickers / 7,845,012 rows with no empty or failed tickers. Phase C
  measured 80/80 Phoenix candidate rows and classified `NOT_VIABLE` because
  the weakest selected name cannot support the `$1M` reference portfolio at 5%
  ADV (`capacity_below_5pct_adv_policy`; minimum 5% ADV capacity about
  `$74.6k`). Phoenix is not eligible for Shadow readiness review without a new
  research candidate or explicit owner-approved capacity policy change.
- **Non-goals:** No Phoenix threshold tuning, no shadow promotion, no paper/live
  behavior changes, no broker calls, no cron changes, and no capital routing.
- **Planned artifacts:** `outputs/model_quality/<date>/phoenix_phase_b_review.json`
  and `.md`.
- **Validation plan:** Fixture tests for populated history, missing history,
  active/inactive mixes, missing regime/price data, deterministic ordering, and
  sparse evidence blocking decision-grade status.

## FR-053 Phase B Argo Regime Selection Validation

- **FR number:** FR-053 Phase B
- **Title:** Argo Regime Selection Validation
- **Date started:** 2026-06-08
- **Status:** `ACTIVE_RESEARCH`
- **Problem statement:** Argo has been re-homed as the regime overlay /
  model-selection layer, but it needs explicit validation that its artifacts are
  stable, point-in-time, and research-only.
- **Scope:** Validate current regime, current recommendation, recommendation
  confidence, transition and stability diagnostics, input freshness,
  no-lookahead checks, and evidence blockers. The validation must distinguish a
  leaderboard winner from a decision-grade recommendation.
- **Non-goals:** No execution, broker submission, cron timing, production
  strategy selection, allocation change, or promotion recommendation.
- **Planned artifacts:** `outputs/model_quality/<date>/argo_phase_b_validation.json`
  and `.md`.
- **Validation plan:** Fixture tests for stable regimes, transitions, stale data,
  missing model-selection artifacts, leaderboard winner without decision-grade
  recommendation, and deterministic output.
- **Current evidence state (2026-06-08):** `argo_regime_selection.*` is now
  expected to emit every run, including degraded `PARTIAL`/`BLOCKED` artifacts
  with explicit reason codes. The 2026-06-08 artifact is `PARTIAL` with Lyra as
  leaderboard winner only, no decision-grade recommendation, stale shadow
  performance evidence dated 2026-04-30, stale promotion governance/readiness
  evidence dated 2026-06-02, and remaining promotion governance blockers. The
  dashboard decision-grade section remains `BLOCKED` until those evidence
  blockers clear.
- **Phase A evidence-consumer framework (2026-06-17):**
  `docs/governance/fr_active/fr_069_argo_phase_a_evidence_framework.md` and
  `outputs/research/argo/argo_phase_a_evidence_framework_2026-06-17.json`
  classify sleeve evidence for research governance only. Argo remains an
  observer, not a capital allocator or promotion engine.
- **Phase B research-priority framework (2026-06-17):**
  `docs/governance/fr_active/fr_069_argo_phase_b_research_priority_framework.md`
  and `outputs/research/argo/argo_phase_b_research_priority_2026-06-17.json`
  rank the next unit of research effort for advisory governance only. Current
  forced ranking: Phoenix, Cassiopeia, Orion, Argo, Cygnus, Polaris, Lyra.
  The ranking is a research queue, not an allocation, promotion, retirement, or
  production decision rule.

## FR-063 Strategy Differentiation Deep Dive

- **FR number:** FR-063
- **Title:** Strategy Differentiation Deep Dive
- **Date started:** 2026-06-08
- **Status:** `ACTIVE_RESEARCH`
- **Problem statement:** Caerus needs a cross-strategy view of whether Polaris,
  Orion, Lyra, Phoenix, and registered research strategies are materially
  distinct or redundant, especially Lyra versus Orion.
- **Priority state:** Active supporting evidence under FR-069. Preliminary
  historical evidence suggests Orion and Lyra are highly correlated and likely
  redundant, but final retain/retire conclusions require sufficient canonical
  new-series history under `dated_same_day_close_to_close_v1`.
- **Working hypothesis:** Only one of Orion/Lyra is likely needed long-term.
  Lyra may be the leading candidate for retention if evidence confirms
  outperformance, but no final decision is approved and the Lyra name must not
  be redeployed yet.
- **Scope:** Produce pairwise holdings overlap, active share, sector difference,
  turnover difference, concentration difference, return correlation when
  available, attribution spread, regime-specific behavior, redundancy
  classification, and a conservative retirement watchlist.
- **Non-goals:** No actual strategy retirement, strategy promotion, allocation
  change, Lyra-name reuse, broker behavior, cron change, or production order
  change.
- **Decision path:** Future Orion/Lyra disposition should be made through the
  FR-069 sleeve architecture and promotion/retirement framework, not as an
  isolated FR-063 decision.
- **Research-only study spec:** `docs/governance/fr_active/fr_063_orion_lyra_redundancy_study.md`
  defines evidence windows, metrics, thresholds, artifacts, and governance
  outputs. It includes Polaris as the baseline and forbids retirement,
  promotion, allocation, or naming decisions.
- **Planned artifacts:** `outputs/model_quality/<date>/strategy_differentiation_deep_dive.json`
  and `.md`.
- **Validation plan:** Fixture tests for near-duplicate Lyra/Orion, distinct
  Phoenix, missing history, identical strategies, no overlap, deterministic pair
  ordering, and sparse evidence blocking decision-grade retirement.

## FR-064 Multi-Asset Research Framework

- **FR number:** FR-064
- **Title:** Multi-Asset Research Framework
- **Date started:** 2026-06-08
- **Status:** `DRAFT_RESEARCH`
- **Problem statement:** Caerus is currently equity-centric. Before any
  non-equity sleeve is researched in detail, the required data, candidate
  sleeves, measurements, promotion preconditions, and explicit non-goals must be
  documented and audited.
- **Scope:** Evaluate design readiness for Treasury duration, cash/T-bill,
  gold, broad commodities, managed-futures proxy, defensive equity ETF proxy,
  and options overlay as deferred design-only.
- **Non-goals:** No trading implementation, no allocation engine, no production
  order generation, no broker calls, no options execution integration, and no
  promotion recommendation.
- **Planned artifacts:** `docs/governance/fr_archive/fr_064_multi_asset_research_framework.md`
  plus `outputs/model_quality/<date>/multi_asset_research_framework.json` and
  `.md`.
- **Validation plan:** Fixture tests for candidate sleeves, missing-data
  degradation, options deferred status, no execution integration, and
  deterministic output.

## FR-065 Dashboard Decision-Grade Consolidation

- **FR number:** FR-065
- **Title:** Dashboard Decision-Grade Consolidation
- **Date started:** 2026-06-08
- **Status:** `ACTIVE_RESEARCH`
- **Problem statement:** The terminal dashboard is broker-authoritative and
  aligned with its shell contract, but it lacks a compact view of model-quality
  decision-grade evidence, blockers, freshness, and research confidence.
- **Scope:** Extend the dashboard data model with a `decision_grade` section and
  render a compact terminal panel sourced from model-quality artifacts. Missing
  artifacts must degrade visibly to `PARTIAL`.
- **Non-goals:** No dashboard redesign, no replacement of broker state with
  planned trades, no suppression of warnings, no execution or broker behavior
  changes, and no promotion routing.
- **Planned artifacts:** Dashboard data JSON section only; no generated outputs
  are committed.
- **Validation plan:** Tests for decision-grade data model presence, visible
  partial degradation when model-quality artifacts are missing, HTML mount, JS
  renderer, and existing dashboard test continuity.

## FR-066 Canonical NAV Track Record Integrity

- **FR number:** FR-066
- **Title:** Canonical NAV Track Record Integrity (daily build, inception
  backfill, SPY/beta-adjusted scoreboard, fail-loud freshness)
- **Date started:** 2026-06-10
- **Status:** `DEPLOYED_OBSERVING` (VM backfill/write completed; cron installed)
- **Governance label:** OPERATIONAL_TELEMETRY / NON_EXECUTIONAL
- **Problem statement:** `outputs/portfolio_history/nav.csv` covers only
  2026-03-03..2026-04-08 (26 rows); the builder was never scheduled; the
  broker-authoritative dashboard view persists no durable local record; the
  2026-05-20→2026-06-04 Alpaca 401 freeze proved silence is not a safe state; no
  SPY-relative or beta-adjusted record exists anywhere.
- **Scope (this session):**
  - `scripts/backfill_portfolio_history.py` (new): one-time inception backfill,
    dry-run default, Alpaca portfolio-history pull, 1 bp reconciliation vs
    nav.csv + broker snapshots, single-day return >5% flags, manifest, one-time
    re-run guard, restatement logging + backup on write.
  - `scripts/build_portfolio_history.py` (extended): additive benchmark/beta
    columns (`spy_close`, `spy_return_1d`, `benchmark_nav`, `excess_return_1d`,
    `rolling_beta_60d`, `beta_adjusted_excess_1d`), append-only merge guard,
    checksum manifest, derived scoreboard (IR windows, drawdowns, headline =
    beta-adjusted-excess IR vs SPY).
  - `core/portfolio_history_escalation.py` (new): NAV_GAP detection, two-failure
    `[CAERUS NAV BROKEN]` escalation, FR-059 reason-code integration, email via
    the Shadow CIO path.
  - `research_registry/research/portfolio_history_freshness.py` (extended):
    checksum verification + trading-day NAV gap scan.
  - `scripts/crontab.txt`: post-close build+escalation line (7:15 PM ET);
    installed on the VM 2026-06-10 after owner approval.
- **Non-goals:** No execution, broker submission, order routing, allocation,
  strategy selection, promotion, or cron execution-phase change. Existing nav rows
  are never deleted.
- **Validation evidence (commands run):**
  - `py_compile` on all four touched modules + new tests: PASS.
  - `pytest Tests/test_portfolio_history_builder.py test_portfolio_history_backfill.py test_portfolio_history_escalation.py test_portfolio_history_freshness.py`:
    **27 passed**.
  - `python3 scripts/validate_cron_commands.py scripts/crontab.txt`: **24 checks
    PASS**.
  - `git diff --check`: clean.
  - VM backfill dry-run/write using VM `.env`: rows reconstructed from
    2026-03-03 with no trading-day gaps; no credentials printed.
  - VM builder after write: 70 NAV rows through 2026-06-10 with SPY/beta columns
    populated per rolling-window availability.
  - VM `scripts/operational_validation.py`: PASS.
- **Deployment caveat:** `nav.csv` overlap is clean, but broker snapshot
  reconciliation is non-clean because snapshots are point-in-time account captures
  rather than the same EOD portfolio-history source. The Apr 8 canonical Alpaca
  portfolio-history row is `$9,751.97`; the older `$9,715.45` baseline is retained
  only as a historical discrepancy, not source truth.
- **Acceptance criteria status:** criteria 1/3/4 are implemented/deployed; criterion
  2 remains observing for 10 fresh trading days; criterion 5 is active via the
  installed escalation cron.
- **Rollback:** Revert the four module changes + the crontab line; delete
  `backfill_manifest.json`, `checksum_manifest.json`, and the added NAV columns.

## FR-051 Cygnus Implementation Wave 1 (v0 Event-Reaction)

- **FR number:** FR-051 (Wave 1 per the 2026-06-10 addendum)
- **Title:** Cygnus post-earnings drift — Wave 1 v0 validation
- **Date started:** 2026-06-10
- **Status:** `SHELVED` (v0 Stage 2 validation FAIL; holdout preserved)
- **Governance label:** RESEARCH_ONLY / NON_EXECUTIONAL
- **Problem statement:** Cygnus needs a point-in-time earnings-event tape with
  auditable availability dates before any backtest. Addendum A2 fixes EDGAR as the
  sole Wave-1 source (8-K Item 2.02, keyed by `acceptanceDateTime`).
- **Scope (Stage 1, this session):** `research/cygnus/{__init__,events,artifacts}.py`
  + `scripts/research/run_cygnus_research.py` build the EDGAR 8-K Item 2.02 event
  tape with A2 ET availability rules (acceptanceDateTime parsed UTC→ET; <09:00
  same-day, else next trading date; Friday/holiday → next trading date) and emit
  the acceptance-timestamp audit. `filings.files` pagination was added after the
  sample revealed `filings.recent` truncates active filers (JPM 4→42 events).
- **Non-goals:** No shadow integration; no registry, execution, broker, cron,
  allocation, or paper/live change. Consensus/revision-dependent fields (v1/v2)
  remain vendor-gated and deferred.
- **Validation evidence (commands run):**
  - `py_compile` on the cygnus modules + runner + tests: PASS.
  - `pytest Tests/test_cygnus_events.py`: **10 passed**.
  - Live bounded sample (AAPL, MSFT, JPM, KO, NVDA, WMT; 2016–2026): 255 events,
    `look_ahead_safe=True`, 0 look-ahead violations, 0 missing timestamps,
    distribution 127 after-close / 126 before-open / 2 during-market (correctly
    delayed). Artifacts under `outputs/research/cygnus/2026-06-10/`.
- **Stage 2 v0 verdict:** FAIL, 4/6. Rank IC 10D: IC 0.0318, t-stat 1.59, FAIL
  due to t-stat below 2. IC 20D/60D decay: PASS. Net IR vs SPY at 25 bps: 0.44,
  PASS. Excess correlation vs Polaris proxy: 0.043, PASS. Event coverage: 1.05,
  PASS. Cost sensitivity at 50 bps: IR -0.32, FAIL.
- **Governance decision:** The tune window also failed. Cygnus v0 is shelved and
  must not be re-tuned. The 2025-forward holdout remains untouched and preserved.
  Cygnus v1 requires EPS-surprise / consensus data. Future diagnostics may compute
  Newey-West or date-clustered t-statistics, but that diagnostic does not change
  the v0 verdict.
- **Rollback:** Delete `research/cygnus/`, the runner, the test, and dated
  `outputs/research/cygnus/` artifacts.

## FR-052 Cassiopeia Event-Driven Spec

- **FR number:** FR-052
- **Title:** Cassiopeia event-driven spec
- **Date started:** 2026-06-10
- **Phase:** Research / Event-Driven Strategy Spec
- **Status:** `BACKLOG`
- **Blast Radius:** LOW
- **Dependencies:** `docs/governance/fr_archive/fr_052_cassiopeia_research_spec.md`
- **Observation Status:** not_started
- **Current State:** Canonical event-driven strategy spec remains spec-only and active backlog maintenance is tracked here until an explicit implementation decision is made.
- **Success Criteria:** The spec remains the single canonical Cassiopeia definition and stays synchronized with the roadmap until an owner decision promotes or retires it.
- **Rollback Reference:** Revert documentation links only.

## FR-067 Vela Stage 0 — PIT Universe Source Comparison

- **FR number:** FR-067 (Stage 0)
- **Title:** Vela small-cap momentum — Stage 0 PIT universe source comparison
- **Date started:** 2026-06-10
- **Status:** `CLOSED_PASS` (Sharadar coverage gate verified 2026-06-10; FR-068
  Phase 1 supersedes for the PIT build)
- **Governance label:** RESEARCH_ONLY / NON_EXECUTIONAL
- **Problem statement:** FR-067 is blocked until a PIT small-cap universe with
  delisted-ticker price coverage exists; hand-curating current names is forbidden.
- **PASS result (2026-06-10):** Sharadar paid entitlement ran the verifier at
  `--sample-size 100`: complete_count=100, complete_pct=1.0,
  median_coverage_pct=0.999. Verifier scoring bug fixed first (`e4b6201`).
  Sharadar approved as the PIT price/security-history source for FR-068 Phase 1.
- **Scope (this session):** `docs/governance/fr_archive/fr_067_stage0_source_comparison.md`
  compares Norgate, Sharadar (Nasdaq Data Link), reconstructed S&P 600, and
  CRSP/WRDS across cost, license, delisted-ticker price coverage, integration
  effort, and PIT membership feasibility. Conditional recommendation: Sharadar
  after successful coverage verification (cross-platform integration;
  survivorship-free delisted prices; PIT market-cap band as the small-cap
  definition).
- **Non-goals:** No `research/vela/` strategy code, no registry entry, no Vela
  strategy-name assignment — all owner decisions per roadmap Section 6.
- **Validation evidence:** Docs-only; `git diff --check` clean.
- **Caveats carried into FR-068:** (1) Sharadar has no S&P 600 / Russell index
  membership — small-cap membership uses a PIT market-cap band or a supplemental
  source; (2) Sharadar carries no analyst consensus, so Cygnus v1
  EPS-surprise-vs-consensus remains separately blocked.
- **Next:** FR-068 Phase 1 (PIT universe foundation) — security-existence
  universe from Sharadar TICKERS; strategy migration deferred.
- **Rollback:** Revert governance edits; comparison doc + verifier remain as evidence.

## FR-068 PIT Universe + Polaris/Orion/Lyra Rebaseline

- **FR number:** FR-068
- **Title:** Point-in-Time universe foundation + survivorship rebaseline
- **Date started:** 2026-06-10
- **Status:** `PHASES_1_4_COMPLETE` (Polaris rebaseline done — MATERIAL; Orion/Lyra matched PIT artifact generated; canonical `caerus_large_cap` resolver certified 2026-06-22)
- **Governance label:** RESEARCH_ONLY / NON_EXECUTIONAL
- **Problem statement:** Every official historical backtest was built from
  `data/universe.csv` (200 current survivors) — confirmed SEVERELY survivorship-
  biased (71.7% of the investable common-stock universe is delisted and invisible).
- **Phases delivered:**
  - **Phase 1** — PIT universe foundation from Sharadar TICKERS (20,618 securities,
    14,790 delisted); `research/pit_universe.py` `Universe(as_of_date)` reader
    (security-existence family; no `data/universe.csv` fallback).
  - **Phase 2** — PIT impact assessment: classification **SEVERE**; 8.5% early
    look-ahead in the curated 200; 71.7% market delisted.
  - **Phase 2.5** — `caerus_large_cap` membership family (1,600; 354 delisted) via
    `research/pit_large_cap_family.py`; full Sharadar SEP price hydration
    (1,600/1,600, incl. delisted) via `scripts/research/hydrate_sharadar_sep.py`.
  - **Phase 3 (priced)** — Legacy vs PIT Polaris on the committed momentum baseline
    (`alpha_lab_v1` signals + `alpha_lab_v2` `baseline_top10_daily`), changing only
    the universe; both legs SEP-priced; window 2014-01-02..2024-12-31 (holdout
    excluded). Result **MATERIAL**: Sharpe 1.054→0.851 (−19%), MaxDD −43%→−54%,
    CAGR 28.83%→30.68%. Attribution dominated by curated-out high-momentum
    large-caps (ENPH/PLUG/GME/NVAX/...), not delisted-loser drag.
  - **Phase 4** — Orion/Lyra matched PIT artifact generated with 2,767 matched
    pre-holdout observations and no statistically meaningful Lyra lead.
  - **Phase 4.1 resolver certification** — canonical
    `Universe(as_of_date, "caerus_large_cap")` now resolves the separate
    `membership_universe_large_cap.csv` artifact and passes the 2014-01-02,
    2020-01-02, and 2026-01-02 count checks documented in
    `reports/pit_universe_certification.md`.
- **Governance outcome:** legacy current-universe backtests are **non-decision-grade**;
  promotion evidence must carry `universe_method = pit_universe`. Legacy retained as
  `legacy_current_universe` (lineage).
- **Non-goals:** No production Polaris/execution/model/ranking/sizing/risk/cost/cron/
  registry change; no holdout access; no tuning.
- **Validation evidence:** py_compile; targeted PIT tests (39+ across PIT suites);
  json.tool on artifacts; git diff --check clean. Real priced run, committed harness.
- **Next:** Replace current-scale `scalemarketcap` with PIT-valid,
  survivorship-free, security-id keyed, date-effective large-cap membership.
  DAILY market cap, PIT index membership, scheduled reconstitution membership,
  decision-time artifacts, or PIT shares x close can qualify only if lineage and
  coverage certify the declared universe policy. Canonical security_id replay
  panel, decision tapes, replay certification, allocator baseline, and
  exposure-matched research framework continue under the FR-069 child lane.
- **Rollback:** Revert rebaseline research modules + artifacts; PIT data is
  gitignored/regenerable.

## FR-069 Research Lab / Modular Sleeve Architecture

- **FR number:** FR-069
- **Title:** Research Lab / modular sleeve architecture (design only)
- **Date started:** 2026-06-10
- **Status:** `PHASE_B_IMPLEMENTED_RESEARCH_ONLY` (machine-readable scaffold; no production refactor)
- **Governance label:** RESEARCH_ONLY / NON_EXECUTIONAL
- **Priority state:** Next major architecture/research workstream after FR-070
  observation; Phase B
  research-only scaffolding implemented.
- **Scope:** `docs/governance/fr_active/fr_069_research_lab_modular_sleeve_architecture.md` —
  a pluggable Sleeve contract on the PIT foundation; shared data/signal/backtest/
  evaluation layers; membership families; governance gates (PIT-required evidence,
  holdout protection, pre-registration). The Caerus Investment Doctrine is the
  architectural north star and target-state constraint. Target state is a
  portfolio-of-sleeves platform with each sleeve allocatable from 0% to 100%.
  FR-069 should define how sleeves are onboarded, evaluated, promoted,
  allocated, and retired. Orion/Lyra rationalization belongs inside this
  architecture rather than as an isolated immediate retirement decision. Maps
  existing strategies to the contract and sequences migration into future FRs.
- **Phase A package:** `docs/governance/fr_active/fr_069_phase_a_architecture_package.md`
  documents the Canonical Sleeve Protocol, Registry-Onboarding Architecture,
  Research Lab Operating Model, Future Sleeve Inventory, and recommended Phase B
  implementation roadmap.
- **Phase B scaffold:** `docs/governance/fr_active/fr_069_phase_b_scaffolding.md`
  documents the machine-readable scaffold, with manifest
  `research_registry/sleeves/manifest.json`, validator
  `scripts/research/validate_sleeve_manifest.py`, read-only MCP tool
  `fr069_sleeve_inventory`, Polaris parity plan, Orion/Lyra PIT evidence plan,
  and future sleeve onboarding placeholders.
- **Phase B2 evidence-envelope validator:** `research_registry/sleeves/evidence.py`
  and `scripts/research/validate_sleeve_evidence.py` validate static sleeve
  evidence metadata for required fields, manifest membership, PIT/holdout
  decision-grade markers, and explicit non-executional impact. This is
  research/test-only and does not generate backtests, broker artifacts,
  allocations, or production registry changes.
- **Phase C readiness packet:** `docs/governance/fr_active/fr_069_phase_c_readiness.md`
  defines the future lifecycle gate matrix, sleeve gap matrix, minimum evidence
  envelope, Orion/Lyra redundancy rules, Phoenix/Cygnus/Cassiopeia/Argo
  onboarding criteria, and validation acceptance criteria. It is readiness only
  and does not activate sleeves or change runtime behavior.
- **Child lane — canonical PIT replay infrastructure:** Research-only lane opened
  2026-06-22 under FR-069, with FR-068 as data dependency. Scope: security_id
  replay price panel, canonical decision tapes, replay certification, canonical
  allocator baseline, exposure-matched attribution, and future research hardening.
  This lane must not reuse FR-074 and must not change execution, broker, scheduler,
  paper/live behavior, allocation, promotion, or production registry behavior.
- **Phase C gate:** Do not begin Phase C until the manifest, validator, MCP
  inventory, readiness packet, and targeted tests pass. Phase C implementation
  still requires separate approval for any behavior beyond research-only
  scaffolding.
- **Non-goals:** No production refactor, no execution/registry runtime behavior
  change, no broker/allocation/portfolio-construction/model/strategy/cron
  change, no live-capital behavior, and no Orion/Lyra/FR-063 retirement.
- **Rollback:** Delete the Phase B scaffold docs/manifest/validator/tests/MCP
  inventory registration. No runtime behavior is wired.

## FR-070 Cash Gating and Post-Sell Buy Budget Reconciliation

- **FR number:** FR-070
- **Title:** Cash Gating and Post-Sell Buy Budget Reconciliation
- **Date started:** 2026-06-11
- **Phase:** Execution Integrity / Cash Deployment
- **Status:** `DEPLOYED_OBSERVING`
- **Blast Radius:** HIGH
- **Priority state:** Observation/monitoring; no longer the primary active implementation lane.
- **Observation Status:** observation_monitoring
- **Current State:** June 12 remediation is deployed. The cash discrepancy was classified as `ARTIFACT_TIMING_FAILURE`, not failed sell-first rebudgeting. Monitoring now runs through MCP target-attainment diagnostics and the next live-run gates for post-buy snapshots, pending-buy terminal state, target cash attainment, and classified warnings.
- **Reopen criteria:** Reopen implementation only for a stale/pre-buy posttrade snapshot, buy timeout/failure, unclassified cash drift, reconciliation/target-attainment contradiction, or achieved cash materially outside tolerance without a classified reason.
- **Implementation Window:** No new implementation is active. Any future remediation remains weekend-maintenance only if classified evidence requires runtime changes.
- **Non-goals:** No weakening of broker cash, buying-power, risk-cash, min-notional, fractional-share, or reconciliation safeguards. No strategy, allocation, cron, or broker-submission behavior change is approved by this documentation entry.
- **Rollback Reference:** Revert the post-buy timing patch and target-attainment diagnostic only if the specific execution invariant regresses; preserve broker cash, buying-power, risk-cash, min-notional, fractional-share, and reconciliation safeguards.

## Roadmap Boundaries

Do not use Phase 4 as a vehicle for microservices, Kubernetes, Airflow, broad
scheduler rewrites, strategy promotion, broker changes, or cron timing changes.
The current bottleneck is operational clarity, not distributed compute scale.

## FR-071 Governance Doctrine Integration

- **FR number:** FR-071
- **Title:** Governance Doctrine Integration
- **Date started:** 2026-06-11
- **Phase:** Governance Documentation
- **Status:** `READY`
- **Blast Radius:** NONE
- **Dependencies:** `docs/governance/caerus_investment_doctrine.md`
- **Observation Status:** not_started
- **Current State:** Documentation-only task to ensure doctrine is referenced from README, roadmap, registry, AGENTS.md, and future FR workflow.
- **Success Criteria:** Future FRs, strategy specs, promotion reviews, and portfolio-construction work explicitly defer to the doctrine unless amended.
- **Rollback Reference:** Revert documentation links only.

## FR-085 Shadow Scorecard Publication Integrity Gates

- **FR number:** FR-085
- **Title:** Shadow Scorecard Publication Integrity Gates
- **Date started:** 2026-06-24
- **Phase:** Reporting Integrity / Governance
- **Status:** `DEPLOYED_OBSERVING`
- **Blast Radius:** LOW (research-reporting only)
- **Dependencies:** `scripts/send_shadow_cio_report.py`, `outputs/shadow_candidates/performance/shadow_nav_series.csv`, `outputs/shadow_candidates/latest/shadow_evaluation.json`, `outputs/price_hydration/<date>/status.json`; provenance context FR-024 / FR-066; upstream dependency = P2 NAV refresh-freeze remediation (follow-up).
- **Problem statement:** The 2026-06-24 Model Scorecard ranked `Orion_Alpha` #1 at +108.92% YTD and named it Leader while the same email's promotion section flagged it `NOT_READY — only 0 valid days`, and Data Health reported `shadow refresh blocked: FAILED`, `nav_series_latest_date=2026-06-05`, `max_cache_date=2026-06-23`. `Orion_Alpha` and `Polaris_Alpha` are concentration shadow variants activated 2026-06-23 (per `CURRENT_RESEARCH_ROADMAP.md`) and therefore have zero valid track-record days; their headline YTD came from an unvalidated `cumulative_return` fallback inheriting the parent Orion/Polaris curve. Two defects: (1) no freshness/abort gate — the leaderboard, rankings, promotion signals, and CIO takeaway published despite a failed/stale refresh; (2) ranking ignored `valid_day_count` and accepted the `cumulative_return` fallback, so a 0-valid-day sleeve could be crowned leader even on fresh data.
- **Scope:** `scripts/send_shadow_cio_report.py` only. (a) Freshness gate: withhold all rankings / leader / runner-up / laggard / promotion-signal / CIO-takeaway output when shadow refresh status is not OK, when latest valid NAV lags the report date beyond `MAX_NAV_AGE_TRADING_DAYS` (default 2, env `SHADOW_CIO_MAX_NAV_AGE_TRADING_DAYS`), or when NAV integrity is `CORRUPT`. (b) Internal-consistency gate: a sleeve enters ranking/leader selection only if `period_return_source == "nav"` and `valid_day_count >= MIN_VALID_DAYS` (10). (c) New `period_return_source` provenance field on `ModelSnapshot`. (d) On withhold, emit a `MODEL SCORECARD: PUBLICATION WITHHELD` block with reason (`FAILED_REFRESH` / `NAV_STALE` / `ARTIFACT_CORRUPT`), latest valid NAV date, and requested date, while preserving the DATA HEALTH diagnostics section. Tests added/extended in `Tests/test_shadow_cio_report.py`.
- **Non-goals:** No trading, allocation, execution, broker, cron, or production price-loading behavior change. Does not modify the upstream shadow refresh job or `_validate_nav_append_continuity`. Does not resolve the NAV freeze itself (tracked as the P2 follow-up).
- **Planned artifacts:** No new output files. New env var `SHADOW_CIO_MAX_NAV_AGE_TRADING_DAYS` (default `2`); new `MODEL SCORECARD: PUBLICATION WITHHELD` email block.
- **Validation evidence:** PR #117 focused scorecard tests passed; PR #117 full-suite failures matched clean `origin/main` baseline before merge. After HOTFIX-2026-06-24 recovery, deployed VM dry-run un-withheld from fresh 2026-06-23 data with Orion as leader and alpha sleeves excluded/`NOT_READY`.
- **Risks / assumptions:** If the upstream NAV refresh fails or the NAV series becomes stale/corrupt again, the scorecard will withhold publication; this is the intended fail-safe state. The internal-consistency gate reuses the existing `valid_days < 10` NOT_READY threshold; if that threshold changes, both code paths should move together.
- **Follow-up (P2 — NAV refresh freeze):** Separate remediation to determine why `trade_date_has_data` returned false for 2026-06-23 (refresh `RuntimeError`, `allow_download=False`) and to backfill the 2026-06-06 → 2026-06-23 gap through the sanctioned restatement workflow (`_append_nav_series` blocks restatement; `_validate_nav_append_continuity` must pass). Now tracked as **HOTFIX-2026-06-24** (registry Hotfix Records; recovery = NAV refresh fix + restatement, PR #118). Do not weaken `trade_date_has_data`, `allow_download`, or continuity validation as a shortcut.
- **Implementing branch / PR:** `fix/scorecard-publication-gates`, merged PR #117, commit `2ee5cd5`.
- **Rollback Reference:** Revert PR #117 / commit `2ee5cd5`; the scorecard reverts to prior publish-on-stale behavior. No execution, broker, allocation, cron, or price-loading behavior changes either way.
