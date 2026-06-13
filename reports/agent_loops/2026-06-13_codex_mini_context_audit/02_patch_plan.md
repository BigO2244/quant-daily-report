# Patch Plan

## Goals

1. Prevent established Shadow NAV history from silently restarting at `1.0`.
2. Reject incremental appends when prior-artifact or CSV continuity is unproven.
3. Reject existing-date overwrites unless a future explicit restatement workflow is approved.
4. Make scorecard health distinguish fresh/valid from fresh/corrupt.
5. Suppress seven-day, YTD, rankings, and promotion signals when performance continuity is corrupt.
6. Preserve original evidence and avoid historical rewrites in this checkout because the reported 2026-06-12 corrupted artifact is absent locally.

## Failing Tests First

Regression tests were added before repair for:

- Missing previous dated artifact inside established NAV history.
- Append continuity mismatch between prior CSV NAV, `previous_nav`, `daily_return`, and candidate NAV.
- Existing-date overwrite attempt.
- Missing strategy column/schema mismatch.
- Simultaneous strategy and SPY scale reset.
- Corrupted scorecard suppressing windows/rankings/promotion signal.
- Legitimate inception behavior.
- Legitimate incremental append behavior.

Initial focused run failed on the pre-patch behavior, confirming the defect class.

## Code Changes

- `research/shadow_tracking/run.py`
  - Treat missing prior artifacts as `BROKEN_CHAIN` when established `shadow_nav_series.csv` history exists.
  - Preserve legitimate inception when no history exists.
  - Carry deterministic reason code `SHADOW_PRIOR_ARTIFACT_MISSING`.

- `scripts/refresh_shadow_scorecard_artifacts.py`
  - Validate append continuity against the existing CSV chain.
  - Reject schema mismatch.
  - Reject existing-date restatement without explicit recovery workflow.
  - Leave historical rows unchanged on rejection.

- `scripts/send_shadow_cio_report.py`
  - Add NAV history integrity assessment.
  - Detect simultaneous scale reset.
  - Suppress performance windows, rankings, and promotion signals when corrupted.
  - Emit `SHADOW_PERFORMANCE_SUPPRESSED` language in the report.

- `scripts/check_shadow_scorecard_health.py`
  - Add performance-integrity check and reason-code details.
  - Fail strict health when NAV continuity is corrupt.

## Governance Corrections

- `docs/governance/ORCHESTRATOR_CONTEXT.md`
- `docs/governance/fr_active_backlog.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`

Clarified that FR-070 is the highest immediate operational observation priority and FR-069 is the next major architecture workstream.

## Non-Goals

- No production artifact rewrite.
- No VM deploy.
- No broker access.
- No cron change.
- No allocation/model/strategy/order behavior change.
- No Orion/Lyra/FR-063 retirement decision.
