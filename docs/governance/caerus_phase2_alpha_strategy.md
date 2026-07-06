---
last_reviewed: 2026-06-25
owner: Caerus Research Program
category: governance
criticality: high
canonical: true
related_systems: [alpha_research, research_data, sleeve_migration, governance]
runtime_impact: documentation_only
---

# Caerus Phase 2 Alpha Strategy

Status: Canonical strategy framework

Execution impact: none. This document does not approve trading, broker
submission, scheduler changes, allocation changes, sleeve promotion, or runtime
Research Data Platform consumer migration.

## 1. Executive Summary

Caerus Phase 1 matured the operating system: execution is governed and
observable, the research sleeve lifecycle exists, the Research Data Platform
(RDP) is observe-only ready for core momentum sleeves, and Polaris, Lyra, and
Orion pass canonical-data parity. Phase 2 shifts the program from building
infrastructure to proving incremental alpha.

The Phase 2 mission is to build differentiated investment intelligence while
preventing research sprawl. Every new sleeve, dataset, feature, or promotion
proposal must cite a specific investment hypothesis, expected alpha or risk
contribution, data readiness status, evidence requirement, measurement plan,
and stop/defer condition.

Priority order:

1. Cassiopeia SEC-event MVP.
2. Value and Quality observe-only candidates using RDP fundamentals.
3. Argo as an evidence consumer and research-priority engine.
4. Phoenix only after a short-interest source or capacity-redesign decision.
5. Cygnus only after an analyst-revision or consensus-source decision.

RDP parity is necessary for migration readiness, but it is not sufficient for
promotion. Backtest performance is also not sufficient. Promotion requires
point-in-time data, live or shadow observation evidence, capacity evidence,
incremental value versus existing sleeves, risk evidence, and governance review.

## 2. Phase 1 Completion Statement

Phase 1 is not perfect, but the platform is mature enough to change strategic
focus. The following foundations now exist:

- governed paper/live execution controls and fail-closed behavior;
- observable execution, reliability, reconciliation, and operator artifacts;
- FR-069 research sleeve lifecycle and evidence-envelope discipline;
- FR-DH/RDP catalog, hydration, normalization, freshness, validation, feature,
  observability, API, migration readiness, and parity surfaces;
- observe-only RDP parity for core momentum sleeves;
- post-RDP strategic assessment at
  `docs/governance/post_rdp_strategic_assessment.md`;
- RDP architecture record at
  `docs/architecture/research_data_platform.md`.

Remaining Phase 1 debt should be handled as platform maintenance. It should not
consume the primary research agenda unless it blocks a named Phase 2 hypothesis.

## 3. Phase 2 Mission: Alpha Generation

Phase 2 exists to answer one question: which incremental data and sleeve
hypotheses improve the Caerus portfolio after accounting for point-in-time
safety, capacity, overlap, operational risk, and promotion evidence?

The mission is not to add every possible dataset. The mission is to prove or
falsify specific investment hypotheses with the smallest reliable data and
engineering path.

Phase 2 success means Caerus can say:

- which differentiated return streams deserve more capital or shadow attention;
- which data sources measurably improve signal quality or risk control;
- which sleeves are redundant, blocked, or not viable;
- which vendor decisions are worth paying for;
- which research work should stop.

## 4. Investment Hypotheses

| ID | Hypothesis | Primary initiative | Required data | Expected contribution | Initial status |
|---|---|---|---|---|---|
| P2-H1 | Public SEC events can identify catalyst-driven underreaction that is differentiated from core momentum. | Cassiopeia SEC-event MVP | SEC events, security master, prices, corporate actions, freshness | New event-driven alpha stream with low-to-moderate overlap | Highest priority |
| P2-H2 | PIT valuation features can add a differentiated value sleeve when measured against momentum and quality overlap. | Value observe-only candidate | PIT fundamentals, fundamental features, prices, security master | Fundamental alpha and diversification | High priority |
| P2-H3 | PIT quality features can improve robustness and attribution even if standalone quality overlaps with Polaris. | Quality observe-only candidate | PIT fundamentals, quality features, prices, security master | Core alpha durability, risk control, and factor explanation | High priority |
| P2-H4 | A governed evidence consumer can improve research allocation without becoming a hidden allocator. | Argo evidence engine | RDP observability, parity, migration readiness, shadow evidence, macro regime features | Better research ROI and future capital-allocation evidence | High priority, advisory only |
| P2-H5 | Crisis reversal can work only if capacity and crowding constraints are solved before signal tuning. | Phoenix redesign decision | PIT prices, liquidity, short interest if approved, capacity evidence | Differentiated crisis alpha if viable | Conditional |
| P2-H6 | Earnings drift requires PIT consensus, surprise, or analyst-revision data before Cygnus v1 can be decision-grade. | Cygnus v1 source decision | Analyst revisions, consensus, EPS surprise, filings, prices | Catalyst continuation alpha | Deferred pending source decision |

No new dataset should be added unless it supports one of these hypotheses or a
future hypothesis approved through this document's gate.

## 5. Sleeve Priority Framework

