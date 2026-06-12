---
last_reviewed: 2026-05-21
owner: architecture
category: architecture
criticality: critical
canonical: true
related_systems: [research, governance, attribution, regime, shadow, alpha_stack]
---

# Caerus Research MCP — Canonical Institutional Architecture

> **Status banner (2026-05-29):** this document is the **aspirational
> design intent** as drafted on 2026-05-21, before implementation. For
> what the MCP **actually does today** (capability matrix, maturity
> level, gaps, next investments) see
> [`research_mcp_current_state_2026-05-29.md`](research_mcp_current_state_2026-05-29.md).
> For the running operator interface see
> [`../operator/research_mcp_operator_guide.md`](../operator/research_mcp_operator_guide.md).
> Where this document and the current-state assessment disagree on
> implemented surface, the current-state assessment wins.

**Version:** 1.0
**Date:** 2026-05-21
**Classification:** Institutional Research Infrastructure
**Constraint:** READ-ONLY — No execution, deployment, or mutation authority

---

## Table of Contents

1. Recommended Institutional MCP Architecture
2. Canonical Research Object Ontology
3. Object Relationship Graph Model
4. Provenance-Aware Metadata Architecture
5. Confidence Propagation Model
6. Governance Inheritance Model
7. Temporal Validity Semantics
8. Truth-Surface Compatibility Framework
9. Object Lifecycle Framework
10. Metadata Registry Architecture
11. Provenance Graph Architecture
12. Recommended Ingestion/Indexing Architecture
13. Recommended GCP VM Deployment Topology
14. Recommended Read-Only Security Model
15. Recommended API/Tool Structure
16. Layer Separation Architecture
17. Recommended Phased Rollout Plan
18. Institutional Architectural Risks
19. Long-Term Scalability Considerations
20. Recommendations for Future Evolution

---

## 1. Recommended Institutional MCP Architecture

### Design Philosophy

The Caerus Research MCP is not a filesystem browser with a chat interface. It is a **research cognition layer** that reasons over institutional research objects — their provenance, confidence, temporal validity, governance state, and lineage relationships — and exposes this reasoning through a structured tool interface conforming to the Model Context Protocol.

The MCP sits entirely within the **research plane**. It has no write access to any operational artifact, no broker credentials, no deployment authority, and no ability to trigger workflows. It is a read-only intelligence surface over the Caerus research corpus.

### Architectural Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    MCP COGNITION LAYER                          │
│                                                                 │
│  Research Object Reasoning · Provenance Queries · Confidence    │
│  Assessment · Governance State Queries · Temporal Validity      │
│  Checks · Lineage Traversal · Cross-Object Correlation         │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                    METADATA REGISTRY                            │
│                                                                 │
│  Object Index · Provenance Graph · Confidence Lattice ·        │
│  Governance State Cache · Temporal Validity Index ·             │
│  Truth-Surface Compatibility Matrix                             │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                    HYDRATION LAYER                              │
│                                                                 │
│  Object Hydrators · Schema Validators · Provenance Extractors  │
│  · Confidence Classifiers · Governance State Readers ·         │
│  Temporal Validity Parsers · Lineage Builders                  │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                    INGESTION / INDEXING LAYER                   │
│                                                                 │
│  Filesystem Watchers · JSON/CSV/Parquet Parsers · Metadata     │
│  Front-Matter Extractors · Schema Version Detectors ·          │
│  Staleness Detectors                                           │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                    STORAGE SUBSTRATE (READ-ONLY)                │
│                                                                 │
│  outputs/ · data/ · docs/ · regime/ · alpha_stack/ · logs/ ·   │
│  config/ · signals_store/ · .parquet files · .csv files ·      │
│  .json artifacts · .md governance docs                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Core Invariants

1. **Read-only**: The MCP never writes to the storage substrate. Its own metadata registry is a separate, disposable index that can be rebuilt from source at any time.
2. **Provenance-first**: Every answer the MCP provides is traceable to specific source artifacts with stated confidence and temporal validity.
3. **Governance-aware**: The MCP understands FR lifecycle states, deployment status, validation status, and promotion readiness. It will not present research artifacts as production-ready without checking governance state.
4. **Confidence-transparent**: The MCP propagates confidence levels through derived answers. If a query depends on a LOW-confidence input, the response is annotated accordingly.
5. **Temporally honest**: The MCP distinguishes between point-in-time valid data and stale data. It never silently serves stale artifacts as current.

---

## 2. Canonical Research Object Ontology

The ontology defines the institutional vocabulary. Every artifact the MCP reasons about maps to one of these object types. The filesystem is an implementation detail — the ontology is the institutional interface.

### Core Research Objects

