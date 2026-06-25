# FR-DH-004 PIT Fundamentals Hydration

Status: DRAFT / PLANNED

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Documentation-only. This spec does not change Quality,
Value, portfolio construction, strategy rankings, execution, or scheduler
behavior.

## Strategic Purpose

Build filed-date-aware fundamentals so Quality, Value, and future fundamental
sleeves can use financial statements without look-ahead bias.

## Problem Statement

Non-PIT-safe fundamentals can leak restated future data into historical
research. Quality currently relies on yfinance-style fundamentals that are not
adequate for decision-grade historical evaluation. Value remains blocked until
filed-date-aware fundamentals exist.

## Scope

- Normalize income statement, balance sheet, cash flow, shares, market cap, and
  valuation fields.
- Preserve filing dates, fiscal periods, report periods, restatement versions,
  and source lineage.
- Support point-in-time views as of a research date.
- Make Quality and Value sleeve dependencies explicit.

## Out of Scope

- Rewriting Quality or Value sleeve logic in this patch.
- Promoting fundamental features to production.
- Adding paid vendor dependencies.
- Using restated future data before filing date.

## Required Datasets

- Income statement fields.
- Balance sheet fields.
- Cash flow statement fields.
- Shares outstanding and float where available.
- Market cap and enterprise value inputs.
- Valuation fields such as price-to-book, price-to-sales, EV/EBITDA, earnings
  yield, free-cash-flow yield, and related components.
- Filing metadata, fiscal period metadata, and restatement metadata.
- Security identifiers from FR-DH-002.

## Proposed Canonical Artifacts

- `data/raw/fundamentals/`
- `data/normalized/fundamentals/statements.parquet`
- `data/normalized/fundamentals/fiscal_periods.parquet`
- `data/normalized/fundamentals/valuation_inputs.parquet`
- `data/normalized/fundamentals/restatement_versions.parquet`
- `data/manifests/fundamentals_manifest.json`

## Proposed Interfaces

- `research_data.load_fundamentals(as_of_date=..., fields=...)`
- `research_data.load_statement(security_id, fiscal_period=..., as_of_date=...)`
- `research_data.load_valuation_inputs(as_of_date=...)`
- `research_data.explain_fundamental_value(security_id, field, as_of_date=...)`

## Acceptance Criteria

- A historical as-of query returns only filings known by that date.
- Restated data is versioned and cannot overwrite the view available before the
  restatement was filed.
- Quality and Value can run in observe-only mode using FR-DH fundamentals
  without direct vendor calls.
- Each record includes source, as-of date, effective/report period, filing date,
  ingestion timestamp, dataset version, and validation status.
- Missing fundamentals are explicit and do not silently impute decision-grade
  values.

## Validation Plan

- Fixture tests for original filing, restatement, late filing, missing filing,
  fiscal period alignment, and security identifier joins.
- Compare as-of views before and after filing dates.
- Reconcile source row counts and filing coverage by date.
- Run Quality/Value parity comparisons in observe-only mode before migration.
- Require freshness and PIT violation checks from FR-DH-009.

## Dependencies

- FR-DH-001 charter.
- FR-DH-002 canonical security master.
- FR-DH-003 corporate actions for shares and split-aware alignment.
- FR-DH-009 freshness monitor.
- FR-DH-005 feature store.

## Risks

- Vendor fields may mix restated and original statements.
- Filing dates may be missing or inconsistent.
- Fiscal period alignment can create accidental future leakage.
- Market cap fields can be current-scale rather than PIT-valid unless sourced
  and dated carefully.

## No-Lookahead / PIT-Safety Requirements

- Restated future data is prohibited before the restatement filing date.
- Original filing values must remain reconstructible.
- Fundamental features must use filing date or ingestion date as the earliest
  availability date, never the fiscal period end alone.
- Market cap and shares data must be date-effective and source-attributed.

## Rollout Sequence

1. Define statement and filing metadata schema.
2. Build PIT fixture tests for filing and restatement scenarios.
3. Prototype read-only normalization from existing available sources.
4. Build FR-DH-005 features from the PIT view.
5. Run Quality and Value observe-only parity before any promotion.

## Recommended Next Implementation Step

Create fixture-driven PIT fundamentals tests for original filing and restatement
behavior, then map current available data sources against the required schema.
