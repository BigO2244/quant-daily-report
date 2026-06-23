# FR-068 Market-Cap Data Audit

Date: 2026-06-23
Scope: Existing local data only

## Inventory

| Source | Local coverage | PIT suitability | Survivorship risk | Result |
|---|---:|---|---|---|
| `data/pit_universe/security_master.csv` | 20,618 securities | PIT listing/delisting identity only | Low for identity | No numeric market cap |
| `data/pit_universe/membership_universe_large_cap.csv` | 1,600 members | Date ranges, but size source is current `scalemarketcap` | Size look-ahead risk | Not decision-grade |
| Sharadar SEP close cache | 1,600 files | PIT price history for current family | Incomplete for full security master | Price-only, no shares |
| Sharadar SEP OHLCV cache | 1,600 files | PIT unadjusted close available for current family | Incomplete for full security master | Price leg usable, not enough |
| `data/fundamental/*.parquet` | 200 files | Filing-date gated shares for some active/current names | High for delisted/missing names | Insufficient |
| EDGAR normalized cache | 153 files | No usable `CommonStockSharesOutstanding` field in normalized cache | High for universe coverage | Insufficient |
| Corporate actions | No local ACTIONS cache found | Needed for robust split/share validation | Missing | Insufficient |
| Sharadar DAILY market cap | No local cache found | Correct target source | Missing | Blocking |

## Filing-Based Reconstruction Coverage

The audit attempted the strongest local fallback: reported `CommonStockSharesOutstanding` with `filed_date <= as_of_date` multiplied by SEP OHLCV unadjusted close.

This was tested against the already-narrow 1,600-row current large-cap family. Since the true decision-grade universe must be reconstructed from the full 20,618-security master, failure on this subset is sufficient to reject the fallback.

| Date | Active current-family members | SEP close available | Shares available | Reconstructable market cap | Coverage |
|---|---:|---:|---:|---:|---:|
| 2014-01-02 | 1,197 | 1,197 | 116 | 116 | 9.6909% |
| 2020-01-02 | 1,243 | 1,243 | 134 | 134 | 10.7804% |
| 2026-01-02 | 1,260 | 1,260 | 149 | 149 | 11.8254% |

## Audit Artifact

JSON: `outputs/research/fr068_marketcap_reconstruction/2026-06-22/fr068_marketcap_reconstruction_audit.json`

CSV: `outputs/research/fr068_marketcap_reconstruction/2026-06-22/filing_marketcap_coverage.csv`

Audit status: `FAIL`

Blockers:

- `SHARADAR_DAILY_MARKETCAP_PANEL_MISSING`
- `FILING_BASED_SHARE_COUNT_COVERAGE_INSUFFICIENT`
