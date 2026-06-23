# FR-068 Market-Cap Blocker Report

Date: 2026-06-23

## Certification Result

Status: `FAIL`

Decision-grade status: `FAIL`

Finding:

- `PIT_EXACT_LARGE_CAP_DAILY_MARKETCAP_MISSING`

Warning:

- `MEMBERSHIP_SCALE_NOT_PIT_EXACT:PIT_APPROXIMATE_SCALE`

Digest:

`359c5a09668a4bff1d42fa1b998c0b1acb3881c39a1ef50d76a84e87e159c5ee`

## Remediation Attempt

Implemented research-only tooling to:

- inventory local market-cap-capable sources,
- test filing-based market-cap reconstruction from shares outstanding x unadjusted close,
- build a date-effective membership artifact from a true daily market-cap panel when available.

Files:

- `research/fr068_marketcap_reconstruction.py`
- `scripts/research/audit_fr068_marketcap_reconstruction.py`
- `Tests/test_fr068_marketcap_reconstruction.py`

## Result

The blocker could not be cleared with existing local data.

Reasons:

- No local Sharadar DAILY market-cap panel was found.
- No Nasdaq Data Link API key was visible in the shell or expected local env/key files.
- Filing-based reconstruction coverage is insufficient:
  - 2014-01-02: 116 / 1,197 current-family members
  - 2020-01-02: 134 / 1,243 current-family members
  - 2026-01-02: 149 / 1,260 current-family members
- Filing/fundamental caches are not survivorship-free enough for the full 20,618-security PIT master.

## Gate Decision

FR-068 remains blocked.

Do not run conviction-allocation or sleeve-promotion rebaselines from this infrastructure yet.

Next exact blocker to clear:

`SHARADAR_DAILY_MARKETCAP_PANEL_MISSING`

## 2026-06-23 Hydration Attempt

Added a research-only Sharadar DAILY market-cap hydrator and a cache-to-family
builder. A later credential-discovery pass found the existing project credential
as `NASDAQ_DATA_LINK_API_KEY` in local shell history and reused it in-process
without printing or writing the secret.

The credential is valid for Sharadar SEP, but `SHARADAR/DAILY` returned only a
small sample slice:

- AAPL DAILY all: 82 rows from 2018-09-04 to 2018-12-31
- AAPL DAILY 2024: 0 rows
- AAPL DAILY 2014: 0 rows
- TWTR DAILY all: 0 rows
- ATVI DAILY all: 0 rows
- all tickers on 2018-09-04: 28 rows
- all tickers on 2018-12-31: 28 rows

By contrast, the same credential returned AAPL SEP rows from 2024-01-02 through
2026-06-22. The blocker is therefore no longer credential discovery; it is
`SHARADAR/DAILY` entitlement/coverage.

The fail-closed family builder produced:

- `daily_marketcap_rows`: 0
- `membership_rows`: 0
- `membership_security_count`: 0
- blocker: `DAILY_MARKETCAP_MEMBERSHIP_SECURITY_COUNT_BELOW_THRESHOLD`

The canonical `data/pit_universe/membership_universe_large_cap.csv` artifact was
not overwritten.
