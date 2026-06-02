---
last_reviewed: 2026-06-02
owner: governance
category: strategic_backlog
criticality: high
canonical: false
related_systems: [governance, research, attribution, telemetry, mcp, alpha_stack]
---

# Caerus Strategic Backlog

## Executive Objective

Maximize research clarity and confidence velocity.

For the next 2-6 weeks, Caerus should optimize for faster interpretation,
cleaner provenance, denser telemetry, and higher deployment confidence. The
near-term bottleneck is not model sophistication or distributed compute. The
bottleneck is knowing exactly what happened, why it happened, whether the
evidence is trustworthy, and what level of capital confidence is justified.

This backlog is additive to the FR governance system. It does not replace
`fr_active_backlog.md`, `fr_registry.md`, `fr_governance_model.md`, or
`operational_lessons.md`.

## Strategic Priorities

| Rank | Priority | Interpretation |
|---:|---|---|
| 1 | Execution trust | Broker, execution, recovery, reconciliation, and rollback evidence must remain boring and explainable. |
| 2 | Telemetry clarity | Operators need concise health, freshness, artifact ownership, and degraded-state signals. |
| 3 | Attribution speed | Convert performance reporting from "what happened" to "what drove it" with daily contribution and exposure evidence. |
| 4 | Provenance | Every important claim should identify its truth surface, source artifacts, confidence level, and governance coverage. |
| 5 | Regime understanding | Explain when strategies work, when they become fragile, and whether outperformance is regime-dependent. |
| 6 | Promotion confidence | Promotion decisions should consume provenance, timing confidence, exposure risk, and observation evidence. **Advanced 2026-06-02 via FR-037 (Tier 3 promotion governance surfaces) and FR-038 (blocker audit + diagnostic surfaces). Final control summary now distinguishes data-quality from real strategy issues and reports a deterministic governance maturity tier. FR-029 is `IN_PROGRESS` tracked via the two `DEPLOYED_OBSERVING` children.** |
| 7 | MCP follow-on capabilities | Phase 7 MCP shipped 2026-05-29 (20 tools, capability router). Continue adding capabilities on the deterministic, read-only contract; do not add transport, agents, or LLM calls inside the MCP. Conformance audit vs frozen semantic layer is next. |
| 8 | Advanced modeling later | Regime switching, meta-labeling, ensembles, and optimization should wait until telemetry and interpretation are stronger. |

## Initiative Categories

### A. Governance / Operational Integrity

Purpose: preserve execution integrity, rollback simplicity, and operator trust.

Focus areas:

- Existing FR system normalization and active backlog discipline.
- Telemetry governance for required, optional, stale, degraded, and missing artifacts.
- Provenance and truth-surface classification for performance claims.
- Freshness manifests for `latest`-style publications.
- Rollback discipline and observation-window evidence.
- Read-only validation isolation before deeper semantic validation.

### B. Research Clarity Acceleration

Purpose: accelerate confidence formation and explain why strategies are winning
or losing.

Focus areas:

- Position attribution and contribution-to-return.
- Exposure decomposition across beta, sector, volatility, momentum, liquidity,
  turnover, and concentration.
- Signal diagnostics and challenger differentiation.
- Regime interpretation and fragility analysis.
- Confidence surfaces for NAV, attribution, exposure, and promotion evidence.
- Challenger analysis for Polaris, Orion, Lyra, and SPY benchmark context.
- CIO-style research packet: concise weekly evidence, risks, and decisions.

### C. MCP / Research Operating System

> **Status update (2026-05-29):** the "planning only" framing below is
> partially obsolete. The MCP has shipped as a Phase 7 read-only operator
> intelligence layer with 20 tools, a capability-based question router,
> and an operator gateway (`scripts/research_mcp_ask.py`). The full
> matrix of what is implemented vs. planned vs. conceptual is in
> [`../architecture/research_mcp_current_state_2026-05-29.md`](../architecture/research_mcp_current_state_2026-05-29.md).
> This section retains the original boundary text below as historical
> context for the planning posture that gated the build.

Purpose: plan the future research operating system without prematurely building
transport or orchestration.

Current boundary (as implemented 2026-05-29):

- MCP is **deployed as a read-only Phase 7 operator intelligence layer**.
  20 tools registered, 9 capabilities in the registry (6 implemented,
  2 stubs returning `NEEDS_CAPABILITY`, 1 covered by an existing tool).
- No execution-path coupling (constitutional contract, enforced by
  [`MCP_IMPLEMENTATION_BOUNDARIES_v1.md`](../architecture/semantics/MCP_IMPLEMENTATION_BOUNDARIES_v1.md)).
