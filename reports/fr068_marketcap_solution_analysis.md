# FR-068 Market-Cap Solution Analysis

Date: 2026-06-23

## Option A: Use Existing PIT Fields

Use security master listing dates, category, exchange, and current large-cap membership.

Result: rejected.

Reason: the existing PIT fields do not contain numeric market cap. The current large-cap artifact uses `scalemarketcap`, which is current/vendor scale and not date-effective.

Certification likelihood: none.

## Option B: Reconstruct Market Cap From Shares Outstanding x Close

Formula:

`market_cap = reported_common_shares_outstanding * unadjusted_close`

PIT rule:

Only use share values where `filed_date <= as_of_date`.

Result: rejected for canonical certification.

Reason: local share-count coverage is far below decision-grade requirements. Coverage is only 9.6909% to 11.8254% on the current-family checkpoint dates, with essentially no delisted coverage. It also covers only the current 1,600-family price cache, not the full 20,618-security master needed to avoid size survivorship contamination.

Certification likelihood: none with current local data.

## Option C: Build From Sharadar DAILY Market Cap

Use a full PIT daily market-cap panel, keyed by `security_id` or mapped from Sharadar ticker/permaticker, for the full security master.

Required fields:

- `date`
- `security_id` or Sharadar ticker with deterministic security-id mapping
- `marketcap`

Result: recommended, but currently blocked by missing local cache/API key.

Correctness: high if sourced from Sharadar DAILY and lineage is hashed.

PIT compliance: high if daily rows are used only as of their own `date`.

Survivorship compliance: high only if DAILY coverage includes active and delisted securities across the full security master.

Implementation effort: moderate. A fail-closed builder now exists in `research/fr068_marketcap_reconstruction.py`.

Certification likelihood: high after the DAILY panel is hydrated and coverage validated.

## Option D: Alternative Proxy

Possible proxies include liquidity, current scale, price percentile, or current market-cap snapshots.

Result: rejected.

Reason: these do not answer the certification blocker. They remain approximations and can create look-ahead or universe contamination.

## Recommendation

Do not replace the canonical family with a partial filing-based reconstruction.

Hydrate or supply a full Sharadar DAILY market-cap panel, build a date-effective `scale_source=marketcap` membership artifact, then rebuild the canonical replay panel and rerun decision-grade certification.