```
Strategy
├── strategy_id: str                    # e.g., "caerus_polaris"
├── display_name: str                   # e.g., "Caerus Polaris"
├── promotion_state: PromotionState     # RESEARCH | BACKTEST | SHADOW | PAPER | LIVE
├── governance_classification: str      # Active control / shadow candidate / challenger
├── sleeve_composition: list[SleeveRef]
├── regime_sensitivity: RegimeSensitivity
└── lineage: LineageNode

NAVSurface
├── surface_id: str
├── nav_surface_type: enum              # LIVE_BROKER_PAPER_NAV | OPERATIONAL_SHADOW_NAV | RESEARCH_BACKTEST_NAV
├── confidence_level: ConfidenceLevel   # BROKER_AUTHORITATIVE | HIGH | PARTIAL_CONFIDENCE | LOW | UNAVAILABLE
├── execution_realism: str              # BROKER_PAPER_FILLS | MODEL_PORTFOLIO_NO_BROKER_FILLS | MODEL_CLOSE_WITH_SYNTHETIC_COSTS
├── point_in_time_validity: str
├── source_path: str
├── strategy_ref: StrategyRef
├── temporal_window: TemporalWindow
└── chain_status: str                   # OK | NO_PRIOR | BROKEN_CHAIN | REPAIRED

AttributionRun
├── run_id: str
├── trade_date: date
├── strategy_ref: StrategyRef
├── nav_surface_ref: NAVSurfaceRef
├── contribution_report: ArtifactRef
├── factor_exposure: ArtifactRef
├── regime_analysis: ArtifactRef
├── concentration_analysis: ArtifactRef
├── decision_attribution: ArtifactRef
├── confidence_level: ConfidenceLevel   # Inherited from NAVSurface
└── governance_state: GovernanceState

ExposureSnapshot
├── snapshot_id: str
├── trade_date: date
├── strategy_ref: StrategyRef
├── exposure_summary: ArtifactRef
├── factor_risk_flags: ArtifactRef
├── concentration_monitor: ArtifactRef
├── exposure_drift_summary: ArtifactRef
├── temporal_validity: TemporalValidity
└── confidence_level: ConfidenceLevel

RegimeAssessment
├── assessment_id: str
├── trade_date: date
├── dimensions: dict[str, RegimeState]  # trend, volatility, breadth, macro
├── regime_performance_breakdown: ArtifactRef
├── regime_fragility_report: ArtifactRef
├── regime_exposure_matrix: ArtifactRef
├── regime_transition_analysis: ArtifactRef
├── temporal_validity: TemporalValidity
└── confidence_level: ConfidenceLevel

AuditFinding
├── finding_id: str
├── audit_type: str                     # PERFORMANCE_VERACITY | EXECUTION | GOVERNANCE | DOCUMENTATION
├── severity: str                       # CRITICAL | HIGH | MEDIUM | LOW | INFORMATIONAL
├── finding_summary: str
├── affected_objects: list[ObjectRef]
├── remediation_state: str              # OPEN | IN_PROGRESS | RESOLVED | ACCEPTED_RISK
├── evidence_refs: list[ArtifactRef]
└── discovered_date: date

GovernanceFR
├── fr_id: str                          # e.g., "FR-024"
├── category: str                       # ARC | OPS | DOC | HOTFIX | FR
├── status: FRStatus                    # BACKLOG | READY | IN_PROGRESS | DONE | DEPLOYED | DEPLOYED_OBSERVING | REVIEWED_DEFERRED
├── blast_radius: str                   # LOW | MEDIUM | HIGH
├── observation_criteria: str
├── rollback_reference: str
├── validation_summary: str
├── affected_objects: list[ObjectRef]
├── deployed_date: date | None
└── lineage: LineageNode

PromotionAssessment
├── assessment_id: str
├── strategy_ref: StrategyRef
├── current_state: PromotionState
├── target_state: PromotionState
├── gate_results: list[GateResult]
├── blocking_findings: list[AuditFinding]
├── confidence_assessment: ConfidenceAssessment
├── governance_readiness: bool
└── assessed_date: date

PortfolioSnapshot
├── snapshot_id: str
├── trade_date: date
├── strategy_ref: StrategyRef
├── holdings: list[HoldingRecord]
├── weights: dict[str, float]
├── cash_weight: float
├── nav_surface_ref: NAVSurfaceRef
├── source_path: str
└── temporal_validity: TemporalValidity

ResearchHypothesis
├── hypothesis_id: str
├── hypothesis_name: str                # e.g., "H2 rank-decay exit"
├── parent_lab: str                     # e.g., "alpha_lab_v2"
├── status: str                         # PROPOSED | TESTING | VALIDATED | REJECTED | PROMOTED
├── validation_runs: list[ValidationRunRef]
├── stability_assessment: StabilityAssessment | None
├── promotion_target: StrategyRef | None
└── lineage: LineageNode

ValidationRun
├── run_id: str
├── hypothesis_ref: HypothesisRef
├── run_type: str                       # BACKTEST | SHADOW_EVALUATION | STABILITY_TEST | ROBUSTNESS_CHECK
├── date_range: TemporalWindow
├── metrics: dict[str, float]           # sharpe, max_drawdown, ic, turnover, etc.
├── nav_surface_ref: NAVSurfaceRef
├── confidence_level: ConfidenceLevel
└── artifacts: list[ArtifactRef]

StabilityAssessment
├── assessment_id: str
├── strategy_ref: StrategyRef
├── window: TemporalWindow
├── regime_dependency_flags: list[str]
├── fragility_classification: str       # STABLE | FRAGILE_REVIEW | FRAGILE_BLOCK
├── beta_amplification: dict
├── concentration_amplification: dict
└── confidence_level: ConfidenceLevel

FragilityAssessment
├── assessment_id: str
├── strategy_ref: StrategyRef
├── trade_date: date
├── classification: str                 # STABLE | FRAGILE_REVIEW | FRAGILE_BLOCK
├── flags: list[str]
├── regime_dependencies: list[str]
├── worst_regime: str
├── best_regime: str
└── confidence_level: ConfidenceLevel

ConfidenceAssessment
├── object_ref: ObjectRef
├── assessed_confidence: ConfidenceLevel
├── contributing_factors: list[ConfidenceFactor]
├── downgrade_reasons: list[str]
├── assessed_date: date
└── temporal_validity: TemporalValidity

ResearchArtifact
├── artifact_id: str
├── artifact_type: str                  # JSON | CSV | PARQUET | MARKDOWN | HTML | PNG
├── schema_version: str | None
├── source_path: str
├── produced_by: str                    # script/module that generated it
├── produced_at: datetime
├── trade_date: date | None
├── strategy_ref: StrategyRef | None
├── confidence_level: ConfidenceLevel
├── temporal_validity: TemporalValidity
├── governance_state: GovernanceState
└── lineage: LineageNode

LineageNode
├── node_id: str
├── object_ref: ObjectRef
├── parent_refs: list[ObjectRef]        # what this was derived from
├── child_refs: list[ObjectRef]         # what was derived from this
├── transformation: str                 # description of the derivation step
└── created_at: datetime

TemporalWindow
├── start_date: date
├── end_date: date
├── as_of: datetime                     # point-in-time snapshot moment
├── staleness_threshold: timedelta
└── is_stale: bool                      # computed: now - as_of > staleness_threshold
```

### Enumeration Types

```
ConfidenceLevel:
    BROKER_AUTHORITATIVE    # Direct from broker, highest trust
    HIGH                    # Validated through multiple independent checks
    PARTIAL_CONFIDENCE      # Known limitations documented
    LOW                     # Missing validation, broken chain, or known issues
    UNAVAILABLE             # Data source unreachable or absent

PromotionState:
    RESEARCH                # Hypothesis stage
    BACKTEST                # Historical validation
    SHADOW                  # Live shadow evaluation, no execution
    PAPER                   # Paper trading with real broker
    LIVE                    # Production (future state)

FRStatus:
    BACKLOG → READY → READY_VALIDATED → IN_PROGRESS → DONE → DEPLOYED
    Extended: DEPLOYED_OBSERVING | REVIEWED_DEFERRED

GovernanceState:
    UNGOVERNED              # No governance metadata present
    GOVERNED_DRAFT          # Under active governance development
    GOVERNED_DEPLOYED       # Deployed under governance controls
    GOVERNED_OBSERVING      # Deployed and under observation
    GOVERNED_DEFERRED       # Explicitly deferred with rationale
```

---

## 3. Object Relationship Graph Model

The research object graph defines how institutional objects relate. These relationships are first-class — the MCP reasons over them, not over directory structure.

```
Strategy ──owns──→ PortfolioSnapshot (many, dated)
Strategy ──owns──→ NAVSurface (many, typed)
Strategy ──owns──→ ExposureSnapshot (many, dated)
Strategy ──owns──→ AttributionRun (many, dated)
Strategy ──owns──→ FragilityAssessment (many, dated)
Strategy ──subject_of──→ PromotionAssessment (many)
Strategy ──subject_of──→ StabilityAssessment (many)
Strategy ──promoted_from──→ ResearchHypothesis (0..1)

ResearchHypothesis ──validated_by──→ ValidationRun (many)
ResearchHypothesis ──promoted_to──→ Strategy (0..1)

ValidationRun ──uses──→ NAVSurface (1)
ValidationRun ──produces──→ ResearchArtifact (many)

AttributionRun ──uses──→ NAVSurface (1)
AttributionRun ──produces──→ ResearchArtifact (many)
AttributionRun ──references──→ RegimeAssessment (1)

RegimeAssessment ──informs──→ AttributionRun (many)
RegimeAssessment ──informs──→ FragilityAssessment (many)
RegimeAssessment ──produces──→ ResearchArtifact (many)

NAVSurface ──governed_by──→ GovernanceFR (0..many)
NAVSurface ──audited_by──→ AuditFinding (0..many)

GovernanceFR ──affects──→ ResearchArtifact (many)
GovernanceFR ──gates──→ PromotionAssessment (0..many)

AuditFinding ──blocks──→ PromotionAssessment (0..many)
AuditFinding ──references──→ ResearchArtifact (many)

PortfolioSnapshot ──valued_by──→ NAVSurface (1)
PortfolioSnapshot ──measured_by──→ ExposureSnapshot (1)

ResearchArtifact ──derived_from──→ ResearchArtifact (many)    # lineage chain
ResearchArtifact ──governed_by──→ GovernanceFR (0..many)

ConfidenceAssessment ──assesses──→ any ObjectRef
```

