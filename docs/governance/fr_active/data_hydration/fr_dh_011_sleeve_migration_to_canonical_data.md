# FR-DH-011 Sleeve Migration to Canonical Data

Status: DRAFT_RESEARCH / READ_ONLY_IMPLEMENTATION

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Read-only advisory migration audit. The current
implementation writes ignored local readiness artifacts under
`outputs/research/data_migration/` and does not invoke sleeve runtime code,
migrate any sleeve, change rankings, promote a model, alter allocation, or
affect paper/live execution.

## Strategic Purpose

Define the controlled observe-only migration path from legacy or direct-source
data access to canonical FR-DH data for Quality, Value, Phoenix, Cassiopeia,
Cygnus, Argo, and future sleeves.

## Problem Statement

Data migration can look like a model change. Without parity tests, backtest
comparison, evidence envelopes, and observe-only windows, Caerus could confuse
data-source changes with genuine strategy improvement or degradation.

## Scope

- Define migration stages for each sleeve family.
- Require observe-only canonical-data runs before promotion.
- Require parity tests and backtest comparison.
- Preserve legacy results as lineage-only where data defects are found.
- Define rules for future sleeves to start on canonical data by default.
- Build a read-only migration readiness audit from the FR-069 sleeve manifest
  and FR-DH observability manifest.

## Out of Scope

- Performing sleeve migration in this patch.
- Changing production allocations.
- Changing model formulas.
- Promoting any sleeve.
- Relaxing existing evidence gates.

## Required Datasets

- FR-DH-002 security master.
- FR-DH-003 corporate actions.
- FR-DH-004 PIT fundamentals.
- FR-DH-005 fundamental features.
- FR-DH-006 macro features.
- FR-DH-007 insider transactions.
- FR-DH-008 SEC events.
- FR-DH-009 freshness monitor.
- FR-DH-010 research data API.

## Proposed Canonical Artifacts

- `outputs/research/data_migration/<sleeve>/<date>/parity_report.json`
- `outputs/research/data_migration/<sleeve>/<date>/backtest_comparison.json`
- `outputs/research/data_migration/<sleeve>/<date>/data_lineage.json`
- `outputs/research/data_migration/<sleeve>/<date>/migration_readiness.md`
- `outputs/research/data_migration/<date>/migration_readiness.json`
- `outputs/research/data_migration/<date>/migration_readiness.md`

## Proposed Interfaces

- Sleeve adapters consume `research_data` APIs.
- Migration reports compare legacy data path versus canonical data path.
- Evidence envelopes record dataset versions, feature versions, freshness
  status, and PIT validation status.
- `scripts/data_hydration/build_sleeve_migration_readiness.py` produces the
  advisory readiness artifact without running sleeves.

## Acceptance Criteria

- Quality migration proves fundamentals are PIT-safe and no longer depend on
  yfinance-style non-PIT inputs.
- Value remains blocked until FR-DH-004 and FR-DH-005 are decision-grade.
- Phoenix migration preserves PIT universe and liquidity/capacity evidence.
- Cassiopeia migration uses canonical event, insider, and SEC data only after
  source validation.
- Cygnus migration remains vendor-gated for consensus/surprise data unless a
  separate approved source exists.
- Argo migration compares proxy macro inputs to canonical macro features before
  changing behavior.
- Future sleeves start with canonical data and must justify exceptions.

## Validation Plan

- Per-sleeve parity tests between legacy and canonical input shapes.
- Backtest comparison before and after canonical migration.
- PIT and freshness status included in every migration artifact.
- Run `Tests/test_sleeve_migration_readiness.py`.
- Run `scripts/data_hydration/build_sleeve_migration_readiness.py --as-of-date
  <date>`.
- Run `scripts/data_hydration/validate_sleeve_migration_readiness.py --path
  outputs/research/data_migration/<date>/migration_readiness.json`.
- Observe-only window before any promotion or runtime change.
- Review under FR-069 evidence-envelope rules.

## Dependencies

- FR-DH-002 through FR-DH-010.
- FR-069 modular sleeve architecture.
- FR-068 PIT universe foundation.
- Existing sleeve onboarding packets and evidence envelopes.

## Risks

- Canonical data may expose that old research was non-decision-grade.
- Backtest deltas may be due to data corrections rather than strategy behavior.
- Partial migration can leave direct vendor calls in hidden code paths.
- Observe-only outputs can be mistaken for promotion evidence without maturity
  gates.

## No-Lookahead / PIT-Safety Requirements

- Migration evidence must state whether old and new paths are PIT-safe.
- Canonical migrated sleeves must use as-of filtered `research_data` calls.
- Any legacy result using non-PIT data must be labeled non-decision-grade or
  lineage-only where applicable.

## Rollout Sequence

1. Inventory direct vendor and ad hoc data calls by sleeve.
2. Maintain advisory readiness artifacts from sleeve and dataset manifests.
3. Migrate one low-risk research-only sleeve in observe-only mode.
4. Produce parity and backtest comparison artifacts.
5. Expand to Quality and Value after fundamentals/features are validated.
6. Migrate event and regime sleeves after SEC/insider/macro datasets are ready.
7. Require future sleeves to use canonical APIs from inception.

## Recommended Next Implementation Step

Use the advisory migration readiness artifact to choose the first observe-only
parity candidate. Keep blocked sleeves blocked until required datasets are
observe-only ready and warning-only datasets have acceptable freshness,
coverage, and PIT evidence.
