# FR-DH-005 Fundamental Feature Store

Status: DRAFT / PLANNED

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Documentation-only. This spec does not change any
current model features, strategy weights, execution behavior, or runtime data
access.

## Strategic Purpose

Create reproducible, versioned fundamental features from FR-DH-004 PIT-safe
fundamentals so sleeves can consume value, quality, profitability, leverage,
growth, and capital-efficiency signals without direct source logic.

## Problem Statement

If each sleeve computes fundamentals independently, feature definitions drift,
PIT controls become inconsistent, and backtest results are hard to reproduce.
A canonical feature store makes feature lineage, versioning, and parity testing
explicit.

## Scope

- Define feature families for value, quality, profitability, leverage, growth,
  capital efficiency, shareholder yield, and balance-sheet strength.
- Require features to use FR-DH-004 PIT fundamentals.
- Version feature definitions and input datasets.
- Provide feature availability and missingness diagnostics.

## Out of Scope

- Optimizing factor weights.
- Changing sleeve ranking formulas.
- Promoting features to production.
- Filling missing features with unstated assumptions.

## Required Datasets

- FR-DH-004 PIT fundamentals.
- Price and market cap inputs where valuation features require them.
- Corporate actions and split-adjusted shares from FR-DH-003 where applicable.
- Security identifiers from FR-DH-002.

## Proposed Canonical Artifacts

- `data/features/fundamental_features/features.parquet`
- `data/features/fundamental_features/feature_definitions.json`
- `data/features/fundamental_features/feature_coverage.json`
- `data/manifests/fundamental_feature_manifest.json`

## Proposed Interfaces

- `research_data.load_fundamental_features(as_of_date=..., feature_set=...)`
- `research_data.load_feature_definitions(feature_version=...)`
- `research_data.explain_feature(security_id, feature_name, as_of_date=...)`

## Acceptance Criteria

- Each feature records feature version, input dataset versions, as-of date,
  lookback window, and validation status.
- Features are reproducible from stored definitions and canonical inputs.
- Missing features are represented explicitly with reason codes.
- Quality and Value can run observe-only feature comparisons against legacy
  feature logic before migration.
- Feature builders cannot read vendor SDKs directly.

## Validation Plan

- Unit tests for representative value, quality, profitability, leverage,
  growth, and capital-efficiency formulas.
- PIT tests proving features do not use filings unavailable as of the feature
  date.
- Snapshot tests for feature version stability.
- Coverage reports by date, sector, and universe.
- Parity tests against existing sleeve features where a legacy equivalent
  exists.

## Dependencies

- FR-DH-001 charter.
- FR-DH-002 security master.
- FR-DH-003 corporate actions.
- FR-DH-004 PIT fundamentals.
- FR-DH-009 freshness monitor.
- FR-DH-010 research data API.

## Risks

- Feature definitions can become implicit model changes if migrated without
  observe-only parity.
- Valuation features can leak future market cap if the market data input is not
  PIT-safe.
- Cross-sectional standardization can accidentally use future universe
  membership.

## No-Lookahead / PIT-Safety Requirements

- Features must use only records available as of the feature date.
- Cross-sectional ranks must use PIT-valid universe membership.
- Feature lookbacks must be declared and reproducible.
- Restated fundamentals may only affect feature versions after the restatement
  availability date.

## Rollout Sequence

1. Define feature family schema and versioning contract.
2. Implement fixture-only feature builders.
3. Build features from FR-DH-004 prototype data.
4. Run observe-only sleeve parity.
5. Expose features through `research_data`.

## Recommended Next Implementation Step

Define `feature_definitions.json` for a small initial Quality/Value feature
set and write fixture tests that recompute features from PIT fundamentals.
