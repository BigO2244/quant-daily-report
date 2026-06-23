# FR-068 Market-Cap Root Cause

Date: 2026-06-23
Scope: Research-only FR-068 certification remediation

## Finding

The remaining decision-grade blocker is real and correctly classified:

`PIT_EXACT_LARGE_CAP_DAILY_MARKETCAP_MISSING`

`Universe(as_of_date, "caerus_large_cap")` now resolves the registered family artifact, but that artifact is still built from current Sharadar `scalemarketcap`, not a date-effective numeric market-cap source.

## Lineage Trace

1. `Universe(as_of_date, "caerus_large_cap")`
   - Code: `research/pit_universe.py`
   - Family registry maps `caerus_large_cap` to `data/pit_universe/membership_universe_large_cap.csv`.

2. Large-cap family artifact
   - File: `data/pit_universe/membership_universe_large_cap.csv`
   - Rows: 1,600
   - `scale_source`: 1,600 rows are `scalemarketcap`
   - Source: `sharadar_tickers`

3. Membership generation logic
   - Code: `research/pit_large_cap_family.py`
   - Builder: `scripts/research/build_caerus_large_cap_family.py`
   - Eligibility requires common stock, US exchange, active price history on date, and either:
     - `scalemarketcap` in `5 - Large` / `6 - Mega`, or
     - numeric PIT `marketcap >= 10,000,000,000`.

4. Replay certification
   - Code: `research/canonical_replay_panel.py`
   - `_classify_scale_precision()` marks `scale_source == scalemarketcap` as `PIT_APPROXIMATE_SCALE`.
   - Formal certification then fails with `PIT_EXACT_LARGE_CAP_DAILY_MARKETCAP_MISSING`.

## Why Approximate

`scalemarketcap` is a current/vendor classification. It is not date-effective and can project later/current size status backward into historical decisions.

The security master has listing/delisting dates, identity, category, exchange, and related tickers, but it does not contain numeric daily market cap.

## Exact PIT Inputs Missing

Decision-grade large-cap reconstruction requires one of:

- a security-id keyed daily market-cap panel for the full PIT security master, preferably Sharadar DAILY `marketcap`, or
- complete PIT shares outstanding for the full security master plus unadjusted daily close, including delisted securities and historical ticker changes.

The local workspace currently has neither.
