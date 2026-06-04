# FR Active Backlog

## Purpose

This document is the operator-readable roadmap for active Caerus Friday
Refactor (FR) work. It contains only work that is not fully closed:

- `BACKLOG`
- `READY`
- `READY_VALIDATED`
- `IN_PROGRESS`
- `PROMOTION_READY`
- `DEPLOYED_OBSERVING`

Fully deployed history and reviewed deferred items belong in
`docs/governance/fr_registry.md`. Governance methodology belongs in
`docs/governance/fr_governance_model.md`.

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
| FR-028 shadow execution timing semantics correction candidate | Accounting Correctness / Promotion Analytics | `DEPLOYED_OBSERVING` | HIGH | FR-024, FR-025, FR-026, FR-027, FR-030 | observing | Phase C adds research-only promotion readiness sidecars: `longitudinal_metrics.json`, `stability_surface.json`, `promotion_readiness.json`, and `promotion_readiness.md`; no historical migration, execution, broker, cron, strategy selection, or capital-allocation change. | Revert FR-028 Phase C commit; ignore new dated promotion-readiness sidecars and continue reading existing `shadow_evaluation.json` / `comparison.json`. |
| FR-029 promotion governance hardening for provenance, exposure, and timing confidence | Promotion Governance | `IN_PROGRESS` | MEDIUM | FR-028, FR-037, FR-038 | tracked_via_children | Partially implemented through FR-037 (Tier 3 promotion governance surfaces) and FR-038 (governance blocker audit + diagnostic surfaces). Both are `DEPLOYED_OBSERVING`. FR-029 itself transitions to `DEPLOYED` only after both children satisfy their observation criteria. Historical intent (consume provenance, exposure, and timing confidence in promotion gates) is preserved and now realized via deterministic six-gate evaluation, regime attribution, research-only allocation evaluation, blocker classification, and deterministic maturity scoring. | Revert promotion-readiness checks to existing scorecard criteria; see FR-037 and FR-038 rollback references for code-level reversal. |
| FR-031 execution integrity contract | Execution Integrity | `DEPLOYED_OBSERVING` | HIGH | HOTFIX-2026-05-27, FR-021, broker/order evidence | observing | Additive validator deployed; writes `audit/execution_integrity.json` and compact operator-summary integrity fields without changing order routing. | Revert FR-031 implementation commits; ignore existing audit artifacts. |
| FR-032 execution lifecycle observability hardening | Execution Observability | `READY` | LOW | FR-031, 2026-05-28 recovery evidence | ready_for_low_risk_implementation | Backfill or generate lifecycle timeline artifacts from existing run artifacts when missing; improve latest-run/operator timeline usability without invoking execution. | Revert helper commit; delete only helper-generated timeline artifacts if they were created for validation. |
| FR-033 dashboard/operator asset alignment | Operator Surfaces | `BACKLOG` | LOW | FR-031, dashboard architecture review | not_started | Resolve stale dashboard execution-integrity asset tests or align them to the current dashboard architecture without redesigning dashboard UI. | Revert test/asset alignment commit; preserve dashboard publishing behavior. |
| FR-034 post-submit cash drift reconciliation review | Execution Accounting Review | `READY` | LOW | FR-031, latest paper execution artifacts | ready_for_audit | Determine whether `cash_target_drift` clears after fills/reconciliation, represents expected pending-fill drift, or indicates accounting/reconciliation mismatch. | Docs-only/audit rollback; no runtime behavior should change. |
| FR-035 execution contract documentation hardening | Execution Documentation | `PROMOTION_READY` | LOW | 2026-05-28 recovery, FR-031 | local_validation_pending | Canonicalize execution source, price basis, freshness scope, fail-closed boundaries, and operator provenance semantics. | Revert docs commit; runtime behavior unchanged. |
| FR-036 MCP Phase 7 — research-question capability router | Research MCP | `DEPLOYED_OBSERVING` | LOW | FR-015 / FR-017 / FR-018 / FR-024–FR-030 telemetry, registry semantics | observing | Read-only operator MCP shipped 2026-05-29: 20 tools, 9-capability registry, deterministic regex classifier + artifact pre-check + tool dispatch (OK / NEEDS_DATA / NEEDS_CAPABILITY / UNSUPPORTED_INTENT), operator gateway at `scripts/research_mcp_ask.py`. No transport beyond local stdio, no LLM, no execution-path coupling. | Revert MCP Phase 7 commits; delete `research_registry/research/`, `scripts/research_mcp_ask.{py,sh}`, and `outputs/research_mcp/`. Server reverts to 16-tool Phase 6 surface. |
| FR-036a MCP conformance audit vs frozen semantics layer | Research MCP | `BACKLOG` | LOW | FR-036 deployed, SEM-001..008 frozen | not_started | Produce a clause-by-clause audit doc mapping each frozen SEM contract to the MCP module that implements it (or to the gap). Pure documentation work; no implementation changes. | Delete audit doc only; implementation untouched. |
| FR-036b MCP `attribution_analysis` capability promotion | Research MCP | `BACKLOG` | LOW | FR-036 deployed, `outputs/attribution/` artifacts, existing `AttributionArtifactAdapter` ingestion family | not_started | Add a research_registry/research/attribution.py loader + `attribution_analysis` MCP tool; flip the capability from stub to implemented. | Revert attribution loader + tool commits; capability returns to NEEDS_CAPABILITY. |
| FR-036c MCP `stable_window_evaluation` capability promotion | Research MCP | `BACKLOG` | LOW | FR-036 deployed, `outputs/research/stable_window_evaluation/` artifacts | not_started | Add a loader + tool that summarises the rolling-window Sharpe / drawdown distribution from existing alpha-lab artifacts; flip the capability from stub to implemented. | Revert window loader + tool commits; capability returns to NEEDS_CAPABILITY. |
| FR-036d MCP strategy-aware promotion readiness drill-down | Research MCP | `BACKLOG` | LOW | FR-036 deployed, existing `promotion_readiness` tool | not_started | Accept a `strategy` argument so "Is Orion ready?" returns Orion's panel specifically rather than the generic challenger verdict. | Revert the strategy-arg patch; tool returns to generic mode. |
| FR-037 Tier 3 promotion governance surfaces | Promotion Governance | `DEPLOYED_OBSERVING` | LOW | FR-028 Phase C, FR-024..027, FR-030, planned consumer FR-038 | observing | Three additive research-only surfaces deployed 2026-06-02: `research/promotion_governance.py` evaluates six gates (observation window / performance / differentiation / risk / universe / execution timing) → `PROMOTE` / `WATCH` / `HOLD` / `DEMOTE` / `BLOCKED`; `research/regime_attribution.py` classifies seven SPY-derived regimes with no look-ahead; `research/dynamic_strategy_allocation.py` evaluates five candidate policies as research-only, never writing production weights. Wired into `research/review_packet.py` with a conservative final control summary (`No promotion recommended` unless every tier agrees and governance explicitly names a candidate). Implementing commits: `c52ae6c` and `fbd4f6a`. | Revert commits `c52ae6c` and `fbd4f6a`; delete `outputs/research/{promotion_governance,regime_attribution,dynamic_strategy_allocation}/`; final control summary falls back to Tier 2 controls. No production allocations or execution behavior change either way. |
| FR-038 governance blocker audit + diagnostic surfaces | Promotion Governance | `DEPLOYED_OBSERVING` | LOW | FR-037 outputs, Tier 1/2 research artifacts | observing | Six additive research-only diagnostic surfaces deployed 2026-06-02: `research/governance_blocker_audit.py` classifies every governance blocker as `REAL` / `DATA_QUALITY` / `CONFIGURATION` / `OBSERVATION_WINDOW`; `research/security_master_reconciliation.py` reconciles holdings/planned/attribution/timing symbols vs the security master; `research/execution_payload_audit.py` diagnoses `planned_execution_payload` state across five hypotheses; `research/differentiation_diagnostic.py` per-pair breakdown with verdicts `TRUE_WEAK_DIFFERENTIATION` / `POSSIBLE_DATA_LIMITATION` / `INSUFFICIENT_HISTORY`; `research/concentration_diagnostic.py` distinguishes actual violations from equal-weight design floors; `research/governance_maturity.py` produces a deterministic 7-component score → `IMMATURE` / `EMERGING` / `DEVELOPING` / `MATURE` / `PROMOTION_READY`. Wired into `research/review_packet.py` final control summary as `blockers_eliminated` / `blockers_remaining` / `data_quality_issues` / `actual_strategy_issues` / `governance_maturity_tier`. Implementing commits: `436cbdf` and `fbd4f6a`. | Revert commits `436cbdf` and `fbd4f6a`; delete `outputs/research/{governance_blocker_audit,security_master_reconciliation,execution_payload_audit,differentiation_diagnostic,concentration_diagnostic,governance_maturity}/`; final control summary falls back to Tier 3-only roll-up (FR-037). No production allocations or execution behavior change either way. |
| FR-055 Intended Portfolio NAV & Operational Drag Attribution | Performance Provenance / Operational Telemetry | `IN_PROGRESS` | LOW | 2026-06-04 operational drag audit, existing planned portfolio/execution artifacts, broker/reconciliation/performance artifacts | implementation_started | Add read-only intended/counterfactual NAV, normalized actual NAV, SPY benchmark alignment, operational drag attribution, stable-window analysis, CLI generation, and research-packet consumption. | Revert FR-055 implementation commit; ignore/delete generated `outputs/operational_drag/<date>/` artifacts. No broker, execution, cron, strategy selection, allocation, or order-routing behavior changes are in scope. |
| FR-056 Operational Drag Source Discovery Patch | Performance Provenance / Operational Telemetry | `IN_PROGRESS` | LOW | FR-055, canonical VM price/NAV/broker/reconciliation/SPY artifacts | implementation_started | Patch FR-055 source discovery/readers so operational drag locates canonical VM price, holdings, reconciliation, NAV, and SPY sources with explicit source-selection diagnostics. | Revert FR-056 reader patch; FR-055 artifacts remain read-only and degraded with missing-data reason codes. No execution, broker, cron, strategy, allocation, or promotion behavior changes are in scope. |
| FR-057 Current Price Hydration for Operational Drag | Performance Provenance / Operational Telemetry | `IN_PROGRESS` | LOW | FR-055, FR-056, existing price hydration/export infrastructure | implementation_started | Hydrate or expose current trade-date price data for intended/actual holdings and SPY so operational drag can mark holdings without stale price gaps. | Revert FR-057 hydration/read-order patch; ignore/delete date-scoped `outputs/operational_drag/<date>/price_hydration.*` artifacts. No execution, broker, cron, strategy, allocation, promotion, or live trading behavior changes are in scope. |
| FR-058 Actual NAV Refresh for Operational Drag | Performance Provenance / Operational Telemetry | `IN_PROGRESS` | LOW | FR-055, FR-056, FR-057, broker-authoritative live-overlay NAV producer, run-scoped NAV snapshots | implementation_started | Harden operational-drag actual-NAV discovery to select the freshest, highest-confidence broker NAV source (not first-file-found), add `source_diagnostics.actual_nav` with `max_available_date`, and emit explicit `actual_nav_from_*` / `actual_nav_stale` / `actual_nav_missing` reason codes so actual-vs-intended-vs-SPY drag can extend through the requested trade date when fresh broker NAV exists. | Revert FR-058 discovery patch; actual-NAV discovery falls back to FR-055 fixed-order readers. No execution, broker, cron, strategy, allocation, promotion, or live trading behavior changes are in scope. |
| FR-059 Broker Telemetry Failure Detection | Operational Telemetry / Service Health | `IN_PROGRESS` | LOW | FR-058A audit (Alpaca 401 silent freeze 2026-05-20→2026-06-04), `scripts/refresh_quant_dashboard.py`, `deploy/caerus-dashboard-refresh.service` | implementation_started | Make Alpaca live-broker telemetry/auth failures loud and alertable: classify failures into reason codes (`alpaca_auth_failed`, `live_broker_required_failed`), add deterministic stale-artifact checks (`nav_artifact_stale`, `broker_snapshot_stale`, `recon_artifact_stale`), surface a structured `live_status` in the refresh output, and enable `--require-live-broker` in the systemd service so bad credentials exit non-zero instead of exit-0 with a swallowed warning. | Revert FR-059 patch + remove `--require-live-broker` from the service unit (sudo cp + daemon-reload); refresh reverts to warn-and-continue. No order execution, broker submission, allocation, strategy, or promotion behavior changes are in scope. |
| FR-060 Intended NAV True Mark-to-Market | Performance Provenance / Operational Telemetry | `IN_PROGRESS` | LOW | FR-055, FR-058, daily precompute plan snapshots, hydrated/historical price store | implementation_started | Fix intended-NAV so `intended_return_daily` is not mechanically 0.0: mark the carried intended holdings to market at each day's prices to derive the rebalance basis, so price moves since the last rebalance flow into the intended return instead of being erased by a same-day target reconstruction. Carry holdings forward between rebalances; no look-ahead; no fabricated prices; keep the same-day reconstruction as a clearly-labeled fallback when carried holdings cannot be priced. | Revert FR-060 patch; intended NAV reverts to same-day reconstruction (drag = 0 − actual). No execution, broker, allocation, strategy, or promotion behavior changes are in scope. |

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

