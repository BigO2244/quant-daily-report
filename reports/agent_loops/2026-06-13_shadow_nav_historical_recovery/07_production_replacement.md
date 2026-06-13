# 07 Production Replacement

Production replacement was not performed.

## Active Artifact Status

- Active `outputs/shadow_candidates/performance/shadow_nav_series.csv` remained unchanged.
- Active `outputs/shadow_candidates/performance/shadow_summary.json` remained unchanged.
- Incident backup remained unchanged.
- No second pre-replacement backup was required because no replacement was attempted.

## Hashes

| File | SHA-256 | Status |
|---|---|---|
| `outputs/shadow_candidates/performance/shadow_nav_series.csv` | `5d59c987c07198c590a287e189a1224f2f783ba8bd55d5fdcb39d1de9e84dc1f` | unchanged/corrupt |
| `outputs/shadow_candidates/performance/shadow_summary.json` | `86a793550a73db81c8dedfe9c96618f4a08a788a150f1354c6542b5a6a65d67a` | unchanged/corrupt |
| `outputs/recovery_backups/shadow_nav_incident_20260613T181114Z/evidence_manifest.json` | `fe69dddbb3845066ba65fe118a1a9eaf7622974a763a1f0b4d4f98f377b1805c` | preserved |

## Final Active Health

The active health check remained fail-closed before recovery:

- `scorecard_data_health`: `Fresh but corrupt`
- `performance_integrity.status`: `CORRUPT`
- `performance_integrity.reason_code`: `SHADOW_NAV_CHAIN_RESET`
- `performance_integrity.offending_date`: `2026-06-09`

Because production artifacts were not replaced, this remains the expected safe state.
