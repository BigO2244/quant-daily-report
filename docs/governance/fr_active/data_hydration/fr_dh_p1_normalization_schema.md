# FR-DH P1 Normalization Schema

Status: DRAFT_RESEARCH / READ_ONLY_IMPLEMENTATION

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Read-only normalization schema and prototype. The P1
normalizer may write ignored local JSON artifacts under `data/normalized/` and
`data/manifests/`. It does not hydrate production datasets, change
paper/live/shadow trading behavior, change execution logic, change model
decisions, or wire data into any sleeve.

## Strategic Purpose

Define the initial P1 canonical normalization schemas for the foundational
FR-DH datasets:

- OHLCV prices.
- Point-in-time security master.
- Corporate actions.
- Dataset freshness.

The schema is the contract used by the read-only P1 normalizer and validator.
The current artifact format is deterministic JSON for review and testing; a
future storage-format decision may promote Parquet or another format after
lineage and validation rules are stable.

## Problem Statement

The first hydration swarm can discover and sample sources, but raw source rows
are not yet canonical Caerus research data. P1 needs explicit schemas for
identity, dates, PIT safety, lineage, and validation before any downstream
normalization or sleeve migration begins.

## Scope

- Define required fields and primary keys for P1 canonical artifacts.
- Define PIT-safety, lineage, and validation requirements.
- Provide a machine-readable template at
  `data/manifests/p1_normalization_schema.template.json`.
- Keep the normalizer, validator, and read API observe-only.

## Out of Scope

- Marking normalized samples as production or decision-grade data.
- Backfilling canonical artifacts.
- Changing strategy, allocation, execution, broker, paper, live, or shadow
  behavior.
- Migrating sleeves to canonical data.
- Enforcing these schemas at runtime before a later approved implementation.

## Proposed Canonical Artifacts

| Dataset | Canonical artifact | Primary key |
|---|---|---|
| `ohlcv_prices` | `data/normalized/prices/ohlcv_prices.json` | `security_id`, `trade_date`, `price_source` |
| `security_master_pit` | `data/normalized/security_master/security_master.json` | `security_id`, `effective_start_date`, `source` |
| `corporate_actions` | `data/normalized/corporate_actions/actions.json` | `corporate_action_id` |
| `dataset_freshness` | `data/normalized/freshness/dataset_freshness.json` | `dataset_id`, `as_of_date` |

## Common Requirements

Every normalized P1 row must include:

- A canonical source label.
- `as_of_date`.
- `ingestion_timestamp`.
- Source lineage, including source table/endpoint or artifact digest where
  available.
- A validation status.
- PIT-safety evidence sufficient to prove the row was knowable on or before the
  research decision date.

No derived feature or sleeve may use P1 artifacts until a later governance gate
approves canonical data consumption.

## Dataset Schemas

### OHLCV Prices

Required fields:

- `security_id`
- `source_symbol`
- `trade_date`
- `open`
- `high`
- `low`
- `close`
- `close_adjusted`
- `volume`
- `price_source`
- `adjustment_policy`
- `as_of_date`
- `ingestion_timestamp`
- `source_retrieved_at`
- `source_artifact_digest`

Validation rules:

- Unique `security_id`, `trade_date`, `price_source`.
- `trade_date <= as_of_date`.
- Non-negative volume when present.
- `high >= low` when both are present.
- Adjusted and unadjusted fields must not be mixed without an explicit
  `adjustment_policy`.

### Point-in-Time Security Master

Required fields:

- `security_id`
- `source_security_id`
- `ticker`
- `name`
- `exchange`
- `asset_type`
- `is_active`
- `listing_date`
- `delisting_date`
- `effective_start_date`
- `effective_end_date`
- `as_of_date`
- `source`
- `ingestion_timestamp`
- `source_artifact_digest`

Validation rules:

- No overlapping effective intervals for the same `security_id` and source.
- Ticker history must preserve reused tickers.
- `listing_date <= delisting_date` when both are present.
- `effective_start_date <= as_of_date`.
- Active/inactive state must align with delisting fields.

### Corporate Actions

Required fields:

- `corporate_action_id`
- `security_id`
- `source_symbol`
- `action_type`
- `announcement_date`
- `ex_date`
- `record_date`
- `payable_date`
- `effective_date`
- `cash_amount`
- `split_ratio`
- `adjustment_factor`
- `old_ticker`
- `new_ticker`
- `as_of_date`
- `source`
- `ingestion_timestamp`
- `source_artifact_digest`

Validation rules:

- `effective_date <= as_of_date`.
- `action_type` must use the approved vocabulary.
- Split actions require `split_ratio`.
- Cash dividend actions require `cash_amount`.
- Ticker changes require `old_ticker` or `new_ticker`.
- `security_id` must resolve through `security_master_pit`.

### Dataset Freshness

Required fields:

- `dataset_id`
- `dataset_name`
- `as_of_date`
- `freshness_status`
- `hydration_status`
- `latest_source_observation_date`
- `latest_ingestion_timestamp`
- `artifact_path`
- `records_written`
- `validation_status`
- `PIT_safe_status`
- `reason`
- `generated_at`

Validation rules:

- `dataset_id` exists in the research data catalog.
- `freshness_status` must use the approved vocabulary.
- `latest_ingestion_timestamp <= generated_at`.
- PIT violations must map to `FAIL_PIT_VIOLATION`.
- Missing `artifact_path` requires a non-empty reason.

## No-Lookahead Requirements

- Prices must not be available before the source session date and ingestion
  timestamp permit.
- Security master rows must preserve symbol history and delisting visibility.
- Corporate actions must use knowable/effective dates explicitly and must not
  back-apply future adjustments without versioned lineage.
- Freshness manifests must separate source observation recency from Caerus
  ingestion recency.

## Validation Plan

- Validate `data/manifests/p1_normalization_schema.template.json` as JSON.
- Run `Tests/test_data_hydration_p1_normalization.py`.
- Run `scripts/data_hydration/normalize_p1.py --as-of-date <date>`.
- Run `scripts/data_hydration/validate_p1_normalization.py`.
- Validate row-level uniqueness and PIT fields before any canonical artifact is
  marked decision-grade.
- Run hydration swarm dry-run and focused source probes before implementing P1
  normalizers.

## Recommended Next Step

Continue from P1 into governed feature artifacts and P3 observe-only
normalizers. P2 normalization now exists for fundamentals, macro/rates, VIX,
insider Form 4 filing metadata, and SEC filing metadata, but those artifacts
remain observe-only until restatement/version, release-date, transaction-level,
and source-policy gaps are closed.
