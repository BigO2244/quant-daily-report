# FR-DH-002 Canonical Security Master

Status: DRAFT / PLANNED

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Documentation-only. This spec does not change
security resolution, execution symbol handling, broker submission, strategy
selection, or scheduler behavior.

## Strategic Purpose

Create a PIT-safe canonical security master that lets research, backtests,
feature builders, and sleeve migration use stable identifiers instead of
survivorship-biased current tickers.

## Problem Statement

Ticker-only research data is fragile. Tickers change, securities delist,
companies merge, and exchanges reclassify listings. Without date-effective
identity and status records, Caerus can overstate historical results and lose
auditability for current holdings reconciliation.

## Scope

- Define canonical security identifiers.
- Preserve ticker history and symbol validity windows.
- Track active/inactive state, exchange, sector, industry, delisting state, and
  permanent identifiers.
- Support multiple source identifiers without making any vendor mandatory.
- Provide date-effective lookups for research and hydration.

## Out of Scope

- Replacing execution-time broker asset validation.
- Changing broker order symbols.
- Rebuilding existing backtests in this patch.
- Mandating Sharadar as the only source. Sharadar may feed the master, but the
  canonical contract must be source-agnostic.

## Required Datasets

- Ticker and symbol history.
- Permanent identifiers such as CIK, FIGI, CUSIP, ISIN, vendor ids, or internal
  canonical ids where available.
- Exchange listing history.
- Sector and industry classifications with effective dates.
- Active/inactive and delisting status.
- Corporate action references from FR-DH-003.

## Proposed Canonical Artifacts

- `data/normalized/security_master/security_master.parquet`
- `data/normalized/security_master/symbol_history.parquet`
- `data/normalized/security_master/identifier_map.parquet`
- `data/normalized/security_master/listing_status.parquet`
- `data/manifests/security_master_manifest.json`

## Proposed Interfaces

- `research_data.load_security_master(as_of_date=...)`
- `research_data.resolve_security_id(symbol, as_of_date=...)`
- `research_data.resolve_symbol(security_id, as_of_date=...)`
- `research_data.load_symbol_history(security_id=...)`

## Acceptance Criteria

- A historical ticker resolves to the correct canonical security for the
  requested as-of date.
- Delisted and inactive securities remain visible to historical research.
- Active-only filters must be explicit and date-scoped.
- Each record includes source, as-of date, effective date, ingestion timestamp,
  dataset version, and validation status.
- Security-master tests include ticker changes, delistings, reused tickers, and
  currently active securities.

## Validation Plan

- Build fixture tests for ticker changes, delistings, exchange moves, and symbol
  reuse.
- Verify that universe construction can include inactive securities when the
  as-of date requires them.
- Compare source counts and delisting coverage against available source
  manifests.
- Require freshness monitor coverage under FR-DH-009 before decision-grade use.

## Dependencies

- FR-DH-001 charter.
- FR-DH-003 corporate actions for delistings, ticker changes, and mergers.
- FR-DH-009 freshness monitor.
- FR-068 PIT universe foundation.

## Risks

- Security identifiers can conflict across sources.
- Missing delisted securities can create survivorship bias.
- Current-sector classification can leak future information into historical
  views unless sector/industry records are date-effective.

## No-Lookahead / PIT-Safety Requirements

- Lookups must use the requested as-of date and must not resolve using future
  ticker changes.
- Delisting and inactive status must be represented as date-effective facts.
- Sector, industry, and exchange classification must be date-effective or
  explicitly marked non-PIT and barred from decision-grade research.

## Rollout Sequence

1. Define canonical schema and fixture set.
2. Build a read-only prototype from available sources.
3. Validate delisted and current security coverage.
4. Add `research_data` lookup functions.
5. Migrate research code observe-only before any production consumer changes.

## Recommended Next Implementation Step

Implement a fixture-only schema and resolver test for symbol history and
delisting visibility, then evaluate available sources for coverage without
adding a new paid dependency.
