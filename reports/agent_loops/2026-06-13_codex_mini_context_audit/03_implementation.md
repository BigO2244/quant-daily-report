# Implementation Report

## Shadow NAV Continuity

Implemented fail-closed behavior for established chains:

- If prior dated performance artifact is missing while `performance/shadow_nav_series.csv` already has history, `build_shadow_performance_payload()` returns `BROKEN_CHAIN`.
- Reason code: `SHADOW_PRIOR_ARTIFACT_MISSING`.
- Legitimate inception still starts from `1.0` only when no established NAV history exists.

Implemented append validation:

- Existing date returns `REJECTED` / `SHADOW_EXISTING_DATE_RESTATEMENT_BLOCKED`.
- Missing/non-numeric CSV or performance fields return `REJECTED` / `SHADOW_NAV_SCHEMA_MISMATCH`.
- Prior CSV NAV must match reported `previous_nav`.
- Candidate NAV must equal prior CSV NAV compounded by reported `daily_return`.
- Rejections preserve existing file bytes.

## Scorecard Reporting

Implemented NAV integrity assessment in the scorecard report path:

- Detects simultaneous implausible scale resets across model/SPY columns.
- Marks performance integrity as `CORRUPT` with `SHADOW_NAV_CHAIN_RESET`.
- When corrupt, suppresses daily, seven-day, YTD, excess-vs-SPY, ranking, and promotion output.
- CIO takeaway states that performance is unavailable because of artifact corruption.

## Scorecard Health

Health payload now includes:

- `performance_integrity.status`
- `performance_integrity.reason_code`
- `performance_integrity.detail`
- `performance_integrity.offending_date`

Strict health fails when integrity is corrupt, even if freshness otherwise passes.

## Historical Artifact Recovery

No local historical rewrite was performed.

Reason:

- The local preserved `shadow_nav_series.csv` is valid through 2026-06-05.
- The reported 2026-06-12 corrupted dated artifacts are not present in the checkout.
- Rewriting absent production artifacts locally would invent lineage and violate the artifact recovery contract.

Owner-gated production recovery should start from preserved VM artifacts, identify the last valid row, and recompound from validated daily returns only after backup and recovery manifests are written.

## Governance

Priority language was corrected without changing statuses:

- FR-070: `DEPLOYED_OBSERVING`, highest immediate operational observation priority.
- FR-069: next major architecture workstream, research-only.
- FR-063: discrepancy reported; no retirement or silent status change.
- Orion/Lyra: continued evaluation; no retirement or rename.
