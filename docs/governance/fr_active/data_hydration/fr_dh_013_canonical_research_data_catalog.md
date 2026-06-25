# FR-DH-013 Canonical Research Data Catalog

Status: DRAFT_RESEARCH / READ_ONLY_IMPLEMENTATION

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Read-only catalog and research-data foundation. The
current implementation may generate ignored local catalog, hydration, freshness,
raw sample, normalized sample, and manifest artifacts. It does not add vendors,
enforce runtime gates, change research outputs, change paper/live/shadow trading
behavior, or modify execution paths.

## Strategic Purpose

Create the canonical research data catalog: the master inventory and data
dictionary for every dataset Caerus may ingest, hydrate, validate, expose,
migrate, or retire.

The catalog should make future FR-DH implementation work explicit enough that
agents can add data sources, validation, and sleeve migration paths without
re-deriving ownership, PIT-safety, storage, freshness, or consumer expectations.

## Problem Statement

The FR-DH package defines individual hydration domains, but Caerus also needs a
single source of truth for the complete dataset inventory. Without a catalog,
future sleeves can depend on uncataloged data, validation expectations can drift
by source, and retiring or replacing a dataset can leave hidden consumers behind.

## Scope

- Define the canonical catalog and data dictionary for research datasets.
- Define dataset tiers, required catalog fields, status values, and deprecation
  rules.
- Seed an initial inventory across platform, multi-sleeve, sleeve-specific, and
  experimental datasets.
- Keep source-policy and canonical normalization decisions linked to the catalog
  through the FR-DH source policy decision matrix.
- Require future FR-DH implementations to update the catalog when datasets are
  added, changed, or retired.
- Keep the first implementation read-only and non-enforcing.

## Out of Scope

- Building ingestion pipelines.
- Hydrating live or historical data.
- Adding paid vendor dependencies.
- Selecting final preferred vendors.
- Enforcing catalog checks in runtime, execution, or sleeve code.
- Changing model behavior, allocation, broker calls, or scheduler behavior.

## Required Datasets

The catalog must cover every dataset family that Caerus may ingest, hydrate,
validate, expose, or retire. The initial inventory must include at least:

- OHLCV prices.
- Point-in-time security master.
- Corporate actions.
- Fundamentals.
- Fundamental features.
- Dataset freshness.
- Macro rates.
- Yield curve.
- Credit spreads.
- VIX / volatility regime.
- Insider transactions / Form 4.
- SEC 8-K events.
- SEC 10-Q / 10-K filing metadata.
- ETF / index constituents.
- Short interest.
- Options implied volatility / open interest.
- Analyst estimate revisions.
- News metadata.
- News sentiment / embeddings.
- Institutional holdings / 13F.
- Alternative datasets.

## Dataset Tiering

| Tier | Name | Definition | Examples |
|---|---|---|---|
| Tier 1 | Required platform data | Data required for canonical research identity, prices, PIT safety, validation, or broad platform operation. | OHLCV prices, security master, corporate actions, dataset freshness manifests. |
| Tier 2 | Multi-sleeve research data | Data used by multiple sleeves or broad research workflows but not required for all platform operation. | Fundamentals, fundamental features, macro rates, yield curve, VIX, ETF/index constituents. |
| Tier 3 | Sleeve-specific research data | Data primarily used by one sleeve family or candidate strategy. | Form 4 transactions, SEC events, short interest, analyst revisions, 13F holdings. |
| Tier 4 | Experimental / alternative data | Data that is exploratory, unproven, expensive, difficult to validate, or not yet approved for decision-grade use. | News sentiment, embeddings, web-derived alternative data, satellite/card/traffic proxies. |

## Required Catalog Fields

Every catalog row must include:

