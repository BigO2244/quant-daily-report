# FR-DH-006 Macro Data Hydration

Status: DRAFT_RESEARCH / READ_ONLY_IMPLEMENTATION

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Read-only macro normalization and feature generation.
The current implementation may write ignored local artifacts under
`data/normalized/macro/`, `data/normalized/volatility/`,
`data/features/macro_regime_features/`, and `data/manifests/`. This spec does
not change Argo, regime selection, risk controls, allocation, execution, broker
behavior, scheduler behavior, or sleeve consumption.

## Strategic Purpose

Create canonical macro, rates, volatility, credit, and yield-curve datasets so
macro-regime research can move from proxy signals to source-attributed,
publication-lag-aware inputs.

## Problem Statement

Macro regime logic currently relies on proxy inputs in places. Without
canonical macro hydration and release-date handling, regime research can become
stale, inconsistent, or vulnerable to look-ahead bias from revised historical
series.

## Scope

- Hydrate and normalize FRED, Treasury, VIX, credit spread, yield curve, CPI,
  employment, and related macro series where approved sources are available.
- Preserve publication/release dates and revision vintages where available.
- Align macro series to trading calendars.
- Provide feature-ready macro views for Argo and future regime sleeves.
- Build an observe-only `macro_regime_features_v1_observe_only` artifact from
  normalized macro, yield, credit, and VIX samples.

## Out of Scope

- Changing regime decisions in this patch.
- Adding paid vendor dependencies.
- Replacing Argo evidence logic before observe-only validation.
- Treating revised macro history as historically knowable without vintage data.

## Required Datasets

- FRED economic time series.
- Treasury rates and yield curve points.
- VIX and volatility indices.
- Credit spreads.
- Inflation and employment series.
- Calendar and trading-day alignment data.
- Release calendars or vintage metadata where available.

## Proposed Canonical Artifacts

- `data/raw/macro/`
- `data/normalized/macro/macro_rates.json`
- `data/normalized/macro/yield_curve.json`
- `data/normalized/macro/credit_spreads.json`
- `data/normalized/volatility/vix.json`
- `data/features/macro_regime_features/features.json`
- `data/manifests/p2_normalization_manifest.json`
- `data/manifests/feature_store_manifest.json`

## Proposed Interfaces

- `research_data.load_macro()`
- `research_data.load_macro_regime_features()`
- `research_data.load_macro_series(series_id, as_of_date=...)`
- `research_data.load_macro_release_calendar()`

## Acceptance Criteria

- Macro observations expose observation date, release date, source, ingestion
  timestamp, dataset version, and validation status.
- Trading-day alignment uses only data released by the as-of date.
- Revised series are either vintage-aware or explicitly marked non-decision-
  grade for historical simulation.
- Argo can run observe-only against canonical macro features before any
  migration.

## Validation Plan

- Fixture tests for publication lag, weekend/holiday alignment, missing release
  dates, and revised observations.
- Compare source series counts and date ranges against source manifests.
- Run macro feature stability tests.
- Run `Tests/test_data_hydration_p2_normalization.py`.
- Run `Tests/test_data_hydration_feature_store.py`.
- Run `scripts/data_hydration/normalize_p2.py --as-of-date <date>`.
- Run `scripts/data_hydration/build_feature_store.py --as-of-date <date>`.
- Run `scripts/data_hydration/validate_p2_normalization.py`.
- Run `scripts/data_hydration/validate_feature_store.py`.
- Run Argo observe-only parity and divergence reports.
- Require FR-DH-009 freshness monitor coverage.

## Dependencies

- FR-DH-001 charter.
- FR-DH-009 freshness monitor.
- FR-DH-010 research data API.
- FR-069 Argo evidence framework.

## Risks

- Revised macro series can create look-ahead if vintage history is unavailable.
- Calendar alignment can use data on a date before the market could know it.
- Proxy replacement may alter model behavior and must be treated as a separate
  observe-only migration.

## No-Lookahead / PIT-Safety Requirements

- Macro features must use release date or vintage date, not just observation
  period date.
- If vintage data is unavailable, the series must be labeled with PIT limitation
  and barred from decision-grade historical claims.
- Trading-day fills must carry source and lag diagnostics.

## Rollout Sequence

1. Define macro schema and release-date fields.
2. Build fixture tests for publication lag and trading-day alignment.
3. Prototype source hydration using only approved free or existing sources.
4. Generate observe-only macro features.
5. Compare Argo proxy and canonical macro behavior before migration.

## Recommended Next Implementation Step

Harden release-date/vintage policy and coverage diagnostics for public macro
samples, then compare `macro_regime_features_v1_observe_only` against legacy
Argo proxy inputs without wiring it into Argo.
