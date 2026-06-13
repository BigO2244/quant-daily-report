# Recovery Plan

## Incident Findings

- VM `shadow_nav_series.csv` row count: 3,129
- Date range: `2014-01-02` through `2026-06-12`
- Duplicate dates: none
- Missing fields: none
- Non-numeric fields: none
- Non-positive NAV values: none

## NAV Discontinuity

First invalid CSV row:

- `2026-06-09`

Previous CSV row:

- `2026-06-05`

Observed reset ratios from `2026-06-05` to `2026-06-09`:

| Series | Ratio |
|---|---:|
| `caerus_polaris` | `0.03978521420421215` |
| `caerus_orion` | `0.010222370872810641` |
| `caerus_lyra` | `0.010548005695364534` |
| `spy_benchmark` | `0.2547091139110989` |

Last valid row in `shadow_nav_series.csv`:

- `2026-06-05`

Relevant nuance:

- `outputs/shadow_candidates/2026-06-08/` exists.
- `shadow_nav_series.csv` does not contain a `2026-06-08` row.
- The `2026-06-09` row was appended on a ~1.x performance chain while the established CSV chain was on the long historical scale.

## Recovery Gate Decision

Recovery was not performed.

Reason:

- The last valid CSV anchor (`2026-06-05`) and first invalid row (`2026-06-09`) are proven.
- However, model daily returns for `2026-06-08` through `2026-06-12` were not independently validated from PIT universe, strategy holdings, and price inputs during this deployment task.
- `shadow_performance.json` values after the break are internally chain-consistent, but using them alone would trust the corrupted/restarted chain lineage rather than independently validating daily returns.

Fail-closed decision:

- Do not rewrite production Shadow artifacts.
- Keep the deployed fix active so corrupted performance is suppressed.
- Require a separate recovery pass that validates daily returns from immutable dated holdings/weights and price data before staging any recovered files.

## Owner-Gated Recovery Requirements

A future recovery can proceed only after proving:

1. `2026-06-05` is the final valid anchor.
2. The complete repair date range is known, likely `2026-06-08` through `2026-06-12`.
3. Daily returns for Polaris, Orion, Lyra, and SPY are independently recomputed from the same dated holdings/weights and price convention.
4. Recomputed daily returns match the dated artifacts within deterministic tolerance.
5. Recovered NAV is staged outside active artifact paths first.
6. Original and recovered hashes plus daily-return parity are recorded in a recovery manifest.

Compounding formula for a future approved recovery:

```text
recovered_nav_t = recovered_nav_t_minus_1 * (1 + validated_daily_return_t)
```