- `dataset_id`
- `dataset_name`
- `tier`
- `domain`
- `source_type`
- `preferred_source`
- `fallback_source`
- `cost_classification`
- `update_frequency`
- `expected_latency`
- `PIT_safe`
- `required_date_fields`
- `canonical_storage_path`
- `canonical_artifact_name`
- `validation_rules`
- `freshness_sla`
- `consuming_sleeves`
- `feature_outputs`
- `owner`
- `status`
- `deprecation_policy`
- `known_risks`

Recommended status values:

- `DRAFT`
- `PLANNED`
- `PROTOTYPE`
- `OBSERVE_ONLY`
- `DECISION_GRADE`
- `DEPRECATED`
- `RETIRED`
- `BLOCKED`

## Initial Dataset Inventory

| dataset_id | dataset_name | tier | domain | source_type | preferred_source | fallback_source | cost_classification | update_frequency | expected_latency | PIT_safe | required_date_fields | canonical_storage_path | canonical_artifact_name | validation_rules | freshness_sla | consuming_sleeves | feature_outputs | owner | status | deprecation_policy | known_risks |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `ohlcv_prices` | OHLCV prices | Tier 1 | prices | market_data | TBD existing approved price source | secondary approved price source | existing/free_or_paid_tbd | daily | T+0/T+1 by source | yes, if as-of dated | trade_date, as_of_date, ingestion_timestamp | `data/normalized/prices/` | `ohlcv_prices.json` | schema, duplicate keys, split alignment, missing bars, stale dates | next trading day | Polaris, Orion, Lyra, Phoenix, all backtests | returns, liquidity, volatility | Research Data Steward | OBSERVE_ONLY | require replacement parity before retire | stale prices, adjustment drift, delisted coverage gaps |
| `security_master_pit` | Point-in-time security master | Tier 1 | identity | security_master | source-agnostic canonical master | supplemental identifier sources | existing/free_or_paid_tbd | daily/weekly by source | T+1 or source cadence | yes | as_of_date, effective_date, ingestion_timestamp | `data/normalized/security_master/` | `security_master.json` | identifier uniqueness, symbol history, delisting visibility, active/inactive state | one business day after source refresh | all sleeves | canonical ids, universe joins | Research Data Steward | OBSERVE_ONLY | retire only after all consumers use replacement ids | survivorship bias, reused tickers, missing delistings |
| `corporate_actions` | Corporate actions | Tier 1 | corporate_actions | market_reference | source-agnostic corporate action feed | broker/reference cross-check | existing/free_or_paid_tbd | daily | T+1 by source | yes, if knowable dates exist | effective_date, as_of_date, ingestion_timestamp | `data/normalized/corporate_actions/` | `actions.json` | action type checks, adjustment factor checks, security id join | one business day after source refresh | all backtests, holdings reconciliation | adjustment factors, delisting returns | Research Data Steward | OBSERVE_ONLY | require adjusted-price parity before retire | double adjustments, missing merger/spinoff details |
| `dataset_freshness` | Dataset freshness | Tier 1 | metadata | internal_manifest | hydration swarm summary and dataset manifests | future canonical validators | internal | per hydration run | same run | yes, derived from as-of-dated run metadata | as_of_date, latest_source_observation_date, latest_ingestion_timestamp, generated_at | `data/normalized/freshness/` | `dataset_freshness.json` | status vocabulary, catalog membership, PIT status, artifact lineage | generated with every hydration run | operators, FR-DH validators, research_data API | data trust status, freshness status, lineage status | Research Data Steward | OBSERVE_ONLY | preserve historical manifests before retire | stale manifests, upstream partial data, missing source observation dates |
| `fundamentals_pit` | Fundamentals | Tier 2 | fundamentals | filings/vendor_normalized | Sharadar SF1 sample after safe fallback probes | SEC/company filings where feasible | paid_or_existing_tbd | quarterly/daily refresh | filing date plus ingestion latency | yes, required | fiscal_period_end, filing_date, as_of_date, ingestion_timestamp | `data/normalized/fundamentals/` | `statements.json` | filing-date gating, restatement versioning, fiscal-period alignment | one business day after filing ingestion | Quality, Value, Phoenix, future fundamental sleeves | raw statement fields, valuation inputs | Research Data Steward | OBSERVE_ONLY | deprecate only after feature parity and PIT audit | future restatement leakage, missing filing dates |
| `fundamental_features` | Fundamental features | Tier 2 | features | derived | FR-DH-005 feature builders | none unless approved | internal | daily after fundamentals refresh | after fundamentals validation | yes, derived from PIT inputs | feature_date, as_of_date, input_dataset_version | `data/features/fundamental_features/` | `features.json` | formula reproducibility, input version lineage, missingness reasons | same day as input validation | Quality, Value, Phoenix | value, quality, profitability, leverage, growth | Research Data Steward | OBSERVE_ONLY | retire by feature_version with parity report | implicit model change, universe leakage |
| `macro_rates` | Macro rates | Tier 2 | macro | public_macro | FRED/Treasury approved source | alternate public source | free | daily/monthly by series | release lag by series | conditional on release dates | observation_date, release_date, as_of_date, ingestion_timestamp | `data/normalized/macro/` | `macro_rates.json` | release-date availability, calendar alignment, revision labeling | one business day after release | Argo, regime research, risk diagnostics | rate levels, rate changes | Research Data Steward | OBSERVE_ONLY | retire series only after mapped replacement exists | revised history, release lag, mixed frequencies |
| `yield_curve` | Yield curve | Tier 2 | macro | public_rates | Treasury approved source | FRED mirror | free | daily | same day/T+1 | yes, if publication time is tracked | observation_date, release_date, as_of_date | `data/normalized/macro/` | `yield_curve.json` | curve point completeness, interpolation policy, calendar alignment | one business day | Argo, macro regime research | slope, inversion, term premium proxies | Research Data Steward | OBSERVE_ONLY | preserve old curve methodology by version | interpolation drift, holiday gaps |
| `credit_spreads` | Credit spreads | Tier 2 | macro_credit | public_or_vendor | approved credit spread source TBD | FRED public spread proxies | free_or_paid_tbd | daily/monthly by series | release lag by series | conditional on release dates | observation_date, release_date, as_of_date | `data/normalized/macro/` | `credit_spreads.json` | release-date availability, missing series, unit consistency | one business day after release | Argo, Phoenix, risk diagnostics | spread levels, stress features | Research Data Steward | OBSERVE_ONLY | retire only with overlap/parity window | source discontinuities, revised series |
| `vix_volatility_regime` | VIX / volatility regime | Tier 2 | volatility | market_index | approved VIX source TBD | public market data mirror | existing/free_or_paid_tbd | daily | T+0/T+1 | yes, if as-of dated | trade_date, as_of_date, ingestion_timestamp | `data/normalized/volatility/` | `vix.json` | missing bars, stale date, calendar alignment | next trading day | Phoenix, Argo, risk diagnostics | vol regime, stress flags | Research Data Steward | OBSERVE_ONLY | retain legacy proxy until canonical parity passes | stale source, proxy mismatch |
| `insider_form4` | Insider transactions / Form 4 | Tier 3 | insiders | SEC_filing | SEC Form 4 source | parsed vendor mirror if approved | free_or_paid_tbd | daily | filing acceptance plus ingestion | yes | transaction_date, filing_date, as_of_date, ingestion_timestamp | `data/normalized/insiders/` | `form4_filings.json` | transaction-code map, duplicate/amendment handling, issuer join | one business day after filing | Cassiopeia, event research | insider buy/sell, cluster buying | Research Data Steward | OBSERVE_ONLY | retire parser only after accession-level parity | noisy transactions, derivative classification |
| `sec_8k_events` | SEC 8-K events | Tier 3 | sec_events | SEC_filing | SEC submissions/source filings | parsed vendor mirror if approved | free_or_paid_tbd | daily | filing acceptance plus ingestion | yes | event_date, filing_date, acceptance_timestamp, as_of_date | `data/normalized/sec_events/` | `eight_k_items.json` | item-code extraction, accession join, amendment handling | one business day after filing | Cassiopeia, event research | event flags, item-code features | Research Data Steward | OBSERVE_ONLY | retire only after event-level parity | event date vs filing date confusion |
| `sec_10q_10k_metadata` | SEC 10-Q / 10-K filing metadata | Tier 3 | sec_events | SEC_filing | SEC submissions/source filings | parsed vendor mirror if approved | free_or_paid_tbd | daily | filing acceptance plus ingestion | yes | period_end, filing_date, acceptance_timestamp, as_of_date | `data/normalized/sec_events/` | `filings.json` | form type, period alignment, CIK/security join | one business day after filing | Cygnus, Cassiopeia, fundamentals QA | filing events, reporting cadence | Research Data Steward | OBSERVE_ONLY | preserve accession history before retire | CIK mapping gaps, amended filings |
| `etf_index_constituents` | ETF / index constituents | Tier 2 | universe | constituent_reference | approved constituent source TBD | ETF holdings/public source where feasible | free_or_paid_tbd | daily/monthly by source | source cadence | yes, required for historical claims | effective_date, as_of_date, ingestion_timestamp | `data/normalized/constituents/` | `constituents.json` | membership date ranges, security id join, weight validation | source cadence plus one business day | universe research, Argo, allocation research | membership flags, benchmark exposure | Research Data Steward | OBSERVE_ONLY | retire only after overlap and membership parity | survivorship bias, current-holdings leakage |
| `short_interest` | Short interest | Tier 3 | short_interest | exchange_or_vendor | approved short interest source TBD | none until approved | paid_or_free_tbd | biweekly/monthly | publication lag by source | yes, if settlement/publication dates exist | settlement_date, publication_date, as_of_date | `data/normalized/short_interest/` | `short_interest.parquet` | publication lag, units, float denominator, stale checks | one business day after publication | Phoenix, risk diagnostics, future sleeves | short interest ratio, squeeze proxies | Research Data Steward | PLANNED | require signal parity before retire | reporting lag, vendor restatements |
| `options_iv_open_interest` | Options implied volatility / open interest | Tier 4 | options | options_vendor | approved options source TBD | none until approved | paid_tbd | daily/intraday by source | source cadence | conditional, requires timestamped availability | quote_date, expiration_date, as_of_timestamp, ingestion_timestamp | `data/normalized/options/` | `options_surface.parquet` | surface completeness, stale quotes, contract symbology | source cadence plus one business day | Phoenix, volatility research, future options overlays | IV rank, skew, open interest | Research Data Steward | PLANNED | retire only after contract-level parity | cost, contract mapping, timestamp leakage |
| `analyst_estimate_revisions` | Analyst estimate revisions | Tier 3 | estimates | estimates_vendor | approved estimates source TBD | none until approved | paid_tbd | daily | vendor publication lag | yes, required | estimate_date, revision_date, publication_date, as_of_date | `data/normalized/estimates/` | `estimate_revisions.parquet` | revision chronology, stale analyst records, security id join | one business day after source refresh | Cygnus v1, Quality, Value | estimate revision signals, surprise inputs | Research Data Steward | BLOCKED | retire only after vendor replacement and parity | paid dependency, restatement/availability leakage |
| `news_metadata` | News metadata | Tier 4 | news | news_vendor_or_public | approved news source TBD | public RSS/source metadata where feasible | free_or_paid_tbd | daily/intraday | source cadence | conditional on publication timestamp | publication_timestamp, as_of_timestamp, ingestion_timestamp | `data/normalized/news/` | `news_metadata.json` | timestamp checks, duplicate detection, source attribution | source cadence plus one business day | Cassiopeia, event research, future sleeves | news event counts, source flags | Research Data Steward | OBSERVE_ONLY | retire only after source overlap analysis | timestamp drift, syndication duplicates |
| `news_sentiment_embeddings` | News sentiment / embeddings | Tier 4 | news_features | derived_or_vendor | approved model/source TBD | none until approved | internal_or_paid_tbd | daily/intraday after news ingest | after news validation | conditional on model/input timestamps | publication_timestamp, model_run_timestamp, as_of_timestamp | `data/features/news_features/` | `news_features.parquet` | model version, input lineage, leakage checks | after news metadata validation | Cassiopeia, future alternative-data sleeves | sentiment, topic, embeddings | Research Data Steward | PLANNED | retire by model_version with reproducibility archive | model drift, future-context leakage, cost |
| `institutional_13f` | Institutional holdings / 13F | Tier 3 | ownership | SEC_filing | SEC 13F source | parsed vendor mirror if approved | free_or_paid_tbd | quarterly | filing lag | yes | report_period_end, filing_date, as_of_date, ingestion_timestamp | `data/normalized/institutional_holdings/` | `form13f_filings.json` | filing-date gating, amendment handling, security id join | one business day after filing | Cassiopeia, ownership research, risk diagnostics | ownership changes, crowding features | Research Data Steward | OBSERVE_ONLY | retire parser after CUSIP/security parity | delayed filings, CUSIP mapping, amendments |
| `alternative_datasets` | Alternative datasets | Tier 4 | alternative | experimental | none approved by default | none | paid_or_unknown | source-specific | source-specific | no until proven | source-specific as_of and availability fields | `data/normalized/alternative/` | `alternative_<dataset_id>.parquet` | source-specific schema, PIT proof, legal/compliance review | source-specific | future sleeves only | source-specific features | Research Data Steward | DRAFT | require explicit deprecation and consumer audit | cost, licensing, legal, look-ahead, weak validation |