- No autonomous agents, orchestration, or workflow triggering.
- No LLM call inside the MCP; classification is regex against the
  `CAPABILITY_REGISTRY` (deterministic).

Implemented surfaces:

- Artifact retrieval over semantic research objects (registry index +
  query facade, lineage edges, surface-conflict detection).
- Semantic querying by type, surface, confidence, and governance state
  (`query_registry` tool).
- Operator daily-state intelligence (`morning_cio_brief`,
  `daily_operator_brief`, `anomaly_report`, `promotion_readiness`,
  `artifact_status`, `artifact_drilldown`).
- Execution-timing research (`execution_timing_summary`,
  `execution_timing_by_vix_regime`).
- Shadow-strategy comparison (`shadow_comparison`).
- Natural-language question routing through a deterministic capability
  registry with OK / NEEDS_DATA / NEEDS_CAPABILITY / UNSUPPORTED_INTENT
  terminal statuses.

Planning areas still open (see Priorities §F in the current-state doc):

- Conformance audit of the implementation against the frozen
  `semantics/` layer (SEM-001..008).
- `attribution_analysis` capability (currently a recognised stub
  returning `NEEDS_CAPABILITY`).
- `stable_window_evaluation` capability (same).
- Strategy-aware promotion readiness drill-down.
- Telemetry / observability surfaces for the planner itself.

### D. Advanced Modeling Roadmap

Purpose: preserve a path to deeper alpha research without letting modeling
complexity outrun observability.

Candidate roadmap:

- Regime switching models.
- Markov transition analysis.
- Meta-labeling for entry/exit quality.
- Ensemble weighting across Polaris, Orion, Lyra, and future challengers.
- Portfolio optimization under concentration and turnover constraints.
- Future alpha initiatives after attribution, telemetry, and provenance are
  decision-grade.

### E. Capital Deployment Readiness

Purpose: prepare for disciplined capital scaling without weakening execution or
governance standards.

Focus areas:

- Constrained capital rollout design.
- Operational confidence thresholds.
- Broker trust and reconciliation evidence.
- Slippage and fill realism analysis.
- Promotion governance hardening.
- Scaling discipline tied to drawdown, liquidity, concentration, and timing
  confidence.

## Prioritized Roadmap

