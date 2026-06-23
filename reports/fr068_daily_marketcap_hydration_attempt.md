# FR-068 DAILY Market-Cap Hydration Attempt

Date: 2026-06-23

## Objective

Hydrate Sharadar DAILY market cap for the full PIT security master, build a
date-effective `caerus_large_cap` family with `scale_source=marketcap`, and rerun
canonical replay certification.

## Implementation Added

- `scripts/research/hydrate_sharadar_daily_marketcap.py`
- `scripts/research/build_daily_marketcap_large_cap_family.py`
- `research/fr068_marketcap_reconstruction.py`
- `Tests/test_fr068_marketcap_reconstruction.py`

## Hydration Command

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 scripts/research/hydrate_sharadar_daily_marketcap.py \
  --security-master data/pit_universe/security_master.csv \
  --cache-dir data/research_cache/sharadar_daily_marketcap
```

Initial result before credential discovery:

```text
[DAILY_MARKETCAP_HYDRATE][REFUSED] No API key. Use --api-key, --env-file, NASDAQ_DATA_LINK_API_KEY, or QUANDL_API_KEY. (Key never logged.)
EXIT_CODE:2
```

Credential discovery update:

- canonical project variable: `NASDAQ_DATA_LINK_API_KEY`
- credential source found: local shell history, masked and reused in-process only
- secret exposure: no secret printed, written, or passed on the command line

Hydration smoke test:

```text
CREDENTIAL_DISCOVERY:FOUND_MASKED source=local_zsh_history var=NASDAQ_DATA_LINK_API_KEY
security_count: 2
hydrated: 0
empty: 2
failed: 0
total_rows: 0
```

Known-ticker DAILY smoke after adding `--tickers` support:

```text
tickers: AAPL,MSFT
security_count: 2
hydrated: 2
empty: 0
failed: 0
total_rows: 164
date_range: 2018-09-04..2018-12-31
```

Targeted entitlement probe:

```text
SHARADAR/SEP AAPL 2024+: rows=619, first=2024-01-02, last=2026-06-22
SHARADAR/DAILY AAPL all: rows=82, first=2018-09-04, last=2018-12-31
SHARADAR/DAILY AAPL 2024: rows=0
SHARADAR/DAILY AAPL 2014: rows=0
SHARADAR/DAILY TWTR all: rows=0
SHARADAR/DAILY ATVI all: rows=0
SHARADAR/DAILY 2018-09-04 all tickers: rows=28
SHARADAR/DAILY 2018-12-31 all tickers: rows=28
```

Interpretation: the existing credential is valid for Sharadar SEP but the
available `SHARADAR/DAILY` response is a tiny sample slice, not the full
survivorship-free daily market-cap panel required for FR-068 certification.

## Family Build Command

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 scripts/research/build_daily_marketcap_large_cap_family.py \
  --security-master data/pit_universe/security_master.csv \
  --daily-cache-dir data/research_cache/sharadar_daily_marketcap \
  --output data/pit_universe/membership_universe_large_cap.csv \
  --summary-out outputs/research/fr068_marketcap_reconstruction/2026-06-22/daily_marketcap_family_summary.json
```

Result after DAILY entitlement probe:

```text
status: FAIL
daily_marketcap_rows: 0
membership_rows: 0
membership_security_count: 0
blocker: DAILY_MARKETCAP_MEMBERSHIP_SECURITY_COUNT_BELOW_THRESHOLD
```

The canonical large-cap membership artifact was not overwritten.

## Certification Result

```text
status: FAIL
decision_grade_status: FAIL
finding: PIT_EXACT_LARGE_CAP_DAILY_MARKETCAP_MISSING
warning: MEMBERSHIP_SCALE_NOT_PIT_EXACT:PIT_APPROXIMATE_SCALE
```

## Gate Decision

FR-068 remains blocked until the existing Nasdaq Data Link / Sharadar credential
has full `SHARADAR/DAILY` market-cap entitlement, or an equivalent full
`SHARADAR/DAILY` market-cap cache is available.