## Proposed Canonical Artifacts

- `docs/governance/fr_active/data_hydration/fr_dh_013_canonical_research_data_catalog.md`
- `data/manifests/research_data_catalog.json`
- `data/manifests/research_data_catalog.schema.json`
- `data/manifests/research_data_catalog_history.parquet`
- `data/manifests/research_data_catalog_deprecations.json`
- `data/manifests/source_policy_decision_matrix.template.json`
- `docs/governance/fr_active/data_hydration/fr_dh_source_policy_decision_matrix.md`

The markdown file is the governance source. The current read-only
implementation also generates `data/manifests/research_data_catalog.json` from
the catalog module for validation and hydration-swarm use. Generated runtime
manifests remain ignored local artifacts unless a separate artifact-promotion
decision is approved.

The source policy decision matrix is the read-only planning surface for source
priority, credential expectations, current blocker state, and P1-P4 canonical
normalization order. It must stay aligned with the catalog before any dataset is
promoted to canonical research data.

## Proposed Interfaces

- `research_data.load_research_data_catalog()`
- `research_data.get_dataset_catalog_entry(dataset_id)`
- `research_data.validate_dataset_catalog_entry(entry)`
- `research_data.list_datasets(tier=..., domain=..., status=...)`
- `research_data.list_dataset_consumers(dataset_id)`