| Priority | Sleeve or layer | Phase 2 role | Why it ranks here | Required next decision |
|---:|---|---|---|---|
| 1 | Cassiopeia | Event-driven alpha sleeve | Public SEC data can produce a PIT-safe MVP with differentiated return drivers. | Approve SEC-event MVP scope and evidence contract. |
| 2 | Value | Fundamental alpha sleeve | RDP fundamentals make a canonical value prototype practical. | Define valuation features and overlap measurement. |
| 3 | Quality | Fundamental robustness sleeve | Quality can test whether Polaris durability is explainable or improvable. | Define quality features and standalone-vs-diagnostic role. |
| 4 | Argo | Evidence consumer | Argo can rank evidence and blockers without touching allocation. | Keep advisory-only and consume Phase 2 evidence. |
| 5 | Phoenix | Conditional crisis-reversal sleeve | Current candidate failed capacity; only a redesign or source change justifies more work. | Choose capacity redesign, short-interest source audit, or hold. |
| 6 | Cygnus | Deferred earnings-drift sleeve | v0 is shelved and v1 is vendor-gated. | Decide analyst/consensus source before implementation. |
| 7 | Future sleeves | Intake only | New sleeve ideas should not outrank current evidence backlog. | Require hypothesis, data, capacity, and stop criteria before work starts. |

## 6. Dataset Contribution Framework

Every new data source must have a measurable expected contribution. The default
answer to "can we add this dataset?" is "only if it improves a named
hypothesis."

| Dataset family | Primary hypothesis | Expected alpha or risk contribution | Measurement |
|---|---|---|---|
| SEC events | P2-H1 | Catalyst timing, event taxonomy, campaign signals | Event-window returns, hit rate, overlap, drawdown, capacity |
| Insider transactions | P2-H1, P2-H3 | Management behavior and event confirmation | Incremental IC, event interaction, false-positive reduction |
| PIT fundamentals | P2-H2, P2-H3 | Value, quality, profitability, leverage, growth | Factor IC, spread returns, sector-neutral returns, overlap |
| Macro and VIX regime | P2-H4 | Context for evidence scoring and risk interpretation | Regime-conditioned sleeve performance and stability |
| Short interest | P2-H5 | Crowding, squeeze risk, crisis-reversal context | Phoenix candidate quality, capacity, reversal asymmetry |
| Analyst revisions | P2-H6, later P2-H1 | Earnings drift and catalyst confirmation | Revision-timing IC, post-event drift, publication lag audit |
| Options IV/OI | Future approved hypothesis | Volatility, hedging, sentiment, crash-risk context | Incremental signal after cost and complexity adjustment |
| News metadata | Future approved hypothesis or P2-H1 extension | Event detection and entity linking | Timestamp quality, precision/recall, event novelty |
| Sentiment embeddings | Future approved hypothesis | Unstructured event interpretation | Model-version stability and incremental alpha after news controls |
| Alternative data | Future approved hypothesis only | Unknown | Named dataset legal, cost, PIT, and contribution review |

Dataset approval gate:

- dataset_id and catalog entry;
- Phase 2 Hypothesis reference;
- expected alpha/risk contribution;
- measurement plan;
- source, cost, licensing, and credential status;
- PIT and freshness policy;
- stop/defer condition.

## 7. Promotion Evidence Standard

No sleeve should be promoted based only on backtest performance. A promotion
proposal must include:

- cited Phase 2 Hypothesis;
- canonical RDP/data readiness status;
- point-in-time data certification;
- source lineage and freshness evidence;
- benchmark and existing-sleeve comparison;
- incremental alpha or risk contribution measurement;
- overlap and correlation analysis;
- capacity, liquidity, and turnover evidence;
- cost and slippage sensitivity;
- drawdown and failure-mode review;
- shadow or live-observation evidence appropriate to the promotion level;
- explicit rollback and stop criteria.

RDP parity is necessary but not sufficient. It proves canonical data-path
equivalence for migrated inputs; it does not prove alpha quality, robustness,
capacity, or promotion readiness.

## 8. Incremental Alpha Measurement

Every Phase 2 experiment must measure incremental value against the existing
Caerus opportunity set, not just standalone performance.

Required measurements:

- absolute return, excess return, Sharpe, drawdown, and hit rate;
- factor and sleeve overlap versus Polaris, Orion, Lyra, and SPY;
- incremental information coefficient or event-window return where applicable;
- regime-conditioned performance;
- capacity and liquidity-adjusted expected return;
- transaction-cost sensitivity;
- deterioration under conservative delays, publication lags, and stale-data
  assumptions;
- incremental portfolio contribution when combined with existing sleeves.

Claims should distinguish:

- alpha improvement;
- risk reduction;
- diversification;
- explainability;
- data-quality improvement;
- operator/governance visibility.

## 9. Research Experiment Design

Phase 2 experiments should use pre-registered designs:

1. State the Phase 2 Hypothesis.
2. Define the data dependency and RDP readiness state.
3. Define the eligible universe, as-of date contract, and PIT fields.
4. Freeze feature definitions before evaluation.
5. Define train, validation, test, and untouched holdout boundaries where
   applicable.