### Graph Query Patterns

The MCP should support the following institutional queries natively:

- **Lineage traversal**: "What artifacts were used to produce this attribution run?"
- **Confidence propagation**: "What is the effective confidence of this promotion assessment, given all its dependencies?"
- **Governance coverage**: "Which research artifacts are not covered by any governance FR?"
- **Temporal validity**: "Which artifacts for strategy X are stale as of today?"
- **Truth-surface compatibility**: "Can I compare Orion's shadow NAV to Polaris's broker NAV?"
- **Fragility correlation**: "Across all strategies, which regime dimensions most frequently appear in fragility flags?"

---

## 4. Provenance-Aware Metadata Architecture

### Provenance Envelope

Every research object exposed by the MCP is wrapped in a provenance envelope:

```json
{
  "object_type": "AttributionRun",
  "object_id": "attr_caerus_polaris_2026-04-30",
  "provenance": {
    "source_paths": ["outputs/attribution/2026-04-30/attribution_summary.json"],
    "produced_by": "scripts/research/build_strategy_attribution.py",
    "produced_at": "2026-04-30T14:22:00Z",
    "schema_version": "caerus_attribution_v1",
    "nav_surface_type": "OPERATIONAL_SHADOW_NAV",
    "confidence_level": "LOW",
    "confidence_rationale": "Inherited from NAVSurface: chain_status=NO_PRIOR",
    "temporal_validity": {
      "trade_date": "2026-04-30",
      "as_of": "2026-04-30T14:22:00Z",
      "staleness_threshold_hours": 24,
      "is_stale": true
    },
    "governance_state": "GOVERNED_DEPLOYED",
    "governing_frs": ["FR-024", "FR-026"],
    "lineage_parents": [
      "navsurface_operational_shadow_2026-04-30",
      "regime_assessment_2026-04-30",
      "portfolio_snapshot_2026-04-30"
    ]
  },
  "data": { ... }
}
```

### Provenance Rules

1. **No provenance erasure**: The MCP never strips provenance metadata from responses. If provenance is unknown, it is marked `PROVENANCE_UNKNOWN`, never omitted.
2. **Confidence inheritance**: When an object depends on another, its confidence cannot exceed the minimum confidence of its dependencies (see Section 5).
3. **Lineage completeness**: Every object must trace to either a raw data source (prices, broker state) or a governance document. Orphan objects are flagged.
4. **Surface labeling**: Every numeric result (NAV, return, Sharpe, drawdown) must carry its `nav_surface_type` and `confidence_level`. Unlabeled numerics are prohibited.

---

## 5. Confidence Propagation Model

Confidence is not a free-text label. It is a lattice with strict propagation rules.

### Confidence Lattice (Ordered)

```
BROKER_AUTHORITATIVE > HIGH > PARTIAL_CONFIDENCE > LOW > UNAVAILABLE
```

### Propagation Rules

**Rule 1 — Floor Propagation**: When object A depends on objects B and C, the confidence of A is at most `min(confidence(B), confidence(C))`.

**Rule 2 — Downgrade Triggers**: Specific conditions force confidence downgrade regardless of input confidence:

| Condition | Downgrade To |
|---|---|
| Shadow chain status is NO_PRIOR, BROKEN_CHAIN, or NO_DATA | LOW |
| Broker snapshot missing or equity unavailable | UNAVAILABLE |
| Research backtest uses model close and synthetic costs | PARTIAL_CONFIDENCE |
| Artifact older than its staleness threshold | Downgrade by one level |
| Governance FR is DEPLOYED_OBSERVING (not yet DEPLOYED) | PARTIAL_CONFIDENCE |
| No governance FR covers the artifact | LOW |

**Rule 3 — No Silent Upgrades**: Confidence can only be upgraded by explicit reassessment with documented rationale. Confidence never silently increases.

**Rule 4 — Propagation Transparency**: When the MCP reports a confidence level, it also reports the propagation chain — which dependency was the limiting factor.

### Example Propagation

```
PromotionAssessment for Orion:
  ├── NAVSurface (OPERATIONAL_SHADOW_NAV) → confidence: LOW (chain_status=NO_PRIOR)
  ├── AttributionRun → confidence: LOW (inherited from NAVSurface)
  ├── FragilityAssessment → confidence: PARTIAL_CONFIDENCE (regime data is current)
  ├── StabilityAssessment → confidence: PARTIAL_CONFIDENCE
  └── Result: PromotionAssessment confidence = LOW
      Limiting factor: NAVSurface chain_status=NO_PRIOR
```

---

## 6. Governance Inheritance Model

Governance flows downward from GovernanceFR objects through the artifact tree.

### Inheritance Rules

1. **Direct Governance**: An artifact is directly governed when a GovernanceFR explicitly names it in its scope (e.g., FR-024 governs `nav_surface_registry.json`).

2. **Inherited Governance**: An artifact inherits governance from its parent if the parent is governed and the derivation is a deterministic transformation. Non-deterministic derivations (e.g., model training, random sampling) break inheritance.

3. **Governance Gaps**: Objects with no direct or inherited governance are classified `UNGOVERNED`. The MCP exposes governance gaps as first-class findings.

4. **Observation Inheritance**: If a GovernanceFR is in `DEPLOYED_OBSERVING` state, all artifacts it governs inherit a `GOVERNED_OBSERVING` state, which triggers a confidence downgrade to at most `PARTIAL_CONFIDENCE`.

5. **Deferred Governance**: If a GovernanceFR is `REVIEWED_DEFERRED`, its artifacts are classified `GOVERNED_DEFERRED`. The MCP preserves the deferral rationale and re-entry criteria.

### Governance Coverage Query

The MCP should be able to answer: "For strategy X on date Y, what percentage of the research artifact tree is governed?" This is a critical institutional readiness metric.

---

## 7. Temporal Validity Semantics

Financial research is point-in-time. The MCP must reason about time rigorously.

### Temporal Concepts

**trade_date**: The market date a research object pertains to. An attribution run for 2026-04-30 uses data from that trading day.

**as_of**: The timestamp when the artifact was produced. An attribution run might have `trade_date=2026-04-30` but `as_of=2026-05-01T02:00:00Z` if it was computed overnight.

**staleness_threshold**: The duration after which an artifact should be considered potentially stale. Default thresholds:

| Object Type | Default Staleness |
|---|---|
| PortfolioSnapshot | 24 hours |
| NAVSurface (broker) | 4 hours |
| NAVSurface (shadow) | 24 hours |
| RegimeAssessment | 24 hours |
| AttributionRun | 48 hours |
| GovernanceFR | Never stale (governance is persistent) |
| ResearchHypothesis | Never stale (state is explicit) |

**is_stale**: Computed property: `now() - as_of > staleness_threshold`. The MCP annotates stale objects but still serves them — with explicit staleness warnings.

