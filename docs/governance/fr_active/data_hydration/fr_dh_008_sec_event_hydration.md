# FR-DH-008 SEC Event Hydration

Status: DRAFT / PLANNED

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Documentation-only. This spec does not change
Cassiopeia, Cygnus, event selection, model behavior, execution, or scheduler
state.

## Strategic Purpose

Normalize SEC event metadata so event-driven and earnings-drift research can
use filing-aware 8-K, 10-Q, 10-K, and earnings/event information without
look-ahead bias.

## Problem Statement

Event research depends on the date an event became knowable. Filing period end,
event date, filing acceptance date, press-release date, and market reaction date
can differ. Without explicit dating rules, research can inadvertently use events
before they were public.

## Scope

- Parse 8-K item codes.
- Normalize 10-Q and 10-K filing metadata.
- Capture earnings and event metadata where source coverage permits.
- Preserve filing acceptance timestamp, filing date, period date, event date,
  accession number, source URL/path, and security mapping.
- Define event dating and no-lookahead rules.

## Out of Scope

- Trading on events.
- Rebuilding Cygnus or Cassiopeia signals in this patch.
- Adding consensus EPS or paid event datasets.
- Treating source text extraction as final without validation.

## Required Datasets

- SEC submissions and filing metadata.
- 8-K item code data.
- 10-Q and 10-K filing metadata.
- Earnings filing or release metadata where available.
- Company CIK and canonical security mapping.
- Optional future: consensus and surprise data under separate governance.

## Proposed Canonical Artifacts

- `data/raw/sec/events/`
- `data/normalized/sec_events/filings.parquet`
- `data/normalized/sec_events/eight_k_items.parquet`
- `data/normalized/sec_events/earnings_events.parquet`
- `data/manifests/sec_events_manifest.json`

## Proposed Interfaces

- `research_data.load_sec_events(as_of_date=..., event_type=...)`
- `research_data.load_filings(form_type=..., as_of_date=...)`
- `research_data.explain_sec_event(event_id)`

## Acceptance Criteria

- 8-K item codes are captured and linked to filing accession numbers.
- 10-Q/10-K metadata includes form type, period date, filing date, acceptance
  timestamp, and security id.
- Event date and knowable date are separate fields.
- Historical research views include only events filed or ingested by the as-of
  date.
- Source parsing and normalization errors are recorded with validation status.

## Validation Plan

- Fixture tests for 8-K item extraction, 10-Q metadata, 10-K metadata, amended
  filings, missing CIK mappings, and event-date/filing-date differences.
- Source reconciliation against SEC metadata fields.
- PIT tests confirming events are unavailable before acceptance/filing time.
- Observe-only Cygnus and Cassiopeia comparisons before migration.

## Dependencies

- FR-DH-001 charter.
- FR-DH-002 security master.
- FR-DH-007 insider transactions for SEC source conventions.
- FR-DH-009 freshness monitor.
- FR-DH-010 research data API.
- FR-069 Cygnus and Cassiopeia onboarding.

## Risks

- Event dates may be disclosed in filing text but not available in structured
  fields.
- Time-zone handling around SEC acceptance timestamps can affect same-day
  availability.
- Earnings event metadata may need a separate vendor or source decision.

## No-Lookahead / PIT-Safety Requirements

- SEC events must use filing acceptance timestamp or filing date as the earliest
  knowable date unless a separate source proves earlier public release.
- Event date alone must not make an event available in historical simulation.
- Amended filings must preserve original and amended versions.
- Calendar alignment must respect market session timing where used in returns.

## Rollout Sequence

1. Define filing and 8-K item schema.
2. Build fixture tests from representative SEC metadata.
3. Prototype normalized filing metadata.
4. Add event features only after source validation.
5. Migrate event-driven sleeves observe-only before any signal use.

## Recommended Next Implementation Step

Implement a fixture-only parser and schema validator for 8-K item codes and
10-Q/10-K filing metadata, with no sleeve integration.