6. Define benchmark and sleeve-comparison set.
7. Define pass/fail and stop/defer criteria before running.
8. Generate deterministic artifacts.
9. Preserve research-only separation from paper/live execution.
10. Record all blocked external decisions explicitly.

Research-only work must remain separate from paper/live execution until it is
promoted through governance.

## 10. Guardrails Against Research Sprawl

Phase 2 must avoid uncontrolled data and sleeve expansion.

Guardrails:

- no new dataset without a named hypothesis;
- no new sleeve without a distinct return driver;
- no vendor source without cost, licensing, PIT, and maintenance review;
- no feature set without an expected contribution and measurement plan;
- no duplicated sleeve if overlap explains the same return stream;
- no retuning failed research unless a new hypothesis is approved;
- no production consumer migration based only on RDP parity;
- no promotion proposal without data readiness and evidence gates;
- no generic alternative-data work without a named dataset and legal review.

Argo may rank research priorities, but it may not silently promote, retire,
allocate, or reweight sleeves.

## 11. 3-Month Alpha Roadmap

| Month | Work | Target evidence |
|---|---|---|
| 1 | Cassiopeia SEC-event MVP scope and 13D/13D/A tape | PIT event tape, acceptance timestamps, campaign dedupe, initial event study |
| 1-2 | Value and Quality observe-only feature definitions | Canonical fundamental feature panels, overlap checks, initial IC/spread evidence |
| 2 | Argo evidence consumer v2 | Evidence scoring, blocker taxonomy, RDP readiness ingestion |
| 2-3 | SEC and macro hardening | Accession lineage, release-date policy, freshness checks |
| 3 | Phoenix decision checkpoint | Capacity redesign, short-interest source audit, or formal hold recommendation |

## 12. 6-Month Alpha Roadmap

| Horizon | Work | Decision target |
|---|---|---|
| 0-3 months | Public-source alpha evidence | Decide whether Cassiopeia, Value, or Quality deserve deeper shadow-readiness work |
| 3-6 months | Evidence maturation | Expand event tapes, harden fundamentals, improve overlap and capacity measurement |
| 3-6 months | Source decisions | Decide on analyst revisions, short interest, options, paid news, and sentiment scope |
| 3-6 months | Argo advisory layer | Produce a research-priority report that ranks evidence, blockers, and expected ROI |

## 13. 12-Month Alpha Roadmap

| Horizon | Work | Decision target |
|---|---|---|
| 6-9 months | Candidate consolidation | Promote none by default; identify candidates ready for formal shadow-readiness review |
| 6-9 months | Vendor-gated research if approved | Cygnus v1 and short-interest features only after source decisions |
| 9-12 months | Portfolio intelligence | Argo produces stable advisory reports without allocation authority |
| 9-12 months | Long-term bets | Options, news, sentiment, and alternative data proceed only if hypothesis and source gates pass |

## 14. Stop / Defer Criteria

Stop or defer work when:

- the work cannot cite a Phase 2 Hypothesis;
- the expected contribution is not measurable;
- the dataset is uncataloged or lacks PIT/freshness policy;
- a vendor, license, credential, or business decision is unresolved;
- the sleeve overlaps existing sleeves without a distinct value proposition;
- capacity or liquidity makes the sleeve non-viable at reference capital;
- validation requires weakening no-lookahead, freshness, or fail-closed rules;
- backtest performance is the only positive evidence;
- the work would touch paper/live execution before promotion governance.

## 15. Required Governance Updates

Future new sleeve, new dataset, or promotion proposals must include a Phase 2
Alpha Gate block with:

- Phase 2 Hypothesis;
- expected alpha/risk contribution;
- required evidence;
- RDP/data readiness status;
- promotion gate impact;
- dataset dependency;
- measurement plan;
- stop/defer condition.

Required references:

- Current research roadmap:
  `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
- Strategic backlog:
  `docs/governance/Strategy_Roadmap_And_Research_Backlog.md`
- Post-RDP assessment:
  `docs/governance/post_rdp_strategic_assessment.md`
- RDP architecture:
  `docs/architecture/research_data_platform.md`
- FR-DH catalog:
  `docs/governance/fr_active/data_hydration/fr_dh_013_canonical_research_data_catalog.md`

The governance hygiene agent should warn, not fail, when an explicit Phase 2
Alpha Gate block is missing required fields. Legacy documents are not required
to be retrofitted immediately.

## 16. How This Document Must Be Used

This document is the decision framework for future Caerus research work.

Use it when:

- proposing a new sleeve;
- adding a new dataset;
- adding a new feature family;
- requesting a vendor source;
- designing a promotion packet;
- extending Argo research-priority logic;
- deciding whether to stop or defer research.

Any new sleeve, new dataset, or promotion proposal must cite:

- the relevant hypothesis from this document;
- expected alpha/risk contribution;
- required evidence;
- RDP/data readiness status;
- promotion gate impact.

If a proposal cannot satisfy those fields, the default decision is defer.

Runtime impact: documentation-only governance strategy. No execution, broker,
scheduler, allocation, sleeve promotion, model behavior, dashboard behavior, or
production data-consumer behavior is changed.