Future enforcement hooks may check that canonical data APIs expose only
cataloged datasets, but the first implementation is read-only and advisory.

## Catalog Rules

- Every future FR-DH implementation must update the catalog when adding,
  changing, or retiring a dataset.
- No sleeve may depend on an uncataloged dataset after migration to canonical
  research data.
- Production-impacting datasets must include freshness, lineage, validation,
  and PIT-safety metadata before decision-grade use.
- Vendor-specific references remain optional and replaceable unless a separate
  governance decision approves a vendor-specific dependency.
- Catalog changes must be reviewed as governance changes, even when the data
  pipeline change is implementation-only.
- Source-policy matrix changes must accompany catalog changes when a dataset's
  preferred source, fallback source, credential requirement, hydration status,
  or normalization priority changes.

## Acceptance Criteria

- FR-DH-013 exists and is linked from the FR-DH index, registry, active backlog,
  and roadmap surfaces.
- The catalog defines tiering, required fields, status values, deprecation
  policy, and read-only first implementation behavior.
- `research_data.catalog` can generate a machine-readable catalog with every
  required field populated, including `dataset_freshness`.
- The source policy decision matrix defines source priority, expected credential
  names, current blocker state, and canonical normalization order.
- The initial inventory includes all required dataset families listed in this
  document.
