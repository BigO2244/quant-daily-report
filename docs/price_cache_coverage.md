# Price Cache Coverage

## Purpose

FR-002 introduces a read-only advisory preview for price cache coverage
sidecar semantics.

The diagnostic inspects the existing parquet cache and universe metadata. It
does not hydrate prices, mutate the cache, write sidecars, refresh shadow
artifacts, change cron, alter execution, or affect promotion logic.

## Diagnostic Command

```text
python3 -m scripts.research.check_price_cache_coverage --trade-date YYYY-MM-DD --markdown
python3 -m scripts.research.check_price_cache_coverage --trade-date YYYY-MM-DD --json
python3 -m scripts.research.check_price_cache_coverage --trade-date YYYY-MM-DD --strict
```

`--strict` exits nonzero unless coverage status is `READY`.

## Coverage States

| State | Meaning | Operator Interpretation |
|---|---|---|
| `READY` | Cache exists, expected symbols are present, and symbol coverage reaches the requested trade date. | Coverage is sufficient for advisory review. |
| `INCOMPLETE` | Cache exists but some expected symbols are missing or stale. | Inspect hydration health before using shadow artifacts. |
| `STALE` | Cache max date lags the requested trade date. | Run or wait for the approved hydration workflow. |
| `MISSING` | The parquet cache is absent. | Hydration has not produced the expected cache artifact. |
| `UNKNOWN` | Universe or cache could not be read. | Inspect local environment and parquet dependencies. |

## Reported Fields

The diagnostic reports:

- cache path and existence;
- row count;
- cache min and max date;
- expected universe symbol count;
- present symbol count;
- missing symbol count and sample;
- stale symbol count and sample;
- ignored and aliased ticker metadata;
- runtime effect, always `none`;
- sidecar status, currently `advisory_preview_only`.

## Relationship To Hydration Health

Price hydration health explains whether post-close hydration completed. Price
cache coverage explains what the cache contains after whatever hydration state
exists.

Use them together:

1. Run hydration health to classify the hydration state.
2. Run cache coverage to inspect symbol/date coverage.
3. Use FR-030 / Orion only when source readiness is `READY`.

## Future Sidecar Requirements

A future generated sidecar may persist this coverage summary beside the parquet
cache, but that is not implemented here. Before persistence is added, Caerus
should define:

- write timing;
- producer ownership;
- retention class;
- freshness semantics;
- rollback behavior;
- tests proving the sidecar cannot become more authoritative than the parquet.

Until then, this diagnostic is read-only observability.
