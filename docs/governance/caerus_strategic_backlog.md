---
last_reviewed: 2026-05-22
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
| 6 | Promotion confidence | Promotion decisions should consume provenance, timing confidence, exposure risk, and observation evidence. |
| 7 | MCP planning | Track the future research operating system now, but keep it architecture/planning only until registry semantics are stable. |
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

Purpose: plan the future research operating system without prematurely building
transport or orchestration.

Current boundary:

- MCP is architecture/planning only for now.
- No production MCP implementation yet.
- No execution-path coupling.
- No autonomous agents, orchestration, or workflow triggering.

Future planning areas:

- Artifact retrieval over semantic research objects.
- Semantic querying by surface, confidence, governance, lineage, and time.
- Experiment lineage and replay-safe reconstruction.
- Agent boundaries and read-only trust model.
- Telemetry APIs for future retrieval layers.
- Provenance, confidence, and governance model as first-class query substrate.

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
| FR-015 artifact registry and ownership matrix | Governance / Operational Integrity | 1 | LOW | HIGH | None | Week 1 | `IN_PROGRESS` |
| FR-017 operational health aggregator | Governance / Operational Integrity | 2 | LOW | HIGH | FR-015 preferred | Week 1 | `IN_PROGRESS` |
| FR-018 latest publication freshness manifest | Governance / Operational Integrity | 3 | LOW | HIGH | FR-015 preferred | Week 1 | `IN_PROGRESS` |
| FR-023 documentation and generated artifact separation | Governance / Operational Integrity | 4 | LOW | MEDIUM | FR-015 preferred | Week 1 | `IN_PROGRESS` |
| Strategic backlog consolidation | Governance / Operational Integrity | 5 | LOW | HIGH | Existing FR docs | Week 1 | Drafted here |
| Interpretation layer planning | Research Clarity Acceleration | 6 | LOW | HIGH | FR-015, FR-017 | Week 1 | BACKLOG |
| FR-024 NAV surface registry and performance provenance enforcement | Research Clarity Acceleration | 7 | LOW | HIGH | FR-015 | Week 2 | `PROMOTION_READY` |
| FR-025 immutable daily shadow holdings and weights history | Research Clarity Acceleration | 8 | MEDIUM | HIGH | FR-024 | Week 2 | `PROMOTION_READY` |
| FR-026 exposure intelligence and concentration risk observability | Research Clarity Acceleration | 9 | LOW | HIGH | FR-024, FR-025 | Week 2 | `PROMOTION_READY` |
| FR-027 regime decomposition and fragility reporting | Research Clarity Acceleration | 10 | LOW | HIGH | FR-026 | Week 2 | `PROMOTION_READY` |
| FR-030 daily research interpretation packet v1 | Research Clarity Acceleration | 11 | LOW | HIGH | FR-015, FR-017, FR-018, FR-024 through FR-027 | Week 2-3 | `PROMOTION_READY` |
| Attribution acceleration packet | Research Clarity Acceleration | 12 | LOW | HIGH | FR-025, FR-026, FR-030 | Week 2-3 | BACKLOG |
| Regime diagnostics packet | Research Clarity Acceleration | 13 | LOW | HIGH | FR-027, FR-030 | Week 2-3 | BACKLOG |
| Exposure intelligence weekly review | Research Clarity Acceleration | 14 | LOW | HIGH | FR-026, FR-030 | Week 2-3 | BACKLOG |
| FR-028 timing semantics candidate review | Governance / Operational Integrity | 15 | HIGH | HIGH | FR-024 through FR-027, FR-030 interpretation evidence | Week 3-4 | `BACKLOG` |
| FR-029 promotion governance hardening | Capital Deployment Readiness | 16 | MEDIUM | HIGH | FR-028 | Week 4-6 | `BACKLOG` |
| MCP research operating system architecture | MCP / Research Operating System | 17 | LOW | MEDIUM | Registry/query semantics and FR-030 packet artifacts | Week 3-6 | Planning only |
| CIO-style research packet | Research Clarity Acceleration | 18 | LOW | HIGH | Attribution, exposure, regime artifacts, FR-030 | Week 3-4 | BACKLOG |
| Slippage and execution realism review | Capital Deployment Readiness | 19 | MEDIUM | MEDIUM | Broker/reconciliation evidence | Week 4-6 | BACKLOG |
| Constrained capital rollout plan | Capital Deployment Readiness | 20 | HIGH | MEDIUM | FR-029, broker trust, timing confidence | Week 5-6 | BACKLOG |
| Regime switching / Markov research | Advanced Modeling Roadmap | 21 | LOW | MEDIUM | Regime diagnostics stable | Later | Deferred |
| Meta-labeling and ensemble weighting | Advanced Modeling Roadmap | 22 | LOW | MEDIUM | Attribution and signal diagnostics stable | Later | Deferred |
| Optimization research | Advanced Modeling Roadmap | 23 | MEDIUM | LOW until telemetry matures | Exposure and capital rules stable | Later | Deferred |

## Two-Week Tactical Roadmap

### Week 1

Primary objective: make operational interpretation clearer without touching
execution behavior.

Work items:

- FR-015: complete the artifact registry and ownership matrix.
- FR-017: keep the operational health aggregator read-only and operator-facing.
- FR-018: define freshness manifests for `latest`-style artifacts.
- FR-023: clarify generated artifacts versus canonical documentation.
- Backlog consolidation: use this document as the strategic crosswalk.
- Interpretation layer planning: define the weekly evidence packet, including
  performance, provenance, attribution, exposure, regime, and open risks.

Validation posture:

- Docs and read-only tooling first.
- No cron changes.
- No execution-path coupling.
- No dashboard substitution for missing canonical data.

### Week 2

Primary objective: convert research outputs into faster confidence formation.

Work items:

- FR-024: promote explicit NAV surface and performance provenance artifacts.
- FR-025: continue immutable daily holdings and weights history.
- FR-026: operationalize exposure intelligence and concentration risk flags.
- FR-027: expand regime decomposition and fragility reporting.
- FR-030: generate the first daily research interpretation packet from
  provenance, freshness, exposure, concentration, and regime telemetry.
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

## MCP Planning Boundary

MCP is NOT FR-028.

FR-028 is the governed accounting/timing semantics candidate path for shadow
performance interpretation. MCP is a separate future research operating system
initiative.

Current MCP posture:

- Planning and architecture only.
- No production implementation.
- No transport layer.
- No server.
- No autonomous runtime.
- No workflow execution.
- No broker access.
- No dashboard integration.

Future MCP should consume the research registry, provenance graph, confidence
lattice, governance metadata, and temporal reconstruction rules. It should not
be allowed to redefine them.

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
- MCP remains planning-only until local registry/query semantics are stable.

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
  and regime evidence.
- A read-only research registry query layer for operator review.
- MCP retrieval tools after registry semantics are stable.
- Scenario-specific capital rollout plans once execution trust and promotion
  gates are stronger.
- Advanced modeling experiments after telemetry density supports reliable
  interpretation.

## Validation Notes

This document introduces no runtime behavior changes. It is an operator-readable
strategic planning layer that preserves the existing FR governance model,
status vocabulary, blast-radius framework, and rollback discipline.
