# FR-DH-000 Data Hydration Index

Status: DRAFT_RESEARCH / READ_ONLY_IMPLEMENTATION

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Read-only research-data foundation. Current
implementation may create ignored local artifacts under `data/raw/`,
`data/normalized/`, `data/features/`, `data/manifests/`, and
`data/hydration_logs/`. It does not change live, paper, shadow, broker,
scheduler, portfolio construction, model selection, allocation, execution, or
sleeve-consumer behavior.

## Strategic Purpose

FR-DH is the dedicated Caerus Data Hydration category. It governs the path from
external source records to canonical point-in-time research data, validated
feature stores, model-facing APIs, and operator visibility.

The purpose is to make research data quality a first-class platform concern
instead of allowing each sleeve to solve ingestion, normalization, freshness,
and look-ahead controls independently.

## Problem Statement

Caerus has matured execution reliability faster than research data governance.
Known gaps include non-PIT-safe fundamentals in the Quality sleeve, a blocked
Value sleeve until filed-date-aware fundamentals exist, proxy macro regime
inputs, and direct vendor-call risk in future sleeves.

Sharadar, SEC, FRED, Treasury, VIX, and other sources should feed the platform.
No vendor should become the platform.

## Scope

- Define the FR-DH governance category and implementation order.
- Establish the canonical plan for source ingestion, normalization, features,
  manifests, freshness, APIs, sleeve migration, and operator visibility.
- Keep research hydration separate from execution, broker submission, and live
  capital decisions.
- Make PIT-safety, lineage, and reproducibility required acceptance criteria for
  all downstream implementation work.

## Out of Scope

- Promoting any hydrated or normalized data to decision-grade status.
- Wiring canonical data into strategy, sleeve, execution, or scheduler paths.
- Adding paid vendor dependencies beyond already configured external access.
- Introducing required runtime dependencies for trading.
- Changing production trading, shadow trading, paper execution, broker calls,
  allocation, model behavior, or scheduler behavior.

## Required Datasets

This index does not own datasets directly. Child FR-DH documents cover:

- Security master and identifier history.
- Corporate actions.
- Filed-date-aware fundamentals.
- Fundamental features.
- Macro, rates, volatility, and credit features.
- Insider transaction events.
- SEC filing and event metadata.
- Dataset freshness and lineage manifests.
- Canonical research data catalog entries.

## Proposed Canonical Artifacts

- `data/raw/`
- `data/normalized/`
- `data/features/`
- `data/manifests/`
- `data/manifests/dataset_freshness.json`
- `data/manifests/research_data_catalog.json`
- `data/manifests/research_data_observability.json`
- `data/manifests/p1_normalization_schema.template.json`
- `data/manifests/source_policy_decision_matrix.template.json`
- `docs/governance/fr_active/data_hydration/`
- `docs/governance/fr_active/data_hydration/fr_dh_runtime_credentials_setup.md`
- `docs/governance/fr_active/data_hydration/fr_dh_p1_normalization_schema.md`
- `docs/governance/fr_active/data_hydration/fr_dh_source_policy_decision_matrix.md`

## Proposed Interfaces

- `research_data.load_prices()`
- `research_data.load_security_master()`
- `research_data.load_corporate_actions()`
- `research_data.load_fundamentals()`
- `research_data.load_fundamental_features()`
- `research_data.load_macro_features()`
- `research_data.load_insider_transactions()`
- `research_data.load_sec_events()`
- `research_data.load_research_data_catalog()`

After migration, no model or sleeve may call vendors directly. Sleeves must use
canonical artifacts through the internal `research_data` interface unless a
separate governance exception is approved and time-bounded.

## FR-DH Sequence

