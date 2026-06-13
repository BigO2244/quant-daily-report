# Recovery Execution

No production artifact recovery was executed.

## Reason

The task authorizes recovery only when lineage can be proven deterministically. The VM evidence proves the NAV chain reset on `2026-06-09`, but the deployment pass did not independently validate all daily returns required to recompound from the `2026-06-05` anchor.

## Production Artifact State

Active Shadow artifacts remain preserved and unchanged except for the pre-deploy evidence backup written under:

```text
outputs/recovery_backups/shadow_nav_incident_20260613T181114Z/
```

The deployed code prevents decision-use of the corrupt series by reporting:

- `Fresh but corrupt`
- `SHADOW_NAV_CHAIN_RESET`
- suppressed rankings
- suppressed daily / seven-day / YTD / excess metrics
- suppressed promotion signals

## Recovery Manifest

No `recovery_manifest.json` was created because no repaired artifacts were staged or installed.
