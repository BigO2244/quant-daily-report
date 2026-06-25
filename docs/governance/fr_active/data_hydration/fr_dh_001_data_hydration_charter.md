# FR-DH-001 Data Hydration Charter

Status: DRAFT / PLANNED

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Documentation-only. This charter approves no live
hydration, broker calls, execution changes, scheduler changes, model changes,
or new runtime dependencies.

## Strategic Purpose

Define the source-of-truth rules, canonical layers, PIT-safety doctrine,
lineage requirements, freshness expectations, and vendor isolation principle
for all Caerus research data.

## Problem Statement

Research sleeves can only become decision-grade if their input data is
reproducible, date-aware, source-attributed, and isolated from vendor-specific
runtime behavior. Current data access patterns are uneven, and some sleeves
still depend on ad hoc source calls or non-PIT-safe inputs.

## Scope

- Establish raw, normalized, feature, and manifest layers.
- Define required metadata for every canonical dataset.
- Require dataset lineage and freshness visibility.
- Separate vendor ingestion from model consumption.
- Define source-of-truth behavior when multiple vendors or files disagree.

## Out of Scope

- Selecting or purchasing vendors.
- Implementing ingestion jobs.
- Changing sleeve logic.
- Changing execution, paper/live broker behavior, or scheduler state.
- Promoting any dataset to decision-grade without separate validation.

## Required Datasets

The charter applies to every research dataset, including prices, security
master, corporate actions, fundamentals, macro series, insider transactions,
SEC events, and derived features.

## Proposed Canonical Artifacts

Canonical folder proposal:

- `data/raw/`: source-preserved extracts and vendor-native payloads.
- `data/normalized/`: schema-normalized, PIT-aware tables keyed by canonical
  identifiers and dates.
- `data/features/`: deterministic derived features with versioned inputs.
- `data/manifests/`: lineage, freshness, schema, validation, and run manifests.

Required metadata fields:

- `source`
- `as_of_date`
- `effective_date`
- `filing_date` where applicable
- `ingestion_timestamp`
- `dataset_version`
- `validation_status`

Recommended supplemental metadata:

- `source_record_id`
- `canonical_security_id`
- `vendor_security_id`
- `source_file`
- `source_hash`
- `normalizer_version`
- `feature_version`
- `schema_version`
- `pit_valid_from`
- `pit_valid_to`

## Proposed Interfaces

- Ingestion jobs write raw and normalized artifacts.
- Validators read raw, normalized, and feature artifacts and write manifests.
- Research code reads canonical data through `research_data`, not vendor SDKs.
- Dashboard/email surfaces read manifests and summarized diagnostics only.

## Acceptance Criteria

- A metadata standard exists for every FR-DH child artifact.
- The layer model is documented and used by future FR-DH implementation tasks.
- Source-of-truth rules are explicit for raw source records, normalized records,
  features, and manifests.
- Vendor isolation is stated as a hard requirement for sleeve migration.
- PIT-safety doctrine is explicit and testable.

## Validation Plan

- Add or run documentation governance checks.
- Verify every FR-DH child document references the charter metadata model where
  relevant.
- In later implementation, add tests that reject artifacts missing required
  metadata.
- In later implementation, add lineage tests that prove a feature can trace back
  to source, version, and as-of date.

## Dependencies

- FR-DH-000 index.
- Existing governance registry and backlog.
- Existing FR-068 PIT universe terminology.
- Existing FR-069 sleeve architecture and evidence-envelope rules.

## Risks

- Too much vendor detail in canonical schemas could create vendor lock-in.
- Too little metadata would make PIT safety and reproducibility unverifiable.
- Optional fields may become de facto required unless schemas are versioned.

## No-Lookahead / PIT-Safety Requirements

- Canonical records must distinguish `effective_date` from knowable date.
- Filing-derived data must include `filing_date` and must not be exposed before
  that date in historical views.
- Ingestion time must be preserved separately from source effective dates.
- Restatements must create new versions or correction records; they must not
  silently rewrite historical views.

## Rollout Sequence

1. Land this charter as documentation.
2. Define initial schemas for security master and dataset freshness manifests.
3. Prototype metadata validation on existing local fixtures only.
4. Extend schema validation to fundamentals, macro, insider, SEC events, and
   features as each FR-DH child is implemented.

## Recommended Next Implementation Step

Create a read-only schema proposal for `data/manifests/dataset_freshness.json`
and a minimal security-master normalized record. Do not hydrate new data yet.
