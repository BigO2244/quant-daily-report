# Alpha Stack Data Standards (PIT and Governance)

Purpose
- Define mandatory data contracts for Alpha Stack research and shadow so results are point-in-time correct, auditable, and promotion-safe.

Scope
- Raw ingestion, feature store semantics, filed-date fundamentals, quality checks, and cache governance.
- Applies only to Alpha Stack data paths and artifacts.

Assumptions
- Production data pipelines continue unchanged.
- Alpha Stack introduces separate storage and output paths.

Status
- Baseline standard for pre-implementation enforcement.

Future Work
- Add source-specific SLAs and retry/backfill policies.
- Add schema registry with version pinning.

## 1. DataStore Contract

Required interface
- `get_prices(symbols, as_of_date, lookback_days)`
- `get_fundamentals(symbols, as_of_date, fields)`
- `get_macro(as_of_date, series_ids)`
- `get_breadth(as_of_date, universe)`

As-of semantics
- Query at `as_of_date = D` may only return records known at close of `D`.
- Any record with source timestamp after `D 23:59:59` local market close is ineligible.

Keying and provenance
- Every record includes:
  - `symbol`
  - `effective_date`
  - `source_timestamp`
  - `ingested_at`
  - `source_name`
  - `revision_id` (if available)

## 2. Point-in-Time Rules

Fundamentals
- Must include filed/report availability date.
- Join rule:
  - valid if `filed_date <= as_of_date`
  - invalid if `filed_date > as_of_date`
- No backfilled future fundamentals in historical simulations.

Macro series
- Use release-aware timestamps where available.
- If revised, use first available vintage unless explicitly testing revision sensitivity.

Breadth and prices
- End-of-day bars are eligible only after market close on that date.
- No look-ahead intraday assumptions in daily models.

## 3. Storage Separation

Required split
- Raw immutable store: `data/alpha_stack/raw/`
- Processed feature store: `data/alpha_stack/features/`
- Research outputs: `outputs/alpha_stack/`

Forbidden
- Writing Alpha Stack research artifacts into production canonical paths.
- Overwriting production `outputs/latest.json`.

## 4. Feature Store Standards

Feature schema minimum
- `symbol`
- `feature_date`
- `feature_name`
- `feature_value`
- `input_window`
- `data_lag_days`
- `provenance_hash`

Validation checks
- Null rate per feature <= 2% on eligible universe (unless explicitly excepted)
- Outlier cap policy documented per feature
- Distribution shift monitors for key features (rolling z-score drift)

## 5. Data Quality Gates

Hard-fail gates
- PIT violation count > 0 in audit sample
- Missing required fields for any enabled sleeve
- Duplicate `(symbol, feature_date, feature_name)` keys

Soft warning gates
- Null rate > 2% and <= 5%
- Source lag exceeds SLA by <= 1 business day

Audit cadence
- Daily light checks
- Weekly PIT audit sample
- Monthly full backfill integrity check

## 6. Cost and Corporate Actions Handling

Required adjustments
- Split/dividend adjusted price series for return calculations
- Explicit corporate action handling logs

Transaction cost assumptions (minimum)
- Commission model
- Slippage model as function of ADV participation
- Spread proxy by liquidity bucket

## 7. Cache Governance

Rules
- Cache keys must include source, version, and date range.
- Cache invalidation required on schema/version bump.
- Backfill jobs must emit checksum manifest.

## 8. Promotion Data Gates

Before shadow start
- PIT audit pass rate 100% over sampled windows
- Data completeness pass for all enabled sleeve dependencies
- Reproducibility check: rerun identical inputs -> identical outputs

Before paper promotion
- 60-day shadow data quality incidents = 0 hard failures
- No unresolved lineage gaps

## 9. Explicit Deferrals

Future phase only
- Options chain PIT store
- Intraday feature store
- Alternative data with nonstandard licensing constraints
