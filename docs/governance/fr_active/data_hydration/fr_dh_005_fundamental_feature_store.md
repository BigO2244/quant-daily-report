# FR-DH-005 Fundamental Feature Store

Status: DRAFT_RESEARCH / READ_ONLY_IMPLEMENTATION

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Read-only feature generation. The current
implementation may write ignored local artifacts under
`data/features/fundamental_features/`, `data/features/macro_regime_features/`,
and `data/manifests/`. It does not change current model features, strategy
weights, execution behavior, runtime data access, or sleeve consumption.

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
- Build an initial observe-only `fundamental_features_v1_observe_only` feature
  set from normalized PIT fundamentals.
- Build an initial observe-only `macro_regime_features_v1_observe_only` feature
  set from normalized macro, yield, credit, and VIX inputs.

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
- `data/features/fundamental_features/features.json`
- `data/features/macro_regime_features/features.json`
- `data/features/fundamental_features/feature_definitions.json`
- `data/features/fundamental_features/feature_coverage.json`
- `data/manifests/feature_store_manifest.json`

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
- The observe-only feature artifact records input artifact digest, input
  dataset schema version, feature version, as-of date, and PIT status.

## Validation Plan

- Unit tests for representative value, quality, profitability, leverage,
  growth, and capital-efficiency formulas.
- Run `Tests/test_data_hydration_feature_store.py`.
- Run `scripts/data_hydration/build_feature_store.py --as-of-date <date>`.
- Run `scripts/data_hydration/validate_feature_store.py`.
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

1. Maintain the observe-only feature family schema and versioning contract.
2. Build `fundamental_features_v1_observe_only` from normalized PIT
   fundamentals.
3. Build `macro_regime_features_v1_observe_only` from normalized macro,
   yield-curve, credit-spread, and VIX inputs.
4. Validate feature manifests, input artifact digests, and feature-date
   ordering.
5. Add feature definitions and coverage diagnostics.
6. Run observe-only sleeve parity.
7. Expose features through `research_data` only for research workflows until a
   migration gate approves sleeve consumption.

## Recommended Next Implementation Step

Add explicit `feature_definitions.json` and coverage diagnostics for the
fundamental and macro feature sets, then run observe-only parity against any
legacy Quality/Value or Argo proxy feature logic. Do not wire features into
sleeves until restatement/version, release-date policy, security-id resolution,
and migration gates pass.
