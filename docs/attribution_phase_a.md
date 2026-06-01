# Attribution Phase A

Attribution Phase A is a read-only research artifact builder for deterministic
position-level PnL contribution. It uses existing on-disk holdings/weight
snapshots and local price matrices only; it does not call brokers, submit
orders, or alter execution behavior.

Run manually:

```bash
python3 scripts/build_position_attribution.py --date YYYY-MM-DD
```

Outputs are written under:

```text
outputs/attribution/YYYY-MM-DD/
  attribution_summary.json
  position_attribution.json
  top_contributors.json
  top_detractors.json
```

Price freshness contract:

- The canonical approved close-price source is
  `outputs/research/flow_detection_v1/price_panel.parquet`, hydrated by
  `scripts/hydrate_price_cache_only.py`.
- Attribution does not fetch live data. It consumes the canonical cache when
  present, then falls back to legacy local wide CSV matrices.
- `attribution_summary.json` records `price_source`, `price_source_max_date`,
  `attribution_date`, `is_price_source_fresh`, `freshness_lag_days`, and
  `freshness_reason_codes`.
- A price source is fresh when its max date is on or after the attribution date.
  Stale sources emit `price_source_stale` and missing per-position prices emit
  `missing_start_price` or `missing_end_price`.

Confidence is `HIGH` only when every analyzed position has weight, start price,
and end price from a fresh source. Partial price coverage reports `MEDIUM`;
missing sources or empty holdings report `LOW` with reason codes.
