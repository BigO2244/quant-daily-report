# 2026-06-12 Shadow NAV Scorecard Corruption

## Incident

The Daily Model Scorecard through 2026-06-12 reportedly emitted economically impossible values:

- Polaris: -92.75% YTD
- Orion: -97.76% YTD
- Lyra: -97.66% YTD
- SPY: -72.25% YTD
- SPY seven-day return: -74.86%
- SPY daily return: +0.54%

The shared collapse across all models and SPY indicates an artifact-chain discontinuity rather than a valid market or strategy result.

## Classification

- Type: artifact corruption / latent code defect.
- Confidence: `HIGH_CONFIDENCE`.
- Code defect: Shadow incremental refresh could restart an established NAV chain at `1.0`.
- Artifact corruption: reported production scorecard was contaminated.
- Deployment mismatch: unproven; VM artifact/log evidence is required.
- Governance drift: priority docs incorrectly made FR-069 primary over FR-070 immediate observation; corrected.
- Regression from recent FR-069 Mini work: not supported by evidence.

## Evidence

Evidence manifest:

- `reports/agent_loops/2026-06-13_codex_mini_context_audit/audit_manifest.json`

Local preserved NAV:

- Path: `outputs/shadow_candidates/performance/shadow_nav_series.csv`
- Rows: 3,125
- Date range: 2014-01-02 through 2026-06-05
- Local continuity status: valid through 2026-06-05.

Absent local incident artifacts:

- `outputs/shadow_candidates/2026-06-12`
- `outputs/shadow_candidates/2026-06-11`
- `outputs/shadow_candidates/2026-06-10`
- `outputs/shadow_candidates/2026-06-09`

## Root Cause

Pre-patch behavior:

1. `load_prior_shadow_performance()` returned `NO_PRIOR` for missing previous dated artifact directories.
2. `build_shadow_performance_payload()` treated `NO_PRIOR` as inception and set prior NAV to `1.0`.
3. `_append_nav_series()` accepted the resulting row into the established CSV without validating continuity or blocking restatement.
4. The scorecard could then compute plausible daily returns from the restarted local chain while seven-day and YTD windows collapsed.

## Fix

Implemented:

- Established chain + missing prior artifact now fails closed as `BROKEN_CHAIN` / `SHADOW_PRIOR_ARTIFACT_MISSING`.
- NAV appends validate schema, previous NAV, daily return, and candidate NAV against the prior CSV row.
- Existing-date overwrites are blocked as `SHADOW_EXISTING_DATE_RESTATEMENT_BLOCKED`.
- Scorecard integrity detects scale resets and suppresses performance windows/rankings/promotion signals.
- Health check reports performance integrity and fails strict mode on corrupt chains.

## Recovery

No production artifact recovery was performed by this task.

Required if production artifacts remain corrupt:

- Preserve VM evidence and hashes.
- Identify last valid production row.
- Validate daily returns independently from preserved dated artifacts.
- Recompound NAV forward from the valid anchor.
- Write a recovery/restatement manifest before replacing any production artifact.

## VM Evidence and Deploy Update — 2026-06-13

VM evidence was preserved before code deployment.

- VM original SHA: `e4abc6044dc2f0bd63c4ce683b3155f19330f051`
- Deployed fix SHA: `491aef6a70e92ff4724f82445324c0d19ccccca9`
- Evidence backup root: `outputs/recovery_backups/shadow_nav_incident_20260613T181114Z`
- Evidence manifest SHA-256: `fe69dddbb3845066ba65fe118a1a9eaf7622974a763a1f0b4d4f98f377b1805c`
- Preserved files: 240

VM artifact findings:

- `shadow_nav_series.csv` rows: 3,129
- Date range: `2014-01-02` through `2026-06-12`
- Last valid CSV row: `2026-06-05`
- First invalid CSV row: `2026-06-09`
- Discontinuity: simultaneous model and SPY NAV reset on `2026-06-09`
- Reset ratios versus `2026-06-05`: Polaris `0.03978521420421215`, Orion `0.010222370872810641`, Lyra `0.010548005695364534`, SPY `0.2547091139110989`

Post-deploy validation:

- VM `HEAD` equals `origin/main` at `491aef6a70e92ff4724f82445324c0d19ccccca9`.
- VM targeted Shadow tests passed: `46 passed`.
- Scorecard dry-run now reports `Fresh but corrupt`.
- Rankings, cumulative returns, and promotion signals are suppressed.
- Strict health reports `FAIL` with `performance_integrity.reason_code=SHADOW_NAV_CHAIN_RESET`.

Recovery status:

- No production artifact recovery was performed.
- Recovery did not pass the daily-return validation gate during this deploy task.
- A future recovery must independently recompute and validate daily returns from dated holdings/weights and price inputs before recompounding NAV from the `2026-06-05` anchor.

## Governance

- FR-070 remains `DEPLOYED_OBSERVING` and highest immediate operational observation priority.
- FR-069 remains research-only and the next major architecture workstream.
- Orion and Lyra remain under evaluation.
- FR-063 was not retired or reclassified.