| Initiative | Category | Priority | Blast Radius | Research Clarity Impact | Dependencies | Estimated Horizon | Current Status |
|---|---|---:|---|---|---|---|---|
| FR-015 artifact registry and ownership matrix | Governance / Operational Integrity | 1 | LOW | HIGH | None | Week 1 | `DEPLOYED` |
| FR-017 operational health aggregator | Governance / Operational Integrity | 2 | LOW | HIGH | FR-015 preferred | Week 1 | `DEPLOYED` |
| FR-018 latest publication freshness manifest | Governance / Operational Integrity | 3 | LOW | HIGH | FR-015 preferred | Week 1 | `DEPLOYED` |
| FR-023 documentation and generated artifact separation | Governance / Operational Integrity | 4 | LOW | MEDIUM | FR-015 preferred | Week 1 | `DEPLOYED` |
| Strategic backlog consolidation | Governance / Operational Integrity | 5 | LOW | HIGH | Existing FR docs | Week 1 | `DEPLOYED` |
| FR-019 runtime artifact retention and backup policy | Governance / Operational Integrity | 6 | LOW | MEDIUM | FR-015, FR-018 | Week 2-3 | `DEPLOYED` |
| FR-020 read-only validation isolation policy | Governance / Operational Integrity | 7 | LOW | MEDIUM | FR-015, FR-019 | Week 2-3 | `DEPLOYED` |
| Interpretation layer planning | Research Clarity Acceleration | 8 | LOW | HIGH | FR-015, FR-017 | Week 1 | `DEPLOYED` |
| FR-024 NAV surface registry and performance provenance enforcement | Research Clarity Acceleration | 9 | LOW | HIGH | FR-015 | Week 2 | `DEPLOYED` |
| FR-025 immutable daily shadow holdings and weights history | Research Clarity Acceleration | 10 | MEDIUM | HIGH | FR-024 | Week 2 | `DEPLOYED` |
| FR-026 exposure intelligence and concentration risk observability | Research Clarity Acceleration | 11 | LOW | HIGH | FR-024, FR-025 | Week 2 | `DEPLOYED` |
| FR-027 regime decomposition and fragility reporting | Research Clarity Acceleration | 12 | LOW | HIGH | FR-026 | Week 2 | `DEPLOYED` |
| FR-030 daily research interpretation packet v1 | Research Clarity Acceleration | 13 | LOW | HIGH | FR-015, FR-017, FR-018, FR-024 through FR-027 | Week 2-3 | `DEPLOYED` |
| Post-close source readiness and hydration confidence | Research Clarity Acceleration | 14 | LOW | HIGH | FR-030, hydration artifacts | Week 2-3 | `DEPLOYED_OBSERVING` |
| Attribution acceleration packet | Research Clarity Acceleration | 15 | LOW | HIGH | FR-025, FR-026, FR-030 | Week 2-3 | BACKLOG |
| Regime diagnostics packet | Research Clarity Acceleration | 16 | LOW | HIGH | FR-027, FR-030 | Week 2-3 | BACKLOG |
| Exposure intelligence weekly review | Research Clarity Acceleration | 17 | LOW | HIGH | FR-026, FR-030 | Week 2-3 | BACKLOG |
| FR-028 timing semantics candidate review | Governance / Operational Integrity | 18 | HIGH | HIGH | FR-024 through FR-027, FR-030 interpretation evidence | Week 3-4 | `BACKLOG` |
| FR-029 promotion governance hardening | Capital Deployment Readiness | 19 | MEDIUM | HIGH | FR-028, FR-037, FR-038 | Week 4-6 | `IN_PROGRESS` (FR-037 + FR-038 `DEPLOYED_OBSERVING` 2026-06-02) |
| FR-037 Tier 3 promotion governance surfaces (six-gate evaluation, regime attribution, research-only allocation) | Capital Deployment Readiness | 19a | LOW | HIGH | FR-028 Phase C, FR-024..027, FR-030 | Week 4-6 | `DEPLOYED_OBSERVING` 2026-06-02 |
| FR-038 governance blocker audit + diagnostic surfaces (blocker classification, security master reconciliation, payload audit, differentiation diagnostic, concentration diagnostic, governance maturity score) | Capital Deployment Readiness | 19b | LOW | HIGH | FR-037 | Week 4-6 | `DEPLOYED_OBSERVING` 2026-06-02 |
| FR-039 security master refresh job | Governance / Operational Integrity | 19c | MEDIUM | HIGH | FR-038 audit findings | Week 5-6 | BACKLOG |
| FR-040 governance threshold calibration (N-aware concentration caps, per-strategy thresholds) | Capital Deployment Readiness | 19d | LOW | MEDIUM | FR-037, FR-038 | Week 5-6 | BACKLOG |
| FR-041 governance trajectory (day-over-day blocker / maturity tier tracking) | Research Clarity Acceleration | 19e | LOW | MEDIUM | FR-038 outputs | Week 5-6 | BACKLOG |
| FR-042 CIO briefing audit integration (wire `blockers_eliminated` / `governance_maturity_tier` into the CIO narrative) | Research Clarity Acceleration | 19f | LOW | MEDIUM | FR-038 outputs | Week 5-6 | BACKLOG |
| FR-043 differentiation remediation playbook | Research Clarity Acceleration | 19g | LOW | MEDIUM | FR-038 differentiation diagnostic | Week 6-7 | BACKLOG |
| FR-044 audit conformance review vs frozen semantics layer (SEM-001..008) | Governance / Operational Integrity | 19h | LOW | MEDIUM | FR-036a pattern, FR-037, FR-038 | Week 6-7 | BACKLOG |
| MCP research operating system architecture | MCP / Research Operating System | 20 | LOW | MEDIUM | Registry/query semantics and FR-030 packet artifacts | Week 3-6 | `DEPLOYED_OBSERVING` (Phase 7 ship 2026-05-29; 20 tools, 9 capabilities, 6 implemented; see [current-state doc](../architecture/research_mcp_current_state_2026-05-29.md)) |
| MCP conformance audit vs frozen semantic layer | MCP / Research Operating System | 20a | LOW | HIGH | SEM-001..008 frozen, MCP implementation live | Week 6-7 | BACKLOG |
| MCP `attribution_analysis` capability promotion | MCP / Research Operating System | 20b | LOW | HIGH | `outputs/attribution/` artifacts, `AttributionArtifactAdapter` family | Week 7-8 | BACKLOG |
| MCP `stable_window_evaluation` capability promotion | MCP / Research Operating System | 20c | LOW | MEDIUM | `outputs/research/stable_window_evaluation/` + `random_windows_*.csv` | Week 7-8 | BACKLOG |
| MCP strategy-aware promotion readiness drill-down | MCP / Research Operating System | 20d | LOW | MEDIUM | Existing `promotion_readiness` tool extension | Week 7 | BACKLOG |
| CIO-style research packet | Research Clarity Acceleration | 21 | LOW | HIGH | Attribution, exposure, regime artifacts, FR-030 | Week 3-4 | BACKLOG |
| Slippage and execution realism review | Capital Deployment Readiness | 22 | MEDIUM | MEDIUM | Broker/reconciliation evidence | Week 4-6 | BACKLOG |
| Constrained capital rollout plan | Capital Deployment Readiness | 23 | HIGH | MEDIUM | FR-029, broker trust, timing confidence | Week 5-6 | BACKLOG |
| Regime switching / Markov research | Advanced Modeling Roadmap | 24 | LOW | MEDIUM | Regime diagnostics stable | Later | Deferred |
| Meta-labeling and ensemble weighting | Advanced Modeling Roadmap | 25 | LOW | MEDIUM | Attribution and signal diagnostics stable | Later | Deferred |
| Optimization research | Advanced Modeling Roadmap | 26 | MEDIUM | LOW until telemetry matures | Exposure and capital rules stable | Later | Deferred |

