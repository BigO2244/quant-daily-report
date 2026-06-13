# 03 Daily Return Reconstruction

## Method

For each audited date:

1. Load dated target weights from `outputs/shadow_candidates/<date>/<strategy>.json`.
2. Load point-in-time closes from `outputs/research/flow_detection_v1/price_panel.parquet`.
3. Compute ticker close-to-close return for `previous_trading_day -> date`.
4. Multiply by same-date target weights.
5. Compare to dated `shadow_performance.json`.
6. Compare active CSV ratios separately to detect lineage or convention mismatch.

No broker, execution, allocation, model, promotion, retirement, or cron path was invoked.

## Affected Reconstruction Status

| Date | Strategy | Existing Daily Return | Reconstructed Return | Status |
|---|---|---:|---:|---|
| 2026-06-08 | caerus_polaris | 0.0559932344 | 0.0559932344 | EXACT_MATCH |
| 2026-06-08 | caerus_orion | 0.0689440713 | 0.0689440713 | EXACT_MATCH |
| 2026-06-08 | caerus_lyra | 0.0662008357 | 0.0662008357 | EXACT_MATCH |
| 2026-06-08 | spy_benchmark | 0.0022642301 | 0.0022642301 | EXACT_MATCH |
| 2026-06-09 | caerus_polaris | -0.0216485092 | -0.0216485092 | EXACT_MATCH |
| 2026-06-09 | caerus_orion | -0.0159252315 | -0.0159252315 | EXACT_MATCH |
| 2026-06-09 | caerus_lyra | -0.0320993089 | -0.0320993089 | EXACT_MATCH |
| 2026-06-09 | spy_benchmark | -0.0029355036 | -0.0029355036 | EXACT_MATCH |
| 2026-06-10 | caerus_polaris | -0.0323008847 | -0.0323008847 | EXACT_MATCH |
| 2026-06-10 | caerus_orion | -0.0320830411 | -0.0320830411 | EXACT_MATCH |
| 2026-06-10 | caerus_lyra | -0.0354408238 | -0.0354408238 | EXACT_MATCH |
| 2026-06-10 | spy_benchmark | -0.0157655455 | -0.0157655455 | EXACT_MATCH |
| 2026-06-11 | caerus_polaris | 0.0815312959 | 0.0815312959 | EXACT_MATCH |
| 2026-06-11 | caerus_orion | 0.0959288759 | 0.0959288759 | EXACT_MATCH |
| 2026-06-11 | caerus_lyra | 0.0805873413 | 0.0805873413 | EXACT_MATCH |
| 2026-06-11 | spy_benchmark | 0.0169968394 | 0.0169968394 | EXACT_MATCH |
| 2026-06-12 | caerus_polaris | 0.0312363251 | 0.0312363251 | EXACT_MATCH |
| 2026-06-12 | caerus_orion | 0.0397285140 | 0.0397285140 | EXACT_MATCH |
| 2026-06-12 | caerus_lyra | 0.0403637187 | 0.0403637187 | EXACT_MATCH |
| 2026-06-12 | spy_benchmark | 0.0054082495 | 0.0054082495 | EXACT_MATCH |

## CSV Lineage Finding

The active CSV has no row for `2026-06-08` even though:

- `2026-06-08` is a trading day in the VM price panel.
- `outputs/shadow_candidates/2026-06-08/` exists.
- `shadow_performance.json` for `2026-06-08` is present and reconstructable.

The active CSV then resumes at `2026-06-09` on the local dated-performance NAV scale:

| Date | Strategy | CSV Ratio vs Prior CSV Row | Reconstructed Same-Day Return |
|---|---|---:|---:|
| 2026-06-09 | caerus_polaris | -0.9602147858 | -0.0216485092 |
| 2026-06-09 | caerus_orion | -0.9897776291 | -0.0159252315 |
| 2026-06-09 | caerus_lyra | -0.9894519943 | -0.0320993089 |
| 2026-06-09 | spy_benchmark | -0.7452908861 | -0.0029355036 |

Rows `2026-06-10` through `2026-06-12` then match the same-day dated chain ratios, but only after the invalid 2026-06-09 scale reset.

## Conclusion

The affected dated daily returns are independently validated. They are not sufficient by themselves to authorize active CSV replacement because the required anchor and row-label convention are not proven consistently.
