# Parquet Scaling Review

## Scope

This review covers FR-007. It is intentionally advisory and does not replace
the current canonical single-file price panel:

```text
outputs/research/flow_detection_v1/price_panel.parquet
```

## Current Assessment

The current full parquet read/write path remains acceptable for the present
universe size and daily cadence. Existing guardrails already keep price cache
hydration artifact-only and separate from order execution.

## Recommendation

Defer storage-layout changes until repeated runtime or memory pressure is
observed. The preferred next step is metadata/index sidecars before any
partitioned storage migration.

Candidate order:

1. Advisory coverage sidecar for max date, row count, ticker count, aliases,
   ignored tickers, provider, and generation timestamp.
2. Rolling/index sidecars for reporting reads that do not require full panel
   inspection.
3. Partitioning by date or ticker only after sidecars are insufficient.

## Rollback

No runtime behavior changed. Rollback is to keep the current single parquet as
the only canonical artifact and ignore this review document.

## Promotion Status

FR-007 is reviewed and should remain staged. It is not recommended for an
immediate storage migration.