### Temporal Compatibility Rules

1. **Same-date joining**: Objects may only be joined or compared if they share the same `trade_date` or their temporal windows overlap.
2. **Point-in-time fencing**: The MCP never uses future-dated information to answer questions about a past date. If asked "What was the regime on April 15?", it uses only artifacts with `as_of <= 2026-04-15T23:59:59`.
3. **Staleness annotation**: Every query response includes the `as_of` timestamp of the most recent artifact consumed. If any consumed artifact is stale, the response is annotated.

---

## 8. Truth-Surface Compatibility Framework

This framework prevents the most dangerous error in institutional research: silently blending incompatible NAV surfaces.

### Surface Types (from existing `surface_metadata.json`)

| Surface Type | Execution Realism | Typical Confidence |
|---|---|---|
| `LIVE_BROKER_PAPER_NAV` | Broker paper fills and account state | BROKER_AUTHORITATIVE |
| `OPERATIONAL_SHADOW_NAV` | Model portfolio, no broker fills | LOW to PARTIAL_CONFIDENCE |
| `RESEARCH_BACKTEST_NAV` | Model close with synthetic costs | PARTIAL_CONFIDENCE |

### Compatibility Matrix

```
                          LIVE_BROKER    OPERATIONAL_SHADOW    RESEARCH_BACKTEST
LIVE_BROKER               COMPATIBLE     INCOMPATIBLE          INCOMPATIBLE
OPERATIONAL_SHADOW         INCOMPATIBLE   COMPATIBLE            CAUTIOUS_OK
RESEARCH_BACKTEST          INCOMPATIBLE   CAUTIOUS_OK           COMPATIBLE
```

### Rules

1. **COMPATIBLE**: Objects from these surfaces may be freely combined, compared, and aggregated.
2. **INCOMPATIBLE**: Objects from these surfaces MUST NOT be combined, compared, or aggregated without explicit surface labeling and a compatibility override flag from the caller.
3. **CAUTIOUS_OK**: Objects may be compared for research purposes but the comparison must be annotated with a surface-type mismatch warning.

### MCP Enforcement

When the MCP receives a query that would combine incompatible surfaces, it refuses the combination and returns:
- The individual results per surface
- An explanation of why they cannot be combined
- The surface types and confidence levels of each

It never silently merges.

---

## 9. Object Lifecycle Framework

Research objects follow defined lifecycles. The MCP understands and enforces these.

### Strategy Lifecycle

```
RESEARCH → BACKTEST → SHADOW → PAPER → LIVE
    │          │         │        │
    └──────────┴─────────┴────────┴──→ RETIRED (terminal)
```

Transitions require a `PromotionAssessment` with all gates passing. The MCP can enumerate blocking gates.

### Artifact Lifecycle

```
PRODUCED → INDEXED → VALIDATED → CURRENT → STALE → SUPERSEDED → ARCHIVED
```

- **PRODUCED**: Written to disk by a pipeline step.
- **INDEXED**: Discovered and registered by the MCP ingestion layer.
- **VALIDATED**: Schema and provenance checks pass.
- **CURRENT**: Within staleness threshold and not superseded by a newer version.
- **STALE**: Past staleness threshold but no newer version exists.
- **SUPERSEDED**: A newer version of the same object type for the same trade_date exists.
- **ARCHIVED**: Moved to archive storage (still queryable via lineage).

### Governance FR Lifecycle

As defined in existing `governance_taxonomy.md`:

```
BACKLOG → READY → READY_VALIDATED → IN_PROGRESS → DONE → DEPLOYED
Extended: DEPLOYED_OBSERVING | REVIEWED_DEFERRED
```

The MCP reads and respects this lifecycle when computing governance coverage and confidence.

---

## 10. Metadata Registry Architecture

The metadata registry is the MCP's internal index. It is disposable (can be rebuilt from source at any time) and separate from the source artifacts.

### Registry Components

```
registry/
├── object_index.db           # SQLite: object_id, object_type, source_path, trade_date, confidence, governance_state
├── provenance_graph.db       # SQLite: parent_id, child_id, relationship_type, transformation
├── confidence_cache.json     # Pre-computed confidence propagation results
├── governance_coverage.json  # Pre-computed governance gap analysis
├── temporal_index.json       # Object lookup by trade_date and as_of
├── staleness_report.json     # Current staleness state of all objects
├── schema_versions.json      # Known schema versions per artifact type
└── registry_metadata.json    # Registry build timestamp, source hash, version
```

### Registry Properties

1. **Disposable**: The registry can be deleted and rebuilt from source artifacts at any time. It is a cache, not a source of truth.
2. **Deterministic**: Given the same source artifacts, the registry build is deterministic. Two builds from the same source state produce identical registries.
3. **Versioned**: The registry carries a build timestamp and a hash of the source state it was built from. The MCP checks this hash at startup and rebuilds if stale.
4. **Separate storage**: The registry lives in a dedicated directory (`~/.caerus_mcp/registry/` on the VM) outside the source artifact tree. It never contaminates source artifacts.

### Index Schema (SQLite)

```sql
CREATE TABLE objects (
    object_id TEXT PRIMARY KEY,
    object_type TEXT NOT NULL,
    strategy_id TEXT,
    trade_date TEXT,
    as_of TEXT,
    confidence_level TEXT,
    governance_state TEXT,
    source_path TEXT,
    schema_version TEXT,
    is_stale INTEGER DEFAULT 0,
    is_superseded INTEGER DEFAULT 0,
    indexed_at TEXT
);

CREATE TABLE lineage (
    parent_id TEXT,
    child_id TEXT,
    relationship_type TEXT,
    transformation TEXT,
    PRIMARY KEY (parent_id, child_id, relationship_type),
    FOREIGN KEY (parent_id) REFERENCES objects(object_id),
    FOREIGN KEY (child_id) REFERENCES objects(object_id)
);

CREATE TABLE governance_coverage (
    object_id TEXT,
    fr_id TEXT,
    coverage_type TEXT,  -- DIRECT | INHERITED | UNGOVERNED
    PRIMARY KEY (object_id, fr_id),
    FOREIGN KEY (object_id) REFERENCES objects(object_id)
);

CREATE INDEX idx_objects_type_date ON objects(object_type, trade_date);
CREATE INDEX idx_objects_strategy ON objects(strategy_id, trade_date);
CREATE INDEX idx_objects_confidence ON objects(confidence_level);
CREATE INDEX idx_lineage_parent ON lineage(parent_id);
CREATE INDEX idx_lineage_child ON lineage(child_id);
```

---

## 11. Provenance Graph Architecture

The provenance graph is the institutional memory of derivation. It answers "how did we arrive at this conclusion?"

### Graph Structure

The provenance graph is a directed acyclic graph (DAG) where:
- **Nodes** are research objects (identified by `object_id`)
- **Edges** are derivation relationships (parent → child) with a `transformation` label

### Edge Types

| Edge Type | Meaning |
|---|---|
| `DERIVED_FROM` | Child was computed from parent (e.g., attribution from NAV surface) |
| `GOVERNED_BY` | Object is governed by a GovernanceFR |
| `VALIDATED_BY` | Object was validated by a ValidationRun |
| `AUDITED_BY` | Object was audited, producing an AuditFinding |
| `SUPERSEDES` | Newer object replaces older for same type/date |
| `PROMOTED_FROM` | Strategy was promoted from a ResearchHypothesis |

