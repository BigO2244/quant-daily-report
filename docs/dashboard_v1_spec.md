# Dashboard V1 Spec

## Purpose

Define the minimum truthful dashboard for the restart.

V1 is intentionally narrow. It exists to answer four questions correctly before
any richer operator or attribution views are added:

1. What positions do we currently hold?
2. What is current NAV?
3. What trades were made today?
4. What is the historical performance curve?

If V1 cannot answer those correctly from canonical artifacts, it should degrade
visibly or fail to publish rather than guess.

Exact field-to-artifact mappings live in [dashboard_v1_source_map.md](/Users/brettolson/Documents/Caerus/quant-daily-report-main/docs/dashboard_v1_source_map.md).

## Non-Goals

V1 does not include:

- attribution
- sleeve contribution
- regime display
- risk dashboards
- trade quality analytics beyond raw fills
- intended-vs-actual construction analysis
- blended broker plus governed headline metrics

Those are explicitly deferred until the basic truth surface is stable.

## Design Rules

- One section, one primary source of truth.
- No silent blending across broker, run, and derived sources.
- Every section must declare `as_of`, `source_type`, `trust_level`, and
  `is_stale`.
- If a primary source is missing, render that section unavailable.
- If a fallback is used, say so plainly in the artifact and UI.
- If validation fails, do not publish a clean `ok` artifact.

## V1 Scope

The V1 artifact contains only four sections:

- `positions`
- `nav`
- `trades_today`
- `performance_history`

The UI should be correspondingly simple:

- current positions table
- current NAV summary
- today's fills table
- historical NAV vs SPY chart and return series

## Artifact Schema

```json
{
  "schema_version": "dashboard-v1",
  "generated_at": "2026-04-21T15:30:00Z",
  "report_date": "2026-04-21",
  "environment": "paper",
  "status": {
    "level": "ok",
    "summary": "All primary sections built from canonical sources.",
    "errors": [],
    "warnings": []
  },
  "sections": {
    "positions": {},
    "nav": {},
    "trades_today": {},
    "performance_history": {}
  },
  "sources": [],
  "validation": {}
}
```

## Section Contract

### `sections.positions`

Purpose:

- show the current live book

Primary source:

- broker-authoritative positions snapshot artifact

Required fields:

```json
{
  "as_of": "2026-04-21T10:01:00Z",
  "source_type": "broker_snapshot",
  "trust_level": "canonical",
  "is_stale": false,
  "summary": {
    "positions_count": 8,
    "gross_market_value": 8421.55,
    "net_market_value": 8421.55,
    "cash": 1149.02,
    "largest_position_weight": 0.182,
    "top5_concentration": 0.671
  },
  "rows": [
    {
      "ticker": "NVDA",
      "side": "long",
      "qty": 8,
      "avg_entry_price": 108.42,
      "last_price": 110.10,
      "market_value": 880.80,
      "cost_basis": 867.36,
      "unrealized_pnl": 13.44,
      "unrealized_pnl_pct": 0.0155,
      "weight": 0.092
    }
  ]
}
```

Rules:

- rows sorted by `market_value` descending
- weights computed against current NAV
- if cost basis fields are not trustworthy, set them to `null`
- no sectors, sleeves, or regime fields in V1

### `sections.nav`

Purpose:

- show current account truth

Primary source:

- broker-authoritative account snapshot artifact

Required fields:

```json
{
  "as_of": "2026-04-21T10:01:00Z",
  "source_type": "broker_account",
  "trust_level": "canonical",
  "is_stale": false,
  "equity": 9570.57,
  "cash": 1149.02,
  "long_market_value": 8421.55,
  "short_market_value": 0.0,
  "gross_exposure": 0.8801,
  "net_exposure": 0.8801,
  "buying_power": 2298.04,
  "day_pnl": -68.53,
  "day_return": -0.0071
}
```

Rules:

- `equity` is the headline NAV
- if `buying_power` is absent from source, set it to `null`
- do not infer headline account values from positions or performance history when
  the account source is missing

### `sections.trades_today`

Purpose:

- show what actually filled on the report date

Primary source:

- execution fills artifact from the actual run

Required fields:

```json
{
  "as_of": "2026-04-21T10:00:30Z",
  "source_type": "execution_fills",
  "trust_level": "canonical",
  "is_stale": false,
  "summary": {
    "fills_count": 6,
    "buy_count": 4,
    "sell_count": 2,
    "buy_notional": 2140.22,
    "sell_notional": 1852.11
  },
  "rows": [
    {
      "filled_at": "2026-04-21T09:35:12-04:00",
      "ticker": "AVGO",
      "side": "buy",
      "qty": 2,
      "fill_price": 1310.55,
      "notional": 2621.10,
      "order_id": "abc123",
      "client_order_id": "run_2026_04_21_avgo_01",
      "source_execution_id": "fill_789"
    }
  ]
}
```

Rules:

- only filled trades
- no intended orders, recommendations, or queued orders
- no derived slippage or realized P&L in V1
- rows sorted by `filled_at` ascending

### `sections.performance_history`

Purpose:

- show governed historical performance and SPY-relative context

Primary source:

- governed NAV/performance history artifact

Required fields:

