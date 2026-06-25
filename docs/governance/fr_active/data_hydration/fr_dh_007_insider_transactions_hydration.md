# FR-DH-007 Insider Transactions Hydration

Status: DRAFT / PLANNED

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Documentation-only. This spec does not activate
Cassiopeia, change event-driven research, change execution, or submit orders.

## Strategic Purpose

Normalize SEC Form 4 insider transaction data so event-driven research can
evaluate insider buys, sells, roles, transaction codes, ownership types, and
cluster-buying behavior with source validation and noise filtering.

## Problem Statement

Form 4 data is useful but noisy. Direct/indirect ownership, transaction codes,
planned sales, derivative transactions, reporting delays, and issuer identity
can distort signals unless normalized and filtered consistently.

## Scope

- Define Form 4 filing, issuer, insider, role, ownership, and transaction
  schemas.
- Capture insider buys and sells, transaction code, direct/indirect ownership,
  shares, price, value, filing date, transaction date, and source accession.
- Define cluster-buying and noise-filtering features.
- Preserve source validation results.

## Out of Scope

- Activating Cassiopeia or any insider-driven sleeve.
- Changing event selection logic.
- Treating raw Form 4 transactions as decision-grade signals without filtering.
- Adding paid vendor dependencies.

## Required Datasets

- SEC Form 4 filings and amendments.
- Issuer CIK and ticker/security mapping.
- Reporting owner identity and role fields.
- Transaction tables and footnotes.
- Ownership type and transaction code metadata.
- Security master identifiers from FR-DH-002.

## Proposed Canonical Artifacts

- `data/raw/sec/form4/`
- `data/normalized/insiders/form4_filings.parquet`
- `data/normalized/insiders/form4_transactions.parquet`
- `data/features/insider_features/features.parquet`
- `data/manifests/insider_transactions_manifest.json`

## Proposed Interfaces

- `research_data.load_insider_transactions(as_of_date=..., filters=...)`
- `research_data.load_insider_features(as_of_date=..., feature_set=...)`
- `research_data.explain_insider_event(event_id)`

## Acceptance Criteria

- Transactions link to canonical security ids and SEC accession numbers.
- Buy/sell classification respects transaction codes and derivative flags.
- Direct/indirect ownership and insider role fields are preserved.
- Cluster-buying features are reproducible and versioned.
- Noise filters distinguish open-market buys from lower-signal transaction
  classes where possible.
- Form 4 amendments and duplicates are handled deterministically.

## Validation Plan

- Fixture tests for buys, sells, derivative transactions, gifts, planned sales,
  amendments, duplicate filings, and missing prices.
- Source validation comparing parsed transaction totals to source XML where
  available.
- PIT tests using filing date and transaction date separately.
- Coverage diagnostics by issuer and date.
- Observe-only Cassiopeia research comparisons before migration.

## Dependencies

- FR-DH-001 charter.
- FR-DH-002 security master.
- FR-DH-008 SEC event hydration.
- FR-DH-009 freshness monitor.
- FR-DH-010 research data API.
- FR-069 Cassiopeia onboarding.

## Risks

- SEC XML formats and footnotes can vary.
- Transaction code interpretation can be wrong without explicit mapping.
- Filing delay and transaction date confusion can create look-ahead.
- Insider sales may be routine and noisy without filters.

## No-Lookahead / PIT-Safety Requirements

- Research views must use filing date or ingestion date as knowable date.
- Transaction date alone must not make an event available before the Form 4 was
  filed.
- Amendments must preserve original and corrected records.
- Cluster features must include only filings knowable by the as-of date.

## Rollout Sequence

1. Define Form 4 normalized schema and transaction-code map.
2. Build fixture parser tests for representative SEC filings.
3. Prototype source validation and duplicate handling.
4. Generate observe-only insider features.
5. Compare event-driven research behavior before any sleeve migration.

## Recommended Next Implementation Step

Create Form 4 fixtures covering open-market buys, open-market sells, derivative
transactions, amendments, and direct/indirect ownership fields.