## Two-Week Tactical Roadmap

### Week 1

Status: deployed.

Primary objective was to make operational interpretation clearer without
touching execution behavior.

Work items:

- FR-015: complete the artifact registry and ownership matrix.
- FR-017: keep the operational health aggregator read-only and operator-facing.
- FR-018: define freshness manifests for `latest`-style artifacts.
- FR-023: clarify generated artifacts versus canonical documentation.
- Backlog consolidation: use this document as the strategic crosswalk.
- Interpretation layer planning: weekly evidence packet boundary documented in
  `docs/weekly_research_synthesis.md`, including performance, provenance,
  attribution, exposure, regime, and open risks.

Validation posture:

- Docs and read-only tooling first.
- No cron changes.
- No execution-path coupling.
- No dashboard substitution for missing canonical data.

### Week 2

Status: deployed, with post-close hydration reliability still active as the
next research-operations bottleneck.

Primary objective was to convert research outputs into faster confidence
formation.

Work items:

- FR-024: promote explicit NAV surface and performance provenance artifacts.
- FR-025: continue immutable daily holdings and weights history.
- FR-026: operationalize exposure intelligence and concentration risk flags.
- FR-027: expand regime decomposition and fragility reporting.
- FR-030: generate the first daily research interpretation packet from
  provenance, freshness, exposure, concentration, and regime telemetry.
- FR-019: define retention classes, backup boundaries, and evidence holds
  before any cleanup automation exists.
- FR-020: define validation isolation rules so tests and smoke checks cannot be
  mistaken for production runtime evidence.
- Orion.command: launch the FR-030 packet workflow from the VM, retrieve the
  packet bundle locally, and keep source-readiness warnings operator-visible.
- Source readiness: deployed read-only diagnostics for research source
  readiness, price hydration health, cache lag, hydration-window
  classification, and Orion blocking guidance.
- Attribution acceleration: identify daily top contributors, detractors,
  concentration contribution, and turnover effects.
- Regime diagnostics: summarize what regimes help or hurt Polaris, Orion, and
  Lyra.
- Exposure intelligence: explain beta, sector, momentum, volatility, liquidity,
  and concentration drift in CIO-readable language.

Validation posture:

- Additive artifacts only.
- Preserve current accounting semantics until FR-028 governance review.
- Keep operational shadow NAV LOW confidence where timing assumptions remain
  unresolved.

## FR-030 Daily Research Packet Boundary

FR-030 operationalizes telemetry consumption. It is the first daily
operator-facing synthesis layer over provenance-aware, freshness-aware, and
confidence-aware research artifacts.

FR-030 scope:

- generate dated `packet.md`, `packet.json`, `packet.html`, and `summary.json`;
- summarize operational trust, strategy comparison, exposure, concentration,
  regime, fragility, freshness, and confidence caveats;
- preserve LOW confidence for operational shadow NAV until FR-028;
- prepare future email, dashboard, and MCP retrieval surfaces.

FR-030 is not:

- execution automation;
- promotion logic;
- accounting or timing semantics migration;
- cron or workflow integration;
- dashboard deployment;
- autonomous research interpretation.

Current bottleneck:

- post-close hydration reliability and refreshed shadow artifact availability,
  not packet rendering;
- stale or `NO_DATA` shadow artifacts should block or clearly downgrade packet
  interpretation;
- incomplete packet generation remains advisory and requires explicit operator
  override.

## MCP Planning Boundary

MCP is NOT FR-028.

FR-028 is the governed accounting/timing semantics candidate path for shadow
performance interpretation. MCP is a separate research operating system
surface that consumes FR-028 artifacts read-only and does not redefine them.

Current MCP posture (updated 2026-05-29):

