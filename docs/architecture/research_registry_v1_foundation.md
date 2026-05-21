---
last_reviewed: 2026-05-21
owner: architecture
category: architecture
criticality: high
canonical: false
related_systems: [research_registry, research, governance, attribution, shadow]
---

# Caerus Research Object Registry v1 Foundation

This implementation note describes the initial deterministic registry
foundation. The frozen semantic contracts in `docs/architecture/semantics/`
remain authoritative; this document is only an implementation map.

## Scope

Implemented scope:

- Canonical research object payload schemas.
- SEM-001 metadata envelope construction and validation.
- SQLite registry storage for envelope-bearing research objects.
- `networkx.DiGraph` provenance DAG with cycle refusal.
- SEM-007 confidence lattice and downgrade propagation.
- SEM-004 governance inheritance rules.
- SEM-002 truth-surface compatibility matrix.
- SEM-006 temporal fencing.
- Deterministic rebuild parity checks.
- Artifact-family hydration for grandfathered governance, audit,
  attribution, shadow evaluation, regime intelligence, performance
  veracity, exposure intelligence, and validation artifacts.
- Replay validation and deterministic parity validation helpers.
- Read-only registry observability and VM shadow-readiness checks.
- Initial pytest conformance suite.

Explicitly out of scope:

- MCP transport.
- HTTP servers.
- UI or dashboard integration.
- Broker access.
- Execution, cron, deployment, or orchestration coupling.
- Mutation of source research artifacts.

## Package Structure

`research_registry/` is divided by semantic boundary:

- `models/` — frozen enums, canonical envelope, deterministic identity,
  typed payload schemas.
- `metadata/` — envelope builder export surface.
- `provenance/` — DAG infrastructure.
- `confidence/` — confidence lattice and propagation.
- `governance/` — governance inheritance.
- `temporal/` — point-in-time fencing helpers.
- `validation/` — envelope and truth-surface validators.
- `ingestion/` — deterministic envelope hydration adapters.
- `registry/` — registry facade.
- `storage/` — local SQLite index.
- `replay/` — deterministic rebuild parity helpers.
- `observability/` — conformance, confidence, governance, and DAG
  inspection.
- `runtime/` — dependency, environment, and read-only readiness checks.

## Implementation Sequencing

1. Build envelope and payload schemas.
2. Enforce metadata, confidence, governance, surface, provenance, and
   temporal invariants at ingestion.
3. Store only a derived local SQLite index.
4. Hydrate objects through validators before returning them.
5. Verify rebuild parity from identical source envelopes.
6. Add artifact-specific adapters in future phases without treating paths
   as ontology.

## Deterministic Rebuild

`DeterministicRebuilder` ingests envelopes in deterministic topological
order based on declared parent references. Two rebuilds from identical
envelopes must produce the same object-id set and registry digest.

`DeterministicParityValidator` compares two from-scratch rebuilds using
different ingestion orderings. Drift is reported as object-id set
divergence or registry digest divergence. This is the local preparation
path for Mac Studio to GCP VM parity testing.

## Grandfathered Artifacts

Artifacts without SEM-001 envelopes are hydrated through explicit
artifact-family adapters. These adapters emit envelope-bearing
`ResearchArtifact` roots with:

- `confidence.level = LOW`
- `governance.state = UNGOVERNED`
- inferred lineage annotations
- inferred governance annotations
- inferred confidence annotations
- source content hashes

Derived audit or governance objects may be created from the grandfathered
root, but they inherit LOW confidence. The registry never silently
upgrades grandfathered artifacts.

## Replay And Observability

Replay validation currently verifies canonical envelope equivalence,
chain-hash equivalence, payload equivalence, and temporal admissibility
at a declared anchor. It reports future-information use and divergence
instead of substituting current-state results.

Registry observability is read-only and includes:

- registry integrity validation
- conformance audit checks
- confidence-chain inspection
- governance inheritance inspection
- DAG orphan and cycle inspection

## VM Shadow Preparation Sequence

Recommended sequence for a future read-only VM shadow deployment:

1. Install source from `origin/main` by fast-forward only.
2. Install deterministic dependencies from `requirements.txt`.
3. Run `RuntimeReadinessCheck` with broker environment variables absent
   from the registry process.
4. Hydrate a bounded artifact sample into a disposable SQLite registry.
5. Run `ConformanceAuditor` and `RegistryInspector`.
6. Run deterministic parity rebuilds with different ingestion orderings.
7. Compare local and VM registry digests over the same source artifact
   manifest.
8. Keep the registry database disposable; source artifacts remain the
   durable institutional substrate.

Do not attach the registry to cron, broker credentials, dashboards, or
MCP transport during shadow readiness.

## Future Migration Strategy

Future phases should add one adapter family at a time for existing Caerus
artifacts, beginning with envelope-bearing governance and audit outputs.
Grandfathered artifacts should be hydrated behind explicit adapters with
LOW confidence when lineage is inferred. MCP transport should be added
only after registry conformance and replay parity are stable.

Remaining risks:

- Artifact-family adapters are intentionally conservative and do not yet
  perform deep typed extraction for every historical artifact family.
- Grandfathered artifacts remain LOW confidence until native SEM-001
  producers exist.
- VM parity still requires a fixed manifest of source artifacts to avoid
  comparing different filesystem states.
- Schema registry publication is still represented by code-level schema
  declarations, pending the future machine-readable schema registry.
