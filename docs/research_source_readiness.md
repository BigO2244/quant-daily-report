# Research Source Readiness

## Purpose

Research source readiness explains whether FR-030 / Orion has enough fresh
post-close evidence to produce a usable research packet.

This layer is read-only observability. It does not hydrate prices, refresh
shadow artifacts, submit orders, change timing semantics, change accounting
semantics, or alter promotion logic.

## Readiness States

`READY` means:

- `shadow_performance.json` exists for the trade date;
- `shadow_performance.data_status` is `OK`;
- `comparison.json` exists for the trade date;
- `comparison.status` is `OK`;
- `comparison.strategies` is non-empty;
- price hydration status exists and is `OK`;
- the reported cache max date covers the trade date when that field is present.

`INCOMPLETE` means one or more expected inputs are missing, stale, or explicitly
reported as no-data. Orion should block packet generation by default in this
state because strategy interpretation would be misleading.

`UNKNOWN` means the diagnostic cannot determine a target trade date or source
state. Operators should inspect shadow generation before using FR-030.

## Common Incomplete Causes

`PRICE_CACHE_STALE` means the shadow artifact was generated from stale price
cache inputs. The packet renderer is not the source of the problem. The source
artifacts need post-close hydration and shadow refresh before research review is
usable.

`comparison.status = NO_DATA` means the strategy comparison surface did not
produce usable strategy rows for the trade date.

`strategy_count = 0` means no strategy comparison payload is available. In this
state, ranking and exposure-adjusted interpretation should not be used.

`price_hydration_status = MISSING` means the expected
`outputs/price_hydration/<TRADE_DATE>/status.json` evidence was not present.
Use `docs/price_hydration_health.md` and
`scripts/research/check_price_hydration_health.py` to inspect cache lag,
missing symbols, partial hydration, and the last successful hydration context.

## Operator Workflow

Use the diagnostic before forcing an incomplete packet:

```text
python3 -m scripts.research.check_research_source_readiness --latest
python3 -m scripts.research.check_research_source_readiness --trade-date YYYY-MM-DD --markdown
python3 -m scripts.research.check_research_source_readiness --trade-date YYYY-MM-DD --json --strict
python3 -m scripts.research.check_price_hydration_health --trade-date YYYY-MM-DD --markdown
```

If readiness is `READY`, run Orion.command normally.

If readiness is `INCOMPLETE`, wait for post-close hydration and shadow artifact
refresh, then rerun Orion.command. Only use `ORION_ALLOW_INCOMPLETE_PACKET=1`
when diagnosing source readiness. Incomplete packets are advisory context only.

## Why Orion Does Not Hydrate Automatically

Hydration changes source artifacts and can have operational timing implications.
Orion is a manual operator review launcher, not a scheduler or repair tool. It
does not auto-refresh prices by default so source readiness remains explicit,
auditable, and under operator control.

Approved hydration workflows should remain separate from packet rendering and
operator review.

## Relationship To FR-030

FR-030 consumes source artifacts. It does not make stale source artifacts fresh.

When source readiness is incomplete, FR-030 should explain why the packet is
not analytically usable and what must happen next. The remaining bottleneck is
post-close source readiness, not packet formatting.