- **Phase 7 shipped: read-only operator intelligence + research-question
  layer is live.** 20 MCP tools registered, capability-based router,
  operator gateway at `scripts/research_mcp_ask.py`.
- Stdio JSON-RPC transport layer present (`scripts/research_registry_mcp_server.py`).
- No autonomous runtime, no workflow execution, no broker access, no
  dashboard integration — these constitutional boundaries hold and are
  enforced by [`MCP_IMPLEMENTATION_BOUNDARIES_v1.md`](../architecture/semantics/MCP_IMPLEMENTATION_BOUNDARIES_v1.md).
- No LLM call inside the MCP; classification is deterministic regex.

The MCP consumes the research registry, provenance graph, confidence
lattice, governance metadata, and temporal reconstruction rules. It does
not redefine them — that contract is unchanged from the original
planning boundary.

For what the MCP can and cannot answer today (matrix + maturity level),
see [`../architecture/research_mcp_current_state_2026-05-29.md`](../architecture/research_mcp_current_state_2026-05-29.md).

## Guiding Principles

- Optimize for research clarity.
- Additive first.
- Provenance before automation.
- Observability before sophistication.
- Interpretation before scaling.
- Fast learning loops over architectural perfection.
- Preserve deterministic execution behavior.
- Keep rollback simple.
- Do not let architecture outrun evidence.
- Treat confidence as an output of lineage, not a narrative choice.

## Assumptions And Risks

Assumptions:

- The current execution system remains paper-only and Polaris-only for orders.
- Orion and Lyra remain shadow-only until governance says otherwise.
- FR-024 through FR-027 remain additive research infrastructure.
- FR-028 remains high blast-radius and must not silently rewrite historical
  performance semantics.
- MCP is now `DEPLOYED_OBSERVING` (Phase 7 ship 2026-05-29); registry / query semantics are stable. Further capability additions follow the §F priority list in [`../architecture/research_mcp_current_state_2026-05-29.md`](../architecture/research_mcp_current_state_2026-05-29.md).

Risks:

- Too much parallel planning can dilute Week 1 execution on artifact governance.
- Advanced modeling can create false confidence if attribution and exposure
  evidence remain thin.
- Shadow performance claims remain vulnerable until timing semantics are
  governed.
- Capital deployment work must not front-run broker trust, slippage evidence,
  and promotion governance.
- MCP scope creep could introduce orchestration complexity before the trust
  substrate is ready.

## Future Extension Opportunities

- A weekly CIO research packet generated from additive artifacts.
- A promotion confidence scorecard that consumes provenance, exposure, timing,
  and regime evidence. (Realized 2026-06-02 via FR-037 + FR-038. Future work:
  FR-039..044 follow-on items below.)
- A read-only research registry query layer for operator review.
- MCP retrieval tools after registry semantics are stable.
- Scenario-specific capital rollout plans once execution trust and promotion
  gates are stronger.
- Advanced modeling experiments after telemetry density supports reliable
  interpretation.

### Follow-on items unlocked by FR-037 / FR-038

- **FR-039 security master refresh job** — eliminate the data-quality
  cascade that FR-038's blocker audit flagged on local environments.
  OPS category; docs-first then runtime.
- **FR-040 governance threshold calibration** — decide whether the
  concentration cap should be N-aware (so a 5-position equal-weight
  strategy is not reported as a `CONFIGURATION` violation by construction)
  or whether the strategy should hold more names. Decision belongs in
  governance docs before any code changes.
- **FR-041 governance trajectory** — turn the static governance
  maturity tier and blocker classification mix into a day-over-day
  trajectory artifact so operators can see progress without comparing
  packets manually.
- **FR-042 CIO briefing audit integration** — wire
  `blockers_eliminated`, `blockers_remaining`, and
  `governance_maturity_tier` into the CIO narrative so the operator
  story reflects the audit findings, not just the raw governance verdict.
- **FR-043 differentiation remediation playbook** — the differentiation
  diagnostic confirmed `TRUE_WEAK_DIFFERENTIATION` against 60+ day
  history. Document the remediation menu (selection-logic change vs.
  correlation cap calibration) so the strategy team has a clear path.
- **FR-044 audit conformance review** — mirror FR-036a's MCP conformance
  audit by mapping each FR-038 classification taxonomy element against
  the frozen `docs/architecture/semantics/` SEM-001..008 contracts and
  flagging any semantic drift. Pure docs deliverable.

## Validation Notes

This document introduces no runtime behavior changes. It is an operator-readable
strategic planning layer that preserves the existing FR governance model,
status vocabulary, blast-radius framework, and rollback discipline.
