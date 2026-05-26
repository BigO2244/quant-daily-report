# Price Hydration Health

## Purpose

Price hydration health explains whether post-close price cache refresh evidence
is complete enough for shadow artifacts and FR-030 packets to be analytically
useful.

This is an observability layer only. It does not hydrate prices, submit orders,
change cron, alter accounting semantics, or change promotion logic.

## How Hydration Fits FR-030

FR-030 consumes shadow performance and comparison artifacts. Those artifacts
depend on current price cache data. If price hydration is missing, stale, or
partial, shadow artifacts can report `NO_DATA`, zero strategy rows, or stale
performance surfaces.

In that state, Orion should block packet generation by default. The operator
should inspect hydration health before forcing an incomplete packet.

## Expected Timeline

Routine post-close hydration is expected after the market close window and
before nightly shadow review. If the operator checks too early, the correct
interpretation may be `WAITING_FOR_POST_CLOSE`.

If the hydration window has passed and the trade date remains stale or missing,
the issue is no longer just timing. It should be treated as stale-but-recoverable
or structurally broken depending on available status evidence.

The normal same-day lifecycle is:

1. Morning shadow artifacts may show stale or no-data state because same-day
   close data is not available yet.
2. Post-close hydration runs after the scheduled 18:30 ET window.
3. Shadow artifacts refresh against the hydrated cache.
4. FR-030 / Orion source readiness can become `READY`.

Before 18:30 ET on a trading day, missing same-day hydration evidence is not by
itself a failure if the cache covers the latest completed trading day.

## Status Vocabulary

`PRICE_CACHE_STALE` means shadow artifacts were generated from a cache that did
not cover the trade date. The packet renderer is not the cause.

`PARTIAL` means hydration evidence exists, but one or more expected symbols are
missing or the status artifact explicitly reports partial completion.

`STALE_BUT_RECOVERABLE` means the hydration window has passed and the last
known cache date is behind the expected trade date, or the expected trade-date
status is missing while earlier successful hydration evidence exists.

`WAITING_FOR_POST_CLOSE` means same-day hydration evidence is not present, the
scheduled hydration window has not occurred yet, and the cache still covers the
latest completed trading day. This is expected before the post-close refresh.
If it persists after the expected post-close window, escalate as stale or
structurally broken.

`STRUCTURALLY_BROKEN` means the diagnostic cannot parse the status artifact or
the available evidence is inconsistent enough that operator inspection is
required.

## Operator Guidance

Run:

```text
python3 -m scripts.research.check_price_hydration_health --latest --markdown
python3 -m scripts.research.check_price_hydration_health --trade-date YYYY-MM-DD --json
```

If hydration is ready, rerun Orion.command normally.

If hydration is stale or partial, wait for or run the approved hydration
workflow separately, then refresh shadow artifacts before using FR-030.

Do not force an incomplete packet for research interpretation. Use forced
incomplete packets only to diagnose source-readiness behavior.

## What Operators Should Look At

- `hydration_status`
- `cache_max_date`
- `stale_days`
- `hydration_window_passed`
- `hydration_state_classification`
- `symbols_missing_count`
- `missing_symbols_sample`
- `last_successful_hydration`
- `recommended_next_action`

These fields explain whether the system is simply early, stale but recoverable,
partial, or structurally broken.