| FR-DH | Title | Purpose |
|---|---|---|
| FR-DH-000 | Data Hydration Index | Category map, sequencing, dependency graph, and migration rule. |
| FR-DH-001 | Data Hydration Charter | Source-of-truth rules, layer model, metadata standard, and vendor isolation. |
| FR-DH-002 | Canonical Security Master | PIT-safe security identifiers, symbol history, and active/inactive state. |
| FR-DH-003 | Corporate Actions Hydration | Splits, dividends, mergers, ticker changes, delistings, and adjustment audit trail. |
| FR-DH-004 | PIT Fundamentals Hydration | Filed-date-aware financial statements, shares, market cap, and valuation fields. |
| FR-DH-005 | Fundamental Feature Store | Reproducible value, quality, profitability, leverage, growth, and capital-efficiency features. |
| FR-DH-006 | Macro Data Hydration | FRED, Treasury, VIX, credit, and yield-curve data with publication lag handling. |
| FR-DH-007 | Insider Transactions Hydration | SEC Form 4 transactions, role metadata, ownership type, and cluster-buying signals. |
| FR-DH-008 | SEC Event Hydration | 8-K item codes, 10-Q/10-K metadata, earnings/event metadata, and event dating. |
| FR-DH-009 | Dataset Freshness Monitor | Freshness, completeness, schema, anomaly, and PIT violation checks. |
| FR-DH-010 | Research Data API | Internal model-facing access layer that isolates vendors from sleeves. |
| FR-DH-011 | Sleeve Migration to Canonical Data | Observe-only migration plan for Quality, Value, Phoenix, Cassiopeia, Cygnus, Argo, and future sleeves. |
| FR-DH-012 | Data Dashboard and Email Visibility | Read-only data trust visibility for operators. |
| FR-DH-013 | Canonical Research Data Catalog | Master inventory and data dictionary for every dataset Caerus may ingest, hydrate, validate, expose, or retire. |

Supporting matrix:

- `fr_dh_source_policy_decision_matrix.md` records the read-only source policy,
  expected credential names, blocker state, and P1-P4 canonical normalization
  order for the cataloged datasets.
- `fr_dh_runtime_credentials_setup.md` records the non-secret runtime setup for
  focused Nasdaq Data Link / Sharadar probes.
- `fr_dh_p1_normalization_schema.md` records the draft P1 canonical
  normalization schemas used by the read-only P1 JSON normalizer and validator.

## Implementation Phases

| Phase | Scope | FR-DH Items |
|---|---|---|
| Phase 0 | Charter / governance | FR-DH-000, FR-DH-001, FR-DH-013 |
| Phase 1 | Security master, corporate actions, freshness monitor | FR-DH-002, FR-DH-003, FR-DH-009 |
| Phase 2 | PIT fundamentals, fundamental features | FR-DH-004, FR-DH-005 |
| Phase 3 | Macro and insider data | FR-DH-006, FR-DH-007 |
| Phase 4 | SEC events and research data API | FR-DH-008, FR-DH-010 |
| Phase 5 | Sleeve migration and dashboard/email visibility | FR-DH-011, FR-DH-012 |

## Dependency Graph

```text
FR-DH-001 charter
  -> FR-DH-013 canonical research data catalog
  -> FR-DH-002 security master
  -> FR-DH-003 corporate actions
  -> FR-DH-009 freshness monitor

FR-DH-013 catalog
  -> all dataset-specific FR-DH implementation work
  -> runtime credential setup for read-only source probes
  -> source policy decision matrix
  -> P1 normalization schema planning
  -> FR-DH-010 research data API
  -> FR-DH-011 sleeve migration

FR-DH-002 + FR-DH-003
  -> FR-DH-004 PIT fundamentals
  -> FR-DH-005 fundamental feature store

FR-DH-006 macro + FR-DH-007 insider + FR-DH-008 SEC events
  -> FR-DH-010 research data API

FR-DH-004 + FR-DH-005 + FR-DH-006 + FR-DH-007 + FR-DH-008 + FR-DH-010
  -> FR-DH-011 sleeve migration
  -> FR-DH-012 dashboard/email visibility

FR-DH-009 monitors every dataset family before decision-grade use.
FR-DH-013 catalogs every dataset before migrated sleeves may depend on it.
```

## Acceptance Criteria

- All FR-DH governance files exist and are discoverable from the registry,
  active backlog, and current research roadmap.
- Each child FR-DH document includes scope, non-goals, datasets, artifacts,
  interfaces, validation, dependencies, risks, rollout, PIT-safety, runtime
  impact, and next implementation step.