## Immediate Focus

1. Implement FR-032 first because it is read-only/observability work and
   directly closes the operator visibility gap from the 2026-05-28 recovery:
   existing successful runs may predate timeline artifact generation.
2. Keep FR-031 in `DEPLOYED_OBSERVING` until multiple execution runs show
   integrity status and findings are accurate, visible, and not conflated with
   broker/order-routing outcomes.
3. Observe FR-028 Phase C for deterministic sidecar generation, conservative
   readiness classification, no prior-day mutation, and MCP consumption of
   artifact-backed evidence. Keep FR-029 in Friday-governed Track B until Phase
   C evidence is stable.
4. Treat FR-030 as deployed telemetry consumption, not promotion logic.
5. Focus the next research-operations bottleneck on post-close hydration and
   source readiness, not packet rendering.
6. Observe FR-036 (MCP Phase 7) for stability: deterministic routing,
   honest NEEDS_DATA / NEEDS_CAPABILITY returns, no scope creep into
   transport or autonomous orchestration. Keep MCP work separated from
   FR-028 accounting/timing semantics except for read-only MCP
   consumption of Phase C research artifacts. The four FR-036a..d
   follow-on items are scoped backlog work, not in-flight builds.
7. Use FR-015/017/018/023-027/030 outputs as inputs to future attribution,
   source-readiness, and governance reviews without changing execution paths.