- The catalog states that migrated sleeves cannot depend on uncataloged
  datasets.
- The catalog states that production-impacting datasets require freshness,
  lineage, validation, and PIT-safety metadata.

## Validation Plan

- Run `git diff --check`.
- Run governance documentation tests.
- Run governance hygiene tests.
- Run the documentation governance validator in strict mode if available.
- Run a structural check that every FR-DH file contains the required sections.
- Run `Tests/test_data_hydration_catalog.py`.
- Run `scripts/data_hydration/validate_research_data_catalog.py`.
- In later implementation, add a formal JSON schema file for
  `research_data_catalog.json` if a schema-enforced artifact is promoted.

## Dependencies

- FR-DH-000 Data Hydration Index.
- FR-DH-001 Data Hydration Charter.
- FR-DH-009 Dataset Freshness Monitor.
- FR-DH-010 Research Data API.
- FR-DH-011 Sleeve Migration to Canonical Data.
- FR-068 PIT universe foundation.
- FR-069 modular sleeve architecture.

## Risks

- A catalog without enforcement can drift unless updates are included in every
  future FR-DH implementation checklist.
- Excessive vendor specificity can make the catalog brittle.
- Too little detail can let hidden sleeve dependencies survive migration.
- Experimental datasets can be mistaken for approved data if tier and status are
  not visible.

