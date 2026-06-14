# 08 Final Summary

## Executive Conclusion

Recovery is blocked and active VM artifacts were not replaced.

The affected dated daily returns from `2026-06-08` through `2026-06-12` are independently reproducible from dated target weights and point-in-time prices. However, the active `shadow_nav_series.csv` cannot be safely reconstructed from the declared `2026-06-05` anchor because the CSV anchor and surrounding rows do not prove a single deterministic convention with the dated recovery inputs.

## Root Cause Update

The immediate corruption remains the `2026-06-09` simultaneous NAV scale reset across all strategies and SPY.

Additional recovery-blocking finding: the active artifacts mix at least two Shadow performance lineages:

- historical/backtest-style NAV scale in `shadow_nav_series.csv` through `2026-06-05`;
- local dated-performance NAV scale beginning at `2026-06-09`;
- no active CSV row for the `2026-06-08` trading day.

## Recovery Eligibility

Decision: `NEEDS_OPERATOR`

No production artifact recovery was performed because:

- the `2026-06-05` anchor is not independently proven under one convention;
- `2026-06-08` is a missing trading row in the active CSV;
- same-day and forward-return conventions coexist under the same `weights_as_of_t` label;
- replacing active artifacts would require an owner-approved convention/restatement decision.

## Validated Daily Returns

Dated `shadow_performance.json` returns for all current Shadow strategies and SPY from `2026-06-08` through `2026-06-12` exactly match independently reconstructed same-day weighted returns from VM price data and dated target weights.

## Safety

- No broker order was submitted.
- No trading workflow was run.
- No execution, broker, allocation, model, strategy, promotion, retirement, or cron behavior changed.
- No secrets were printed or committed.
- VM cron was inspected read-only and left unchanged.
- Original incident backups and hashes remain preserved.

## Required Next Decision

The owner should choose and document the canonical `shadow_nav_series.csv` convention before production recovery:

1. Keep historical/backtest forward-return convention and rebuild/restate affected rows accordingly.
2. Convert the CSV to dated same-day performance convention and restate the full series with manifest.
3. Supersede the legacy CSV with a new versioned artifact and mark old cumulative performance non-decision-grade.

## Owner Decision Received

After this report, the owner approved Option 3 with dated same-day returns as
the canonical operational Shadow observation methodology. Follow-up recovery
work must use `dated_same_day_close_to_close_v1`, preserve the legacy corrupted
CSV as superseded evidence, and replace active artifacts only through a staged
backup/manifest workflow.