```json
{
  "as_of": "2026-04-21",
  "source_type": "governed_nav_history",
  "trust_level": "canonical",
  "is_stale": false,
  "summary": {
    "inception_date": "2026-04-09",
    "latest_nav": 9570.57,
    "since_inception_return": -0.0043,
    "spy_since_inception_return": 0.0112,
    "excess_since_inception_return": -0.0155,
    "max_drawdown": -0.0261
  },
  "series": {
    "nav": [
      { "date": "2026-04-09", "value": 10000.00 }
    ],
    "daily_return": [
      { "date": "2026-04-10", "value": -0.00449 }
    ],
    "spy_close": [
      { "date": "2026-04-09", "value": 508.22 }
    ],
    "nav_indexed": [
      { "date": "2026-04-09", "value": 100.0 }
    ],
    "spy_indexed": [
      { "date": "2026-04-09", "value": 100.0 }
    ],
    "excess_return_cumulative": [
      { "date": "2026-04-10", "value": -0.0062 }
    ],
    "drawdown": [
      { "date": "2026-04-10", "value": -0.00449 }
    ]
  }
}
```

Rules:

- `nav` is raw dollar NAV history
- `nav_indexed` and `spy_indexed` share a common aligned inception base of 100
- SPY-relative calculations use aligned dates only
- do not reconstruct history from today's positions

## Source Contract

Every consumed source must be recorded in `sources`.

```json
[
  {
    "section": "positions",
    "label": "broker positions snapshot",
    "path": "outputs/broker/broker_snapshot_latest.json",
    "as_of": "2026-04-21T10:01:00Z",
    "source_type": "broker_snapshot",
    "trust_level": "canonical",
    "used": true
  }
]
```

Required fields:

- `section`
- `label`
- `path`
- `as_of`
- `source_type`
- `trust_level`
- `used`

## Validation Contract

Validation is an automated step run by the builder before publish. It is not
just a manual review checklist.

The builder must:

1. load the primary sources
2. build the V1 artifact
3. run the checks below
4. set `status.level` from the results
5. refuse to publish a clean `ok` artifact when blocking checks fail

### Validation Payload

```json
{
  "checks": [
    {
      "name": "positions_sum_matches_nav",
      "status": "pass",
      "severity": "blocking",
      "detail": "sum(position market values) + cash is within tolerance of equity",
      "tolerance": 1.0
    }
  ]
}
```

Allowed values:

- `status`: `pass|warn|fail|not_run`
- `severity`: `blocking|non_blocking`

### Blocking Checks

These failures prevent a clean publish.

#### `positions_source_present`

- verify the canonical positions source exists and is parseable
- fail if missing or malformed

#### `nav_source_present`

- verify the canonical NAV/account source exists and is parseable
- fail if missing or malformed

#### `trades_source_present`

- verify the canonical fills source exists and is parseable
- warn rather than fail only on legitimate no-trade days where the source
  explicitly confirms zero fills

#### `performance_source_present`

- verify governed historical NAV/performance source exists and is parseable
- fail if missing or malformed

#### `positions_sum_matches_nav`

- compare `sum(rows.market_value) + nav.cash` against `nav.equity`
- default tolerance: $1.00
- fail if outside tolerance

#### `positions_weights_sum_reasonable`

- verify the sum of displayed position weights is consistent with exposure
- fail if materially inconsistent with market value and NAV

#### `history_latest_nav_matches_nav_section`

- verify latest `performance_history.series.nav.value` matches `nav.equity`
- use a tight tolerance when both claim to represent the same report date
- fail if the same-date values diverge materially

#### `trades_are_report_date_only`

- verify all displayed fills fall on `report_date` in `America/New_York`
- fail if out-of-date fills are mixed into the table

#### `performance_series_monotonic_dates`

- verify all historical series are strictly date-ordered with no duplicates
- fail on duplicate or descending dates

#### `spy_dates_aligned`

- verify excess-return and indexed comparison series only use dates present in
  both NAV and SPY source series
- fail if excess results are produced from unaligned dates

### Non-Blocking Checks

These mark the artifact degraded but do not prevent publish.

#### `positions_timestamp_fresh`

- warn if `positions.as_of` is stale relative to build time

#### `nav_timestamp_fresh`

- warn if `nav.as_of` is stale relative to build time

#### `trades_timestamp_fresh`

- warn if fills source is stale or not final for the report date

#### `performance_timestamp_fresh`

- warn if historical series stops before the report date

#### `buying_power_present`

- warn only; absence should not block publish

## Status Rules

`status.level` is derived from validation results.

- `ok`: all blocking checks pass, no significant freshness issues
- `warning`: all blocking checks pass, at least one non-blocking check warns
- `error`: any blocking check fails

Suggested publish behavior:

- `ok`: publish normally
- `warning`: publish with a degraded banner
- `error`: either do not publish or publish only an explicit error artifact with
  failed checks and no misleading section data

## Fallback Policy

V1 should be strict.

### `positions`

- primary: broker-authoritative positions artifact
- fallback: none

### `nav`

- primary: broker-authoritative account artifact
- fallback: none

### `trades_today`

- primary: execution fills artifact
- fallback: broker fills only if formally designated canonical later

### `performance_history`

- primary: governed NAV/performance artifact
- fallback: canonical performance csv only if explicitly designated equivalent

If fallback is used:

- declare it in `source_type`
- reduce `trust_level`
- add a visible warning

## Field Conventions

- money fields are numeric USD values
- returns, exposures, and weights are decimals, not formatted percentages
- timestamps are ISO 8601
- dates are `YYYY-MM-DD`
- unavailable fields should be `null`, never guessed strings

## Build Sequence

The implementation order for the restart should be:

1. map every V1 field to one exact source path and extraction function
2. implement the validation contract
3. build the V1 artifact from those canonical sources
4. render a simple UI from the validated artifact
5. add richer panels only after repeated known-date verification passes