## No-Lookahead / PIT-Safety Requirements

- Every catalog row must identify whether the dataset is PIT-safe.
- Every PIT-relevant row must list required date fields that prove when records
  became knowable.
- Datasets that cannot prove PIT safety must remain non-decision-grade until a
  separate validation package resolves the gap.
- Derived feature datasets must reference input dataset versions and model or
  feature versions.

## Rollout Sequence

1. Maintain the FR-DH-013 catalog governance file.
2. Maintain the catalog schema in documentation and `research_data.catalog`.
3. Keep the initial inventory table synchronized with the generated catalog.
4. Link FR-DH-013 from FR-DH-000, registry, active backlog, and roadmaps.
5. Maintain the source policy decision matrix as a read-only planning artifact.
6. Maintain `data/manifests/research_data_observability.json` as the read-only
   roll-up for catalog coverage, freshness, validation, lineage, PIT status,
   source artifacts, versions/stages, and blocker reasons.
7. Maintain `outputs/data_trust/data_trust_summary.json` and markdown as the
   read-only operator-facing summary derived from cataloged datasets.
8. Keep catalog rules advisory until migration gates approve enforcement.
9. Later, add advisory checks that flag uncataloged datasets during sleeve
   migration.

## Runtime Impact Statement

Read-only research-data metadata only. The catalog generator and validators may
write ignored local manifest artifacts, but they must not modify production
trading, paper/live/shadow execution, scheduler state, broker calls, allocation,
model selection, or sleeve data consumption.

## Recommended Next Implementation Step

Keep `docs/governance/fr_active/data_hydration/fr_dh_source_policy_decision_matrix.md`,
`research_data.catalog`, and generated `data/manifests/research_data_catalog.json`
synchronized while implementing lineage/version manifests and coverage
diagnostics. Do not enforce catalog checks in runtime consumers until the
schema, catalog rows, and migration gates are approved.
