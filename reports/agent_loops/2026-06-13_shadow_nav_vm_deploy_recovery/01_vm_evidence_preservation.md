# VM Evidence Preservation

## VM Inspection

- Hostname: `alpha-stack-scheduler`
- Inspection timestamp: `2026-06-13T14:09:54-04:00`
- Repository path: `/home/brettolson/quant-daily-report`
- Branch before deploy: `main`
- VM HEAD before deploy: `e4abc6044dc2f0bd63c4ce683b3155f19330f051`
- `origin/main` after fetch before deploy: `a1ddc68351daccc0be7ff2f131a817479ef178d9`
- Tracked working tree before deploy: clean

Cron was read with `crontab -l`; no cron changes were made.

## Evidence Backup

- Backup root: `/home/brettolson/quant-daily-report/outputs/recovery_backups/shadow_nav_incident_20260613T181114Z`
- Manifest: `outputs/recovery_backups/shadow_nav_incident_20260613T181114Z/evidence_manifest.json`
- Manifest SHA-256: `fe69dddbb3845066ba65fe118a1a9eaf7622974a763a1f0b4d4f98f377b1805c`
- Preserved file count: 240
- Missing requested paths:
  - `outputs/price_hydration/2026-06-06`
  - `outputs/shadow_candidates/2026-06-06`

Key preserved hashes:

| Path | SHA-256 | Bytes | Modified UTC |
|---|---:|---:|---|
| `outputs/shadow_candidates/performance/shadow_nav_series.csv` | `5d59c987c07198c590a287e189a1224f2f783ba8bd55d5fdcb39d1de9e84dc1f` | 257471 | `2026-06-12T22:30:42.157034+00:00` |
| `outputs/shadow_candidates/performance/shadow_summary.json` | `86a793550a73db81c8dedfe9c96618f4a08a788a150f1354c6542b5a6a65d67a` | 4745 | `2026-06-09T00:24:29.302212+00:00` |

The compact manifest copied into this repository is:

- `reports/agent_loops/2026-06-13_shadow_nav_vm_deploy_recovery/evidence_manifest.json`

The full backup remains on the VM and was not committed to source control.