### Graph Queries

The MCP must support:

1. **Upstream traversal**: Given an object, return all ancestors up to raw data sources.
2. **Downstream traversal**: Given an object, return all derived objects.
3. **Impact analysis**: "If this NAV surface is invalidated, what other objects are affected?"
4. **Root cause**: "This attribution run shows LOW confidence — trace back to the root cause."
5. **Completeness check**: "For this trade date, are all expected objects present and linked?"

### Storage

The provenance graph is stored in the `lineage` table of the SQLite registry. For complex traversals, the MCP loads the subgraph into memory as a `networkx.DiGraph` (or equivalent lightweight graph library).

---

## 12. Recommended Ingestion/Indexing Architecture

The ingestion layer translates the filesystem into the research object ontology.

### Ingestion Pipeline

```
Phase 1: DISCOVERY
  ├── Walk outputs/ directory tree
  ├── Walk docs/governance/ for GovernanceFR objects
  ├── Walk docs/architecture/ for architectural lineage
  ├── Detect file types: .json, .csv, .parquet, .md
  └── Record file paths, modification times, sizes

Phase 2: PARSING
  ├── JSON: Extract schema_version, trade_date, strategy references
  ├── CSV/Parquet: Extract column schemas, row counts, date ranges
  ├── Markdown: Extract YAML front matter (metadata standard)
  └── FR Registry: Parse structured FR table from fr_registry.md

Phase 3: CLASSIFICATION
  ├── Map file paths to object types using path-to-ontology rules
  ├── Detect schema versions for version-aware parsing
  ├── Classify confidence levels from embedded metadata
  └── Detect governance state from FR registry cross-reference

Phase 4: HYDRATION
  ├── Construct full research objects with all metadata fields
  ├── Build lineage edges from known derivation patterns
  ├── Compute temporal validity (staleness)
  └── Propagate confidence through dependency chains

Phase 5: REGISTRATION
  ├── Upsert objects into SQLite registry
  ├── Upsert lineage edges
  ├── Compute governance coverage
  ├── Generate staleness report
  └── Write registry_metadata.json with build hash and timestamp
```

### Path-to-Ontology Mapping Rules

```python
ONTOLOGY_MAP = {
    "outputs/attribution/{date}/attribution_summary.json": "AttributionRun",
    "outputs/attribution/{date}/nav_surface_registry.json": "NAVSurface",
    "outputs/attribution/{date}/surface_metadata.json": "NAVSurface",
    "outputs/attribution/{date}/factor_exposure.json": "ExposureSnapshot",
    "outputs/attribution/{date}/regime_fragility_report.json": "FragilityAssessment",
    "outputs/attribution/{date}/regime_performance_breakdown.json": "RegimeAssessment",
    "outputs/attribution/{date}/exposure_summary.json": "ExposureSnapshot",
    "outputs/attribution/{date}/concentration_monitor.json": "ExposureSnapshot",
    "outputs/shadow_candidates/{date}/{strategy}.json": "PortfolioSnapshot",
    "outputs/shadow_candidates/{date}/shadow_performance.json": "NAVSurface",
    "outputs/shadow_candidates/{date}/shadow_evaluation.json": "ValidationRun",
    "outputs/shadow_candidates/performance/shadow_nav_series.csv": "NAVSurface",
    "outputs/portfolio_history/{date}/*": "PortfolioSnapshot",
    "outputs/workflow/{date}/*.json": "ResearchArtifact",
    "outputs/regime_validation/*": "RegimeAssessment",
    "outputs/broker/broker_snapshot_latest.json": "NAVSurface",
    "docs/governance/fr_registry.md": "GovernanceFR",
    "docs/governance/governance_taxonomy.md": "GovernanceFR",
    "research/alpha_lab_v1/*": "ResearchHypothesis",
    "research/alpha_lab_v2/*": "ResearchHypothesis",
}
```

This mapping is configuration, not hardcoded logic. New artifact types can be added without code changes.

### Incremental Indexing

The ingestion pipeline supports incremental mode:
1. Compare file modification times against the registry's `indexed_at` timestamps.
2. Re-index only files that changed since last build.
3. Recompute confidence propagation and governance coverage for affected subgraphs.

Full rebuild is always available via `--full-rebuild` flag.

---

## 13. Recommended GCP VM Deployment Topology

### Current VM State

The existing GCP VM is `alpha-stack-scheduler` in project `alpha-stack-490922`
and zone `us-central1-a`. Access it with:

```bash
gcloud compute ssh brettolson@alpha-stack-scheduler --zone us-central1-a
```

Static external IPs are non-authoritative; resolve the current address only
when a direct SSH path is unavoidable.

The VM runs:
- Cron-scheduled trading pipeline (Phases 0–3)
- Shadow evaluation
- Dashboard generation
- Web server (landing, dashboard, golf bot)

### MCP Deployment Architecture

```
┌──────────────────────────────────────────────────────────┐
│                   GCP VM (e2-standard-4)                  │
│                                                          │
│  ┌─────────────────────┐   ┌──────────────────────────┐  │
│  │  EXISTING RUNTIME   │   │   MCP RESEARCH SERVICE   │  │
│  │                     │   │                          │  │
│  │  cron pipeline      │   │  mcp_server.py           │  │
│  │  shadow evaluation  │   │  ├── stdio transport     │  │
│  │  dashboard gen      │   │  ├── tool handlers       │  │
│  │  web server         │   │  ├── registry builder     │  │
│  │                     │   │  └── read-only FS access  │  │
│  │  writes to:         │   │                          │  │
│  │  outputs/           │   │  reads from:             │  │
│  │  data/              │   │  outputs/ (read-only)    │  │
│  │  logs/              │   │  data/ (read-only)       │  │
│  │                     │   │  docs/ (read-only)       │  │
│  │                     │   │  config/ (read-only)     │  │
│  │                     │   │                          │  │
│  │                     │   │  writes to:              │  │
│  │                     │   │  ~/.caerus_mcp/registry/ │  │
│  └─────────────────────┘   └──────────────────────────┘  │
│                                                          │
│  Shared filesystem (read-only for MCP):                  │
│  ~/quant-daily-report/outputs/                           │
│  ~/quant-daily-report/data/                              │
│  ~/quant-daily-report/docs/                              │
│  ~/quant-daily-report/config/                            │
│  ~/quant-daily-report/research/                          │
│  ~/quant-daily-report/regime/                            │
│  ~/quant-daily-report/alpha_stack/                       │
└──────────────────────────────────────────────────────────┘
```

### Resource Requirements

- **CPU**: Minimal — the MCP is I/O bound (reading JSON/CSV/Parquet), not compute bound
- **Memory**: ~512MB for the registry, provenance graph, and in-memory caches
- **Disk**: ~50MB for the SQLite registry
- **Network**: The MCP server does not listen on any network port. It communicates via stdio transport to a local Claude Code/MCP client, or via a local Unix socket if a thin HTTP adapter is needed.

### Process Isolation

- The MCP runs as a separate process from the trading pipeline.
- It has no environment variables for broker credentials (APCA_API_KEY, etc.).
- It has no write access to the outputs/ directory (enforced via Unix user permissions or read-only bind mount).
- It cannot send signals to or interact with the cron pipeline processes.

---