8. Observe FR-037 (Tier 3 promotion governance) for stability:
   `final_control_summary.current_recommendation == "No promotion recommended"`
   across at least five consecutive daily packets while strategy data is
   unchanged; `is_research_only=true` and `production_weights_modified=false`
   invariants hold in `dynamic_strategy_allocation.json`; no execution-path
   coupling regression in `Tests/test_promotion_governance.py`,
   `Tests/test_regime_attribution.py`, or
   `Tests/test_dynamic_strategy_allocation.py`. FR-037 may transition to
   `DEPLOYED` once those criteria are satisfied and FR-029 is updated.
9. Observe FR-038 (blocker audit + diagnostics) for stability: blocker
   classifications are stable across daily runs for unchanged inputs;
   `governance_maturity_tier` trajectory is recorded weekly; no regression
   on `Tests/test_governance_blocker_audit.py`,
   `Tests/test_security_master_reconciliation.py`,
   `Tests/test_execution_payload_audit.py`,
   `Tests/test_differentiation_diagnostic.py`,
   `Tests/test_concentration_diagnostic.py`, or
   `Tests/test_governance_maturity.py`. FR-038 may transition to `DEPLOYED`
   alongside FR-037.
10. Implement FR-055 as a read-only operational telemetry feature that closes
    the intended-vs-actual NAV measurement gap identified in
    `reports/trading_audit/operational_drag_audit_2026-06-04.md`. It must emit
    deterministic artifacts and explicit missing-data reason codes before it is
    considered promotion-ready.
11. Patch FR-056 as a source-discovery-only correction for FR-055: improve
    canonical VM artifact readers and diagnostics without changing execution,
    broker, cron, strategy selection, allocation, or promotion behavior.
12. Implement FR-057 as a read-only price hydration/export path for operational
    drag so requested trade-date holdings and SPY prices are available when the
    canonical historical matrices are stale. Missing current prices must remain
    explicit reason codes.

## FR-055 Intended Portfolio NAV & Operational Drag Attribution

- **FR number:** FR-055
- **Title:** Intended Portfolio NAV & Operational Drag Attribution
- **Date started:** 2026-06-04
- **Status:** `IN_PROGRESS`
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

## FR-056 Operational Drag Source Discovery Patch

- **FR number:** FR-056
- **Title:** Operational Drag Source Discovery Patch
- **Date started:** 2026-06-04
- **Status:** `IN_PROGRESS`
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
- **Status:** `IN_PROGRESS`
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

## Roadmap Boundaries

Do not use Phase 4 as a vehicle for microservices, Kubernetes, Airflow, broad
scheduler rewrites, strategy promotion, broker changes, or cron timing changes.
The current bottleneck is operational clarity, not distributed compute scale.
