# Price Hydration

Caerus price cache refreshes are artifact-only operations. They must not run
execution, broker submission, strategy promotion, or portfolio construction code.

## Canonical Cache-Only Refresh

Use the lightweight cache-only hydrator for routine post-close refreshes:

```bash
python3 -m scripts.hydrate_price_cache_only
```

This updates the canonical shadow/reporting price cache:

```text
outputs/research/flow_detection_v1/price_panel.parquet
```

and writes hydration status to:

```text
outputs/price_hydration/YYYY-MM-DD/status.json
```

## Fail-Closed Publication

The hydrator distinguishes a partial-symbol response from a provider-wide empty
response. A provider-wide empty response receives three bounded group retries;
if all remain empty, per-symbol fanout is suppressed so one outage cannot create
hundreds of redundant calls.

Downloaded rows are merged into a staged parquet artifact. Publication occurs
only when:

- required current-session and anchor coverage is complete;
- every expected session in each stale symbol's catch-up interval is present;
- no download failure remains; and
- the staged parquet round-trips exactly and has a verified SHA-256 hash.

Only then is the staged file atomically moved over the canonical cache. On any
failure the canonical cache is unchanged and the strict wrapper remains blocked.
The hydration status records group/symbol attempts, the catch-up interval,
missing sessions, and before/staged/canonical hashes under `panel_meta`.

## Full Shadow Runner Boundary

`research.shadow_tracking.run` is for full shadow artifact generation. It builds
signals, strategy snapshots, comparison/evaluation files, performance artifacts,
and feedback-loop artifacts. Do not use it for cache-only hydration.

## Manual Fallback

`scripts/hydrate_shadow_price_cache_vm.sh` remains available as a manual fallback
for a full shadow refresh. It should not be the routine cron path for price
cache hydration.

## Ticker Exceptions

Managed ticker exceptions live in:

```text
data/ticker_exceptions.json
```

The file supports two mechanisms:

- `ignore`: tickers that remain in the universe but are skipped during download.
  Use this when a provider repeatedly returns empty or bad data for a symbol.
- `aliases`: provider-specific ticker mappings. Use this when the universe ticker
  is valid internally but Yahoo requires a different symbol for download.

Ignored tickers are not silently removed. They still appear in hydration metadata
and status artifacts under `ignored_tickers`, so downstream reports can explain
that the symbol was intentionally not hydrated.

Aliases are reported under `aliased_tickers`. The provider symbol is used for
download, then mapped back to the requested ticker in the canonical cache.

Add a ticker only after repeated provider failures, a known delisting/provider
symbol mismatch, or a confirmed Yahoo-specific symbol issue.