## 14. Recommended Read-Only Security Model

### Principle: Least Privilege, Research Plane Only

The MCP operates under a dedicated Unix user (`caerus_mcp`) with:

```bash
# Filesystem permissions
chmod -R o+r ~/quant-daily-report/outputs/
chmod -R o+r ~/quant-daily-report/data/
chmod -R o+r ~/quant-daily-report/docs/
chmod -R o+r ~/quant-daily-report/config/
chmod -R o+r ~/quant-daily-report/research/
chmod -R o+r ~/quant-daily-report/regime/
chmod -R o+r ~/quant-daily-report/alpha_stack/

# MCP's own writable area (registry only)
mkdir -p /home/caerus_mcp/.caerus_mcp/registry/
chown caerus_mcp:caerus_mcp /home/caerus_mcp/.caerus_mcp/registry/
```

### Security Boundaries

| Capability | Allowed | Denied |
|---|---|---|
| Read research artifacts | Yes | — |
| Read governance docs | Yes | — |
| Read config files | Yes | — |
| Write to registry cache | Yes | — |
| Write to outputs/ | — | **Denied** |
| Read broker credentials | — | **Denied** |
| Read .env files | — | **Denied** |
| Execute scripts | — | **Denied** |
| Send network requests | — | **Denied** |
| Access cron configuration | — | **Denied** |
| Modify any source file | — | **Denied** |

### Environment Variable Isolation

The MCP process must NOT have access to:
- `APCA_API_KEY`, `APCA_SECRET_KEY` (broker credentials)
- `SMTP_*` (email credentials)
- Any deployment or CI/CD tokens

The MCP process is started with a sanitized environment:

```bash
env -i HOME=/home/caerus_mcp \
       PATH=/usr/local/bin:/usr/bin \
       CAERUS_MCP_RESEARCH_ROOT=/home/brettolson/quant-daily-report \
       CAERUS_MCP_REGISTRY_PATH=/home/caerus_mcp/.caerus_mcp/registry \
  python3 -m caerus_mcp.server
```

---

## 15. Recommended API/Tool Structure

The MCP exposes capabilities as MCP tools. Each tool is a discrete, well-scoped research query.

### Tool Categories

**Object Retrieval Tools**

| Tool | Description |
|---|---|
| `get_strategy_overview` | Return strategy metadata, promotion state, current confidence, governance coverage |
| `get_nav_surface` | Return NAV surface with full provenance envelope for a strategy and date |
| `get_attribution_run` | Return attribution analysis with confidence and lineage |
| `get_exposure_snapshot` | Return current exposure, concentration, factor risk flags |
| `get_regime_assessment` | Return current regime state across all dimensions with fragility |
| `get_portfolio_snapshot` | Return holdings and weights for a strategy and date |
| `get_research_hypothesis` | Return hypothesis status, validation runs, promotion path |

**Analytical Query Tools**

| Tool | Description |
|---|---|
| `compare_strategies` | Compare two strategies with truth-surface compatibility checks |
| `trace_lineage` | Upstream or downstream lineage traversal from any object |
| `assess_promotion_readiness` | Evaluate whether a strategy meets all promotion gates |
| `compute_governance_coverage` | Return governance gap analysis for a strategy or date range |
| `identify_stale_artifacts` | Return all artifacts past their staleness threshold |
| `propagate_confidence` | Show the confidence propagation chain for any object |

**Governance Query Tools**

| Tool | Description |
|---|---|
| `get_fr_status` | Return current FR lifecycle state with observation criteria |
| `get_fr_registry` | Return the full FR registry with deployment history |
| `get_governance_gaps` | Return ungoverned artifacts ordered by criticality |
| `get_audit_findings` | Return open audit findings with affected objects |

**Temporal Query Tools**

| Tool | Description |
|---|---|
| `get_research_state_at` | Return the full research state for a strategy at a point in time |
| `get_regime_history` | Return regime states over a date range with transition analysis |
| `get_nav_series` | Return NAV time series with per-point confidence annotations |

**Meta Tools**

| Tool | Description |
|---|---|
| `get_registry_health` | Return registry build status, staleness, completeness |
| `rebuild_registry` | Trigger a full registry rebuild from source artifacts |
| `list_object_types` | Return the ontology with descriptions and counts |
| `get_schema_versions` | Return known schema versions per artifact type |

### Tool Response Format

Every tool response follows the provenance envelope pattern from Section 4. No tool ever returns raw data without provenance, confidence, and temporal validity metadata.

---

## 16. Layer Separation Architecture

### Five-Layer Model

```
┌─────────────────────────────────────────────┐
│           COGNITION LAYER                    │  Reasoning, query planning,
│                                             │  cross-object correlation,
│  Understands ontology and relationships.     │  confidence assessment,
│  Plans multi-step queries.                   │  natural language interpretation
│  Synthesizes cross-object answers.           │
│  Enforces truth-surface compatibility.       │
├─────────────────────────────────────────────┤
│           GOVERNANCE LAYER                   │  FR state, promotion gates,
│                                             │  audit findings, governance
│  Evaluates governance coverage.              │  coverage, observation state,
│  Checks promotion readiness.                 │  rollback references
│  Tracks observation state.                   │
│  Validates FR lifecycle transitions.         │
├─────────────────────────────────────────────┤
│           RETRIEVAL LAYER                    │  Object hydration, provenance
│                                             │  extraction, confidence
│  Hydrates research objects from registry.    │  propagation, lineage
│  Applies provenance envelopes.               │  traversal, temporal
│  Propagates confidence.                      │  validation
│  Traverses lineage graph.                    │
├─────────────────────────────────────────────┤
│           STORAGE LAYER                      │  SQLite registry, JSON/CSV/
│                                             │  Parquet parsers, file watchers,
│  Manages the metadata registry.              │  incremental indexing, schema
│  Handles ingestion and indexing.             │  detection
│  Provides indexed lookups.                   │
│  Detects staleness.                          │
├─────────────────────────────────────────────┤
│           INFRASTRUCTURE LAYER               │  Filesystem access, process
│                                             │  isolation, permission model,
│  Read-only filesystem access.                │  deployment, logging,
│  Process isolation and security.             │  monitoring
│  Logging and monitoring.                     │
│  Registry lifecycle management.              │
└─────────────────────────────────────────────┘
```

### Layer Interaction Rules

1. **Cognition → Retrieval**: The cognition layer never touches the filesystem directly. It always queries through the retrieval layer, which returns provenance-enveloped objects.
2. **Retrieval → Storage**: The retrieval layer reads from the SQLite registry and raw files. It never writes to source artifacts.
3. **Governance → Retrieval**: The governance layer queries the retrieval layer for FR state, governance coverage, and audit findings. It does not maintain its own state.
4. **Infrastructure → All**: The infrastructure layer provides filesystem access, process isolation, and logging to all layers above.
5. **No layer skipping**: The cognition layer cannot bypass retrieval to read raw files. The retrieval layer cannot bypass storage to write to the registry without going through the ingestion pipeline.

### Python Module Structure