- The package clearly marks current implementation as read-only, observe-only,
  and not decision-grade.
- The package states that sleeves must stop direct vendor calls after migration.
- The package states that migrated sleeves cannot depend on uncataloged
  datasets.
- The read-only catalog, hydration swarm, freshness manifest, P1 normalization,
  P1 validator, and `research_data` load APIs are covered by targeted tests.

## Validation Plan

- Run `git diff --check`.
- Run governance documentation tests if available.
- Run the read-only governance hygiene auditor if available.
- Run `Tests/test_data_hydration_catalog.py`.
- Run `Tests/test_data_hydration_swarm.py`.
- Run `Tests/test_data_hydration_freshness.py`.
- Run `Tests/test_data_hydration_p1_normalization.py`.
- Run `Tests/test_data_hydration_p2_normalization.py`.
- Run `Tests/test_data_hydration_p3_normalization.py`.
- Run `Tests/test_data_hydration_feature_store.py`.
- Run `Tests/test_data_hydration_observability.py`.
- Run `scripts/data_hydration/validate_research_data_catalog.py`.
- Run `scripts/data_hydration/validate_dataset_freshness.py`.
- Run `scripts/data_hydration/validate_hydration_swarm.py`.
- Run `scripts/data_hydration/validate_p1_normalization.py`.
- Run `scripts/data_hydration/validate_p2_normalization.py`.
- Run `scripts/data_hydration/validate_p3_normalization.py`.
- Run `scripts/data_hydration/validate_feature_store.py`.
- Run `scripts/data_hydration/validate_research_data_observability.py`.
- Manually verify all index links and child file names.

## Dependencies

- Existing governance registry and active backlog.
- FR-068 PIT universe foundation.
- FR-069 modular sleeve architecture.
- Existing research sleeves and artifacts.

## Risks

- Premature implementation could accidentally couple research data hydration to
  execution or broker paths.
- Vendor-specific assumptions could leak into canonical interfaces.
- Metadata omissions could make PIT safety unverifiable.
- Dashboard visibility could be misread as a trading gate before governance
  approves that behavior.

## No-Lookahead / PIT-Safety Requirements

- Every dataset used for research decisions must expose the date on which a
  record became knowable to Caerus.
- Filing-derived data must not be available before filing date and ingestion
  timestamp.
- Restated data must not overwrite prior point-in-time views without preserving
  versioned lineage.
- Feature builders must declare their input versions, as-of date, and lookback
  windows.

## Rollout Sequence

1. Maintain FR-DH governance package and catalog as the source of truth.
2. Keep source credentials only in approved read-only runtime shells or
   approved env-file mechanisms outside the repository.
3. Run the hydration swarm in dry-run or sample mode to refresh raw samples and
   freshness metadata.
4. Run P1 normalization and `validate_p1_normalization.py` for OHLCV, security
   master, corporate actions, and dataset freshness.
5. Maintain P2 observe-only normalizers and validators for PIT fundamentals,
   macro/rates, VIX, insider, and SEC metadata; do not promote while
   restatement, release-date, transaction-level, and source-policy gaps remain.
6. Maintain observe-only feature-store builders after normalized inputs have
   lineage and PIT-safety validation.
7. Add feature coverage diagnostics and feature-definition manifests.
8. Maintain P3 source-specific normalizers for ETF/index constituents, 13F
   filing metadata, and news metadata where read-only samples exist.
9. Maintain `research_data_observability.json` as the read-only data readiness,
   lineage, validation, and blocker summary.
10. Resolve blocked-source datasets or mark them explicitly blocked with source,
   legal, credential, or business-decision reasons.
11. Migrate sleeves observe-only behind explicit governance gates.
12. Add dashboard/email visibility after data trust status is stable.

## Recommended Next Implementation Step

Implement the next observe-only dependency: lineage/version manifests and
coverage diagnostics across P1-P3 artifacts. Keep blocked datasets explicitly
blocked until source, credential, legal, or business-decision requirements are
resolved, and keep all strategy, sleeve, execution, broker, allocation, and
scheduler consumers unchanged until a separate migration gate approves
consumption.
