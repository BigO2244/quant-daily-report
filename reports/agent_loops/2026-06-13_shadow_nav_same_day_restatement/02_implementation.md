# 02 Implementation

Implemented additive recovery utility:

- `scripts/restate_shadow_nav_same_day.py`

Behavior:

- Reads dated `outputs/shadow_candidates/<date>/shadow_performance.json`.
- Reads dated strategy target weights.
- Reads PIT price inputs from
  `outputs/research/flow_detection_v1/price_panel.parquet`.
- Reconstructs same-day close-to-close daily returns.
- Blocks if any recorded daily return cannot be reconstructed.
- Skips non-trading `NO_DATA` artifacts only when no price returns exist for
  that date.
- Writes staged `performance/shadow_nav_series.csv`.
- Writes staged `performance/shadow_summary.json`.
- Writes `daily_return_validation.json`.
- Writes `recovery_manifest.json`.
- On `--replace-active`, verifies expected current hashes, creates a
  pre-replacement backup, atomically replaces the active NAV and summary files,
  and writes `shadow_nav_restatement_manifest.json`.

Tests:

- `Tests/test_shadow_nav_same_day_restatement.py`

Scope:

- Reporting/artifact recovery only.
- No trading, broker, allocation, execution, strategy, model, promotion,
  retirement, or cron behavior changed.