```
caerus_mcp/
├── __init__.py
├── server.py                  # MCP server entry point, tool registration
├── cognition/
│   ├── __init__.py
│   ├── query_planner.py       # Multi-step query decomposition
│   ├── strategy_reasoning.py  # Strategy-level analytical queries
│   ├── comparison_engine.py   # Cross-strategy/cross-surface comparison
│   └── synthesis.py           # Cross-object answer synthesis
├── governance/
│   ├── __init__.py
│   ├── fr_reader.py           # FR registry parser and state machine
│   ├── promotion_gates.py     # Promotion readiness evaluation
│   ├── coverage_analyzer.py   # Governance gap detection
│   └── audit_reader.py        # Audit finding aggregation
├── retrieval/
│   ├── __init__.py
│   ├── object_hydrator.py     # Research object construction from raw data
│   ├── provenance_builder.py  # Provenance envelope construction
│   ├── confidence_engine.py   # Confidence propagation engine
│   ├── lineage_traverser.py   # Graph traversal for lineage queries
│   └── temporal_validator.py  # Staleness and temporal validity checks
├── storage/
│   ├── __init__.py
│   ├── registry.py            # SQLite registry CRUD
│   ├── ingestion.py           # Filesystem walking and parsing
│   ├── indexer.py             # Object classification and registration
│   ├── parsers/
│   │   ├── json_parser.py
│   │   ├── csv_parser.py
│   │   ├── parquet_parser.py
│   │   └── markdown_parser.py
│   └── schema_detector.py     # Schema version detection
├── infrastructure/
│   ├── __init__.py
│   ├── fs_access.py           # Read-only filesystem abstraction
│   ├── config.py              # Configuration loading
│   └── logging.py             # Structured logging
├── ontology/
│   ├── __init__.py
│   ├── types.py               # Dataclass definitions for all object types
│   ├── enums.py               # ConfidenceLevel, PromotionState, etc.
│   ├── relationships.py       # Edge type definitions
│   └── path_mapping.py        # Path-to-ontology configuration
└── tools/
    ├── __init__.py
    ├── object_tools.py        # get_strategy_overview, get_nav_surface, etc.
    ├── analytical_tools.py    # compare_strategies, trace_lineage, etc.
    ├── governance_tools.py    # get_fr_status, get_governance_gaps, etc.
    ├── temporal_tools.py      # get_research_state_at, get_regime_history, etc.
    └── meta_tools.py          # get_registry_health, rebuild_registry, etc.
```

---

## 17. Recommended Phased Rollout Plan

### Phase 0: Foundation (Weeks 1–2)

**Objective**: Establish the infrastructure layer and basic registry.

Deliverables:
- `caerus_mcp/` Python package skeleton with layer separation
- `ontology/types.py` with all dataclass definitions
- `storage/registry.py` with SQLite schema
- `storage/ingestion.py` with filesystem discovery and JSON/CSV/Markdown parsing
- `infrastructure/fs_access.py` with read-only filesystem abstraction
- `infrastructure/config.py` with path mappings
- Basic CLI: `python -m caerus_mcp.build_registry` to build the registry from source
- Unit tests for ontology types and registry CRUD

**Validation gate**: Registry builds successfully from current `outputs/` tree. Object counts match expected artifact counts.

### Phase 1: Object Retrieval (Weeks 3–4)

**Objective**: Hydrate research objects with provenance envelopes.

Deliverables:
- `retrieval/object_hydrator.py` for all core object types (Strategy, NAVSurface, AttributionRun, ExposureSnapshot, RegimeAssessment, PortfolioSnapshot)
- `retrieval/provenance_builder.py` with provenance envelope construction
- `retrieval/temporal_validator.py` with staleness detection
- MCP server skeleton with stdio transport
- First tools: `get_strategy_overview`, `get_nav_surface`, `get_attribution_run`, `get_exposure_snapshot`, `get_regime_assessment`

**Validation gate**: Tools return correct provenance envelopes for known artifacts. Staleness detection matches manual inspection.

### Phase 2: Confidence and Lineage (Weeks 5–6)

**Objective**: Implement confidence propagation and lineage graph.

Deliverables:
- `retrieval/confidence_engine.py` with lattice propagation
- `retrieval/lineage_traverser.py` with upstream/downstream traversal
- `storage/ingestion.py` extended with lineage edge construction
- Tools: `trace_lineage`, `propagate_confidence`, `identify_stale_artifacts`

**Validation gate**: Confidence propagation correctly identifies LOW-confidence attribution runs due to broken NAV chains. Lineage traversal from an attribution run reaches the source NAV surface and regime assessment.

### Phase 3: Governance Integration (Weeks 7–8)

**Objective**: Read FR registry and compute governance coverage.

Deliverables:
- `governance/fr_reader.py` parsing `fr_registry.md` and `governance_taxonomy.md`
- `governance/coverage_analyzer.py` computing governance gaps
- `governance/promotion_gates.py` evaluating promotion readiness
- Tools: `get_fr_status`, `get_fr_registry`, `get_governance_gaps`, `assess_promotion_readiness`, `compute_governance_coverage`

