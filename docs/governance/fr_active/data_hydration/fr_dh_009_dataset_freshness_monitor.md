# FR-DH-009 Dataset Freshness Monitor

Status: DRAFT / PLANNED

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Documentation-only. This spec does not install a cron
job, block execution, mutate production artifacts, or change dashboard behavior.

## Strategic Purpose

Provide a daily, deterministic data trust artifact that reports freshness,
completeness, schema validity, anomaly checks, and PIT-safety violations for
canonical research datasets.

## Problem Statement

Research and operator decisions need to know whether data is current,
complete, schema-valid, and PIT-safe. Without a canonical freshness monitor,
stale or partial data can silently degrade research quality or cause sleeves to
fall back to ad hoc inputs.

## Scope

- Define freshness, completeness, schema, anomaly, and PIT violation checks.
- Produce the daily artifact `data/manifests/dataset_freshness.json`.
- Define failure levels and per-dataset diagnostics.
- Feed read-only dashboard/email visibility under FR-DH-012.

## Out of Scope

- Mutating datasets.
- Auto-repairing failed hydration.
- Blocking paper/live execution in the initial implementation.
- Sending emails or changing dashboards in this patch.

## Required Datasets

- Dataset manifests from security master, corporate actions, fundamentals,
  features, macro, insider, and SEC event datasets.
- Schema registry or schema expectations.
- Trading calendar and expected update cadence per dataset.
- PIT validation outputs where applicable.

## Proposed Canonical Artifacts

- `data/manifests/dataset_freshness.json`
- `data/manifests/dataset_freshness_history.parquet`
- `data/manifests/schema_validation.json`
- `data/manifests/pit_validation.json`

Failure levels:

- `OK`
- `WARN_STALE`
- `WARN_PARTIAL`
- `FAIL_MISSING`
- `FAIL_SCHEMA`
- `FAIL_PIT_VIOLATION`

## Proposed Interfaces

- `research_data.load_dataset_freshness(as_of_date=...)`
- `research_data.require_dataset_status(dataset, max_level=...)`
- Read-only dashboard/email consumers under FR-DH-012.

## Acceptance Criteria

- The freshness artifact is deterministic for a given input state.
- Each dataset row includes status, source, latest data date, latest ingestion
  timestamp, expected cadence, schema version, dataset version, validation
  status, and reason codes.
- PIT violations are explicit and cannot be downgraded silently.
- Missing and partial datasets are distinguishable.
- The monitor can run read-only without broker access or execution side effects.

## Validation Plan

- Fixture tests for OK, stale, partial, missing, schema failure, and PIT
  violation states.
- Snapshot tests for artifact determinism.
- Tests proving no broker or order-submission interfaces are invoked.
- Schema checks for every required artifact field.
- Later integration tests for dashboard/email read-only consumption.

## Dependencies

- FR-DH-001 charter.
- Dataset manifests from FR-DH-002 through FR-DH-008.
- FR-DH-012 visibility.
- Existing governance hygiene patterns.

## Risks

- Freshness thresholds can create false alarms if dataset cadence is not
  documented.
- A monitor that only checks file timestamps can miss stale source contents.
- A dashboard status can be mistaken for an execution gate before governance
  approves gating.

## No-Lookahead / PIT-Safety Requirements

- The monitor must fail or warn on records whose availability dates violate the
  declared PIT contract.
- PIT validation status must be machine-readable.
- A `FAIL_PIT_VIOLATION` state must not be converted to `OK` by downstream
  consumers without separate governance approval.

## Rollout Sequence

1. Define freshness artifact schema.
2. Implement fixture-only monitor tests.
3. Add read-only checks for the security master and corporate actions.
4. Add checks for fundamentals, features, macro, insiders, and SEC events as
   those datasets land.
5. Surface read-only status through FR-DH-012.

## Recommended Next Implementation Step

Implement `dataset_freshness.json` schema tests and a fixture-only monitor that
emits all six required failure levels.
