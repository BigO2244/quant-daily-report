# FR-DH-012 Data Dashboard and Email Visibility

Status: DRAFT_RESEARCH / READ_ONLY_IMPLEMENTATION

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Read-only observability and data-trust artifacts. The
current implementation writes ignored local manifest/report data only and is
non-execution-affecting. This spec does not change dashboard runtime, email
sending, broker behavior, execution gates, or scheduler state.

## Strategic Purpose

Make data trust visible to operators by surfacing freshness, source, stale or
missing flags, PIT violations, and latest successful hydration timestamp in
dashboard and email surfaces.

## Problem Statement

Research outputs can look authoritative even when inputs are stale, partial, or
non-PIT-safe. Operators need a compact, read-only trust surface before using
research artifacts for prioritization, promotion review, or model-quality
analysis.

## Scope

- Define read-only dashboard/email data trust fields.
- Consume FR-DH-009 freshness artifacts and
  `data/manifests/research_data_observability.json`.
- Surface source, latest successful hydration timestamp, stale/missing flags,
  schema status, PIT violations, and known limitations.
- Keep initial visibility advisory and non-execution-affecting.

## Out of Scope

- Redesigning the dashboard.
- Changing dashboard auth, deploy scripts, or unrelated dashboard files.
- Sending new emails in this patch.
- Blocking execution based on data trust status in the initial implementation.
- Hiding or rewriting underlying data artifacts.

## Required Datasets

- `data/manifests/dataset_freshness.json`
- `data/manifests/research_data_observability.json`
- Dataset manifests for all FR-DH data families.
- Optional summary artifacts from FR-DH-011 migration reports.

## Proposed Canonical Artifacts

- `outputs/data_trust/data_trust_summary.json` (implemented read-only)
- `outputs/data_trust/data_trust_summary.md` (implemented read-only)
- Dashboard/email read-only view models derived from freshness and
  observability artifacts.

## Proposed Interfaces

- Dashboard reads summarized data trust status.
- Email/reporting reads the same summary artifact.
- No dashboard or email surface reads vendor APIs directly.
- No dashboard or email surface mutates canonical data.

## Acceptance Criteria

- Operators can see dataset status, source, latest data date, latest hydration
  timestamp, validation status, and reason codes.
- `FAIL_PIT_VIOLATION` is visible and loud.
- Stale, missing, partial, schema-failed, and PIT-failed states are visually and
  textually distinct.
- The visibility layer is read-only and does not call broker/order submission
  paths.
- Dashboard/email output is deterministic for a given freshness artifact.
- The observability manifest reports freshness, coverage, validation, lineage,
  PIT status, source artifacts, versions/stages, and blocker reasons per
  cataloged dataset.

## Validation Plan

- Fixture tests for each FR-DH-009 failure level.
- Run `Tests/test_data_hydration_observability.py`.
- Run `Tests/test_data_trust_summary.py`.
- Run `scripts/data_hydration/build_research_data_observability.py`.
- Run `scripts/data_hydration/validate_research_data_observability.py`.
- Run `scripts/data_hydration/build_data_trust_summary.py`.
- Run `scripts/data_hydration/validate_data_trust_summary.py`.
- Snapshot tests for summary JSON/markdown.
- Tests proving no broker, execution, or vendor submission interfaces are
  invoked.
- Manual dashboard/email review only after unrelated dashboard dirt is isolated.
- Later VM validation should verify served artifact parity if dashboard files
  are changed in a separate patch.

## Dependencies

- FR-DH-009 dataset freshness monitor.
- FR-DH-001 metadata charter.
- Dashboard/reporting governance.
- Existing operator email/reporting conventions.

## Risks

- Operators may treat advisory visibility as a hard gate or as approval to use
  data before validation.
- Dashboard changes can collide with unrelated dirty dashboard work.
- Too much detail can make the trust surface hard to scan.

## No-Lookahead / PIT-Safety Requirements

- PIT violation status must be surfaced directly from validation artifacts.
- Dashboard/email summaries must not suppress or downgrade PIT failures.
- Data trust views must preserve as-of date and dataset version context.

## Rollout Sequence

1. Define read-only summary schema from `dataset_freshness.json` and
   `research_data_observability.json`.
2. Build fixture-based summary tests.
3. Add markdown/email summary generation.
4. Add dashboard display only in a separate isolated dashboard patch.
5. Consider hard gates only after governance approval and observation.

## Recommended Next Implementation Step

Use the generated data-trust summary as the input contract for a future isolated
dashboard/email patch. Defer dashboard file changes until unrelated dashboard
work is clean or intentionally included.