**Validation gate**: Governance coverage percentages match manual audit. Promotion readiness correctly blocks on known issues (e.g., Orion's NO_PRIOR chain status).

### Phase 4: Analytical Layer (Weeks 9–10)

**Objective**: Enable cross-object reasoning and comparison.

Deliverables:
- `cognition/comparison_engine.py` with truth-surface compatibility enforcement
- `cognition/strategy_reasoning.py` for multi-object strategy queries
- Tools: `compare_strategies`, `get_research_state_at`, `get_regime_history`, `get_nav_series`
- Parquet parser for research backtest data

**Validation gate**: Comparing Polaris (broker NAV) with Orion (shadow NAV) correctly returns INCOMPATIBLE with explanation. Point-in-time queries return correct state.

### Phase 5: Production Hardening (Weeks 11–12)

**Objective**: Security hardening, incremental indexing, monitoring.

Deliverables:
- Dedicated `caerus_mcp` Unix user with read-only filesystem access
- Sanitized environment variable startup
- Incremental registry indexing
- Structured logging
- Registry health monitoring
- `rebuild_registry` tool
- Integration tests against the full current artifact tree

**Validation gate**: MCP cannot write to `outputs/`. MCP cannot read `.env`. Incremental indexing correctly detects and re-indexes changed files.

---

## 18. Institutional Architectural Risks

### Risk 1: Schema Drift
**Description**: Research artifact JSON schemas evolve over time (e.g., `caerus_surface_metadata_v1` may become `v2`). If the MCP's parsers are not version-aware, it will silently misinterpret newer artifacts.
**Mitigation**: Schema version detection is a first-class concern in the ingestion layer. Every parser supports version-dispatch. Unknown schema versions are flagged rather than silently parsed.

### Risk 2: Ontology Coupling to Filesystem Layout
**Description**: The path-to-ontology mapping creates implicit coupling. If the pipeline team reorganizes `outputs/`, the MCP breaks.
**Mitigation**: The path mapping is externalized configuration, not hardcoded. A single config file update restores mapping. The MCP logs unmapped paths for review.

### Risk 3: Stale Registry Serving Stale Answers
**Description**: If the registry is not rebuilt after new artifacts are produced, the MCP serves outdated information.
**Mitigation**: The registry carries a build hash. The MCP checks source file modification times against the registry at query time and warns when the registry may be stale. Incremental indexing (Phase 5) minimizes rebuild cost.

### Risk 4: Confidence Floor Masking Real Quality
**Description**: The floor-propagation rule (min of dependencies) may systematically underrate objects whose LOW-confidence dependency is non-material.
**Mitigation**: Confidence propagation always reports the limiting factor. Consumers can override with explicit justification. The MCP never silently upgrades confidence but provides the reasoning chain for human judgment.

### Risk 5: Governance Registry Parsing Fragility
**Description**: The FR registry is a Markdown table parsed as structured data. Markdown formatting changes can break parsing.
**Mitigation**: The FR registry parser uses fuzzy table extraction with fallback to regex patterns. Parsing failures are logged and the MCP degrades gracefully (governance coverage reported as UNKNOWN rather than crashing).

### Risk 6: Scope Creep Toward Execution
**Description**: Once the MCP exists and proves useful, there will be pressure to add write capabilities ("just let it trigger a shadow run", "just let it update a config").
**Mitigation**: The read-only constraint is architectural (Unix permissions, no write handles, no broker credentials), not just policy. Adding write capability requires a new deployment with different permissions — it cannot be done with a config flag.

---

## 19. Long-Term Scalability Considerations

### Data Volume Growth

Current artifact volume (~hundreds of JSON files per day) is trivially handled by SQLite. At projected growth rates:

| Horizon | Daily Artifacts | Registry Size | Rebuild Time |
|---|---|---|---|
| Current | ~100 | ~10MB | <5 seconds |
| 1 year | ~200 | ~100MB | <30 seconds |
| 3 years | ~500 | ~500MB | <2 minutes |
| 5 years | ~1000 | ~2GB | ~5 minutes |

SQLite handles databases up to ~1TB reliably. The registry will not be the bottleneck within any reasonable planning horizon.

### Multi-Strategy Scaling

As the number of named strategies grows (currently 3: Polaris, Orion, Lyra), the ontology and registry scale linearly. The graph structure handles hundreds of strategies without architectural changes.

### Historical Depth

The primary scaling concern is historical depth — retaining and indexing years of daily artifacts. The recommended approach:

1. **Active window**: Full indexing for the most recent 90 trading days.
2. **Archive window**: Summary-level indexing (object_id, type, date, confidence) for older artifacts. Full hydration on demand.
3. **Cold storage**: Artifacts older than 2 years can be moved to GCS (Google Cloud Storage) with the MCP retaining lineage references. Hydration triggers a fetch from GCS.

### Multi-VM Deployment (Future)

If Caerus scales to multiple VMs (e.g., separate research compute), the MCP can be extended with:
- A shared registry in Cloud SQL (PostgreSQL) instead of local SQLite
- Read-only NFS or GCS FUSE mounts for artifact access across VMs
- Registry synchronization protocol between MCP instances

This is not needed now but the architecture does not preclude it.

---

## 20. Recommendations for Future Evolution

### 20.1 Longitudinal Intelligence

**Goal**: The MCP becomes capable of answering questions across time, not just at a point in time.

Capabilities:
- "How has Orion's fragility classification changed over the past 30 days?"
- "What is the trend in governance coverage over time?"
- "Which regime transitions have historically preceded drawdowns?"

**Implementation**: Longitudinal views are materialized by the registry builder as time-series indexes over object fields. No architectural change needed — the registry schema already supports multi-date queries.

### 20.2 Fragility Synthesis

**Goal**: The MCP synthesizes fragility signals across strategies, regimes, and time to provide institutional-level fragility intelligence.

Capabilities:
- "Across all strategies, what is the systemic exposure to a vol regime shift?"
- "If the current regime transitions from risk_off to risk_on, which strategies are most exposed?"
- "What is the portfolio-level concentration risk across all active strategies?"

**Implementation**: Add a `cognition/fragility_synthesizer.py` module that aggregates FragilityAssessment objects across strategies and constructs portfolio-level fragility views. Requires the analytical layer from Phase 4.

### 20.3 Governance Intelligence

**Goal**: The MCP proactively identifies governance risks rather than passively reporting state.

Capabilities:
- "Which deployed FRs have been in DEPLOYED_OBSERVING for more than 14 days without evidence?"
- "Which deferred FRs have met their re-entry criteria based on current system state?"
- "What governance actions should be prioritized this week?"

**Implementation**: Add `governance/intelligence.py` with rule-based governance risk detection. Rules are expressed declaratively and evaluated against the registry.

### 20.4 Learning Systems

**Goal**: The MCP captures institutional learning — what worked, what failed, what was revised — as persistent, queryable memory.

Capabilities:
- "What hypotheses were rejected in Alpha Lab v1 and v2, and why?"
- "What is the cumulative track record of our promotion assessments?"
- "Which regime classifiers have been revised, and what motivated the revision?"

**Implementation**: Extend the ontology with `InstitutionalLesson` objects derived from hypothesis rejections, FR rollbacks, audit findings, and promotion decision records. These form a queryable institutional memory that improves decision quality over time.

### 20.5 Institutional Memory

**Goal**: The MCP maintains a persistent, evolving understanding of the Caerus research program that transcends any individual session or analysis.

This is the long-term aspiration: the MCP becomes the institutional memory of the fund's research process. Every hypothesis tested, every regime encountered, every governance decision made, every promotion assessed — all queryable, all traceable, all confidence-rated.

**Implementation path**:
1. Foundation (Phases 0–5 of this document): Establish the read-only research intelligence surface.
2. Longitudinal + Fragility Synthesis: Enable cross-time and cross-strategy reasoning.
3. Governance Intelligence: Proactive governance risk detection.
4. Learning Systems: Capture institutional lessons as first-class objects.
5. Institutional Memory: The MCP reasons not just over current state but over the fund's research history as a coherent narrative.

Each step is additive. No step requires modifying the read-only constraint.

---

## Appendix A: Glossary

| Term | Definition |
|---|---|
| **Research Object** | A typed, provenance-bearing institutional entity in the Caerus ontology |
| **Provenance Envelope** | Metadata wrapper providing source, confidence, temporal validity, and governance state |
| **Truth Surface** | A specific NAV calculation methodology with defined execution realism and confidence |
| **Confidence Lattice** | Ordered set of confidence levels with floor-propagation semantics |
| **Governance Coverage** | The percentage of research objects governed by at least one GovernanceFR |
| **Staleness** | Condition where an artifact's age exceeds its staleness threshold |
| **Lineage** | The derivation chain tracing a research object back to its source data |
| **FR** | Friday Refactor — a governed maintenance/evolution work item |
| **Hydration** | The process of constructing a full research object from raw filesystem artifacts |

## Appendix B: Relationship to Existing Caerus Governance

This architecture integrates with, does not replace, existing governance:

| Existing System | MCP Relationship |
|---|---|
| FR Registry (`fr_registry.md`) | Read and indexed as GovernanceFR objects |
| Governance Taxonomy (`governance_taxonomy.md`) | Read for category and lifecycle definitions |
| Change Lineage Standard (`change_lineage_standard.md`) | Lineage fields inform the provenance graph |
| Metadata Standard (`metadata_standard.md`) | Front matter is parsed during ingestion |
| Research Integrity Hardening Plan | FR-024 through FR-029 artifacts are indexed |
| Shadow Evaluation System | Shadow artifacts are indexed as NAVSurface and ValidationRun objects |
| Attribution Framework | Attribution outputs are indexed as AttributionRun objects |

---

*Architecture document version 1.0 — May 21, 2026*
*Author: Architecture / Research Infrastructure*
*Classification: Institutional — Internal Use Only*
