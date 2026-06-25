# FR-DH-010 Research Data API

Status: DRAFT_RESEARCH / READ_ONLY_IMPLEMENTATION

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Read-only canonical artifact API. The current
implementation adds local `research_data` loaders for ignored research artifacts
only. It does not call vendors, brokers, networks, schedulers, dashboard code,
execution paths, allocation logic, or sleeve consumers.

## Strategic Purpose

Create the internal model-facing access layer that lets research sleeves consume
canonical data without knowing vendor storage formats, SDKs, credentials, or
source-specific quirks.

## Problem Statement

Direct vendor calls inside sleeves create drift, make tests brittle, and make
PIT-safety hard to enforce. After migration, sleeves should use a stable
`research_data` interface backed by canonical artifacts and manifests.

## Scope

- Define the internal `research_data` API.
- Enforce canonical artifact reads instead of direct vendor calls.
- Provide consistent as-of date and universe parameters.
- Return diagnostics for source, freshness, lineage, and validation status.
- Define testing expectations for the API.

## Out of Scope

- Rewriting sleeves in this patch.
- Changing runtime execution or production model behavior.
- Introducing new network calls at model runtime.

## Required Datasets

- Prices.
- Security master.
- Corporate actions.
- Fundamentals.
- Fundamental features.
- Macro features.
- Insider transactions.
- SEC events.
- Dataset freshness manifests.

## Proposed Canonical Artifacts

The API reads from canonical `data/normalized/`, `data/features/`, and
`data/manifests/` artifacts defined by FR-DH-001 through FR-DH-009.

## Proposed Interfaces

Required initial functions:

- `research_data.load_prices()`
- `research_data.load_security_master()`
- `research_data.load_corporate_actions()`
- `research_data.load_fundamentals()`
- `research_data.load_fundamental_features()`
- `research_data.load_macro()`
- `research_data.load_macro_regime_features()`
- `research_data.load_insider_transactions()`
- `research_data.load_sec_events()`
- `research_data.load_dataset_diagnostics(dataset_id)`
- `research_data.load_dataset_with_diagnostics(dataset_id)`
- `research_data.load_research_data_observability()`
- `research_data.load_data_trust_summary()`

Recommended common parameters:

- `as_of_date`
- `start_date`
- `end_date`
- `universe`
- `security_ids`
- `fields`
- `require_pit_safe`
- `require_freshness_status`

## Acceptance Criteria

- API calls are deterministic for a given artifact set.
- API functions do not call vendors directly.
- Every returned dataset includes source/version/lineage diagnostics or a
  companion diagnostics object.
- Missing data returns explicit reason codes rather than silent empty success.
- Test doubles can run without network, credentials, or broker access.

## Validation Plan

- Unit tests proving API functions read canonical fixture artifacts.
- Run `Tests/test_research_data_api_diagnostics.py`.
- Tests proving no vendor SDK, broker client, or network call is invoked.
- PIT tests for as-of filtering.
- Schema tests for diagnostics and missing-data reason codes.
- Sleeve observe-only tests before replacing legacy data calls.

## Dependencies

- FR-DH-001 charter.
- FR-DH-002 through FR-DH-009 canonical datasets and manifests.
- FR-DH-011 sleeve migration.
- Existing research registry and sleeve architecture.

## Risks

- Too much API surface can become hard to support.
- Too little diagnostics can hide data quality issues.
- Backward compatibility pressures can preserve unsafe legacy behavior.
- Model runtime could accidentally import vendor credentials if isolation is not
  enforced.

## No-Lookahead / PIT-Safety Requirements

- Every API call that can affect research conclusions must accept or infer an
  explicit as-of date.
- Historical calls must filter by availability date, not just observation date.
- API defaults must fail closed or return non-decision-grade status when PIT
  safety cannot be verified.

## Rollout Sequence

1. Define API signatures and diagnostics schema.
2. Implement fixture-backed read-only API functions.
3. Add no-network/no-vendor-call tests.
4. Migrate one sleeve observe-only.
5. Expand migration after parity and data-trust checks pass.

## Recommended Next Implementation Step

Add query filtering parameters such as `as_of_date`, `start_date`, `end_date`,
`security_ids`, and `fields` behind fixture-backed tests before any
observe-only sleeve migration.
