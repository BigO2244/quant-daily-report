# FR-DH-003 Corporate Actions Hydration

Status: DRAFT / PLANNED

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Documentation-only. This spec does not change price
adjustment, holdings reconciliation, broker accounting, execution behavior, or
current model outputs.

## Strategic Purpose

Normalize corporate actions so backtests, price panels, fundamentals, holdings,
and current reconciliation can explain splits, dividends, mergers, ticker
changes, and delistings consistently.

## Problem Statement

Corporate actions are a major source of silent research and accounting drift.
If splits, mergers, ticker changes, dividends, and delistings are not handled
with auditability, historical returns and current holdings reconciliation can
diverge from economic reality.

## Scope

- Normalize splits, cash dividends, stock dividends, mergers, spin-offs, ticker
  changes, name changes, delistings, and exchange changes where source coverage
  permits.
- Define adjustment policy and audit trail.
- Link corporate actions to canonical security ids.
- Preserve source records and normalized action ids.

## Out of Scope

- Changing live broker position reconciliation.
- Changing current execution share quantities.
- Rewriting historical performance in this patch.
- Applying corporate-action adjustments without a separate implementation and
  validation plan.

## Required Datasets

- Split records.
- Dividend records.
- Merger and acquisition records.
- Spin-off records where available.
- Ticker and name change records.
- Delisting records.
- Exchange/listing status records.
- Security master identifiers from FR-DH-002.

## Proposed Canonical Artifacts

- `data/raw/corporate_actions/`
- `data/normalized/corporate_actions/actions.parquet`
- `data/normalized/corporate_actions/adjustment_factors.parquet`
- `data/normalized/corporate_actions/delistings.parquet`
- `data/manifests/corporate_actions_manifest.json`

## Proposed Interfaces

- `research_data.load_corporate_actions(as_of_date=...)`
- `research_data.load_adjustment_factors(as_of_date=...)`
- `research_data.adjust_price_panel(price_panel, adjustment_policy=...)`
- `research_data.explain_corporate_action(security_id, date=...)`

## Acceptance Criteria

- Split-adjustment fixtures reproduce expected adjusted price and share math.
- Dividend records are available for return calculations without silently
  mixing price-return and total-return semantics.
- Ticker changes and delistings link back to the canonical security master.
- Each normalized action includes source, as-of date, effective date, ingestion
  timestamp, dataset version, validation status, and source record id where
  available.
- Adjustment policy is explicit and reproducible.

## Validation Plan

- Fixture tests for split, dividend, ticker change, merger, spin-off, and
  delisting examples.
- Source-to-normalized row-count reconciliation.
- Adjustment factor monotonicity and anomaly checks.
- Cross-check sample adjusted prices against a trusted source where available.
- Require FR-DH-009 freshness and schema status before decision-grade use.

## Dependencies

- FR-DH-001 charter.
- FR-DH-002 canonical security master.
- FR-DH-009 freshness monitor.
- Existing price-hydration and PIT-universe work.

## Risks

- Different vendors use different action types and effective dates.
- Applying adjustments twice can corrupt price panels.
- Missing delisting returns can overstate backtest results.
- Spin-offs and merger consideration can require manual classification when
  source fields are incomplete.

## No-Lookahead / PIT-Safety Requirements

- Corporate actions must be keyed by effective date and knowable date when
  available.
- Adjustment factors used in historical simulation must not include actions
  unknowable as of the simulation date.
- Restated or corrected actions must preserve prior versions and correction
  lineage.

## Rollout Sequence

1. Define action schema and adjustment policy.
2. Build fixture-only validator for common corporate-action classes.
3. Prototype read-only normalization from existing available sources.
4. Add adjustment diagnostics without changing current price consumers.
5. Migrate research consumers only after parity and anomaly checks pass.

## Recommended Next Implementation Step

Create fixtures for splits, dividends, ticker changes, and delistings and use
them to lock the normalized schema before any source integration.
