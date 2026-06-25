# FR-DH Source Policy Decision Matrix

Status: DRAFT_RESEARCH / READ_ONLY

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Governance and research-data planning only. This
matrix does not change paper/live/shadow trading behavior, execution logic,
broker calls, model decisions, scheduler state, or sleeve data consumption.

## Strategic Purpose

Define the current source policy and canonical normalization order for the
FR-DH data hydration program after the first read-only swarm pass.

This matrix is the operator-reviewed bridge between source discovery and
canonical normalization. It records which source should be used first, which
fallback is acceptable, which credentials are expected, and what remains blocked
before a dataset can become decision-grade.

## Sharadar / Nasdaq Data Link Credential Policy

Existing Caerus Sharadar scripts use the Nasdaq Data Link helper path in
`scripts/research/verify_sharadar_coverage.py`.

Canonical credential names:

- `NASDAQ_DATA_LINK_API_KEY`
- `QUANDL_API_KEY` as the legacy fallback used by existing scripts

The hydration swarm must report these names when credentials are unavailable in
the current shell. It must not print or persist key values. A missing current
shell key is `BLOCKED_CREDENTIALS`; HTTP 401/403 is
`BLOCKED_AUTH_OR_ENTITLEMENT`; HTTP 429 is `RATE_LIMITED`.

Runtime action after the key is approved for the intended shell:

```bash
set +x
export NASDAQ_DATA_LINK_API_KEY=<key>
```

or use the repo-approved env-file mechanism outside the repository:

```bash
.venv/bin/python scripts/data_hydration/run_data_hydration_swarm.py \
  --env-file ~/.caerus/nasdaq_data_link.env \
  --limit-sample \
  --datasets corporate_actions security_master_pit etf_index_constituents fundamentals_pit \
  --sources nasdaq_sharadar
```

The key value must never be committed, printed, or written to generated
artifacts.

## Canonical Normalization Order

| Priority | Dataset families | Rationale |
|---|---|---|
| P1 | OHLCV, security master, corporate actions, dataset freshness | Required platform data and freshness evidence before downstream research consumers. |
| P2 | PIT fundamentals, macro rates, yield curve, VIX regime, insider Form 4, SEC filing metadata | Multi-sleeve and event/fundamental research inputs with clear public or existing-source paths. |
| P3 | 13F, ETF constituents, short interest, options IV/OI, analyst revisions, news metadata | Useful but either vendor-dependent, delayed, source-policy-sensitive, or less foundational. |
| P4 | News sentiment/embeddings, alternative datasets | Experimental or derived datasets requiring explicit model/source/legal policy first. |

## Decision Matrix

| dataset_id | FR-DH reference | canonical priority | current hydration status | best available source | fallback source | paid/free status | credential required | expected env vars | user action required | normalization readiness | recommended next step | blocker reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `ohlcv_prices` | FR-DH-013 / FR-DH-002 | P1 | OK public sample + OBSERVE_ONLY normalized JSON | Yahoo chart public for smoke; Sharadar SEP for PIT/delisted coverage | Alpaca, Polygon, Nasdaq Data Link | free smoke; paid/existing for canonical | no for smoke; yes for Sharadar | `NASDAQ_DATA_LINK_API_KEY`, `QUANDL_API_KEY` | Decide canonical PIT price source hierarchy before decision-grade use. | Observe-only P1 JSON normalizer and validator implemented. | Add full-source price policy, adjustment policy, and row-level uniqueness checks before promotion. | None for smoke; canonical PIT price source still needs policy. |
| `security_master_pit` | FR-DH-002 / FR-DH-013 | P1 | OK Sharadar sample + OBSERVE_ONLY normalized JSON | Sharadar TICKERS for PIT security master; SEC company tickers for public smoke | Alpaca asset metadata | existing paid/vendor plus public supplement | yes for Sharadar | `NASDAQ_DATA_LINK_API_KEY`, `QUANDL_API_KEY` | Use approved env-file or exported key only for read-only probes. | Observe-only P1 JSON normalizer and validator implemented. | Strengthen symbol-history, delisting, and identifier-join validation. | Current artifact is sample-scale and not decision-grade. |
| `corporate_actions` | FR-DH-003 / FR-DH-013 | P1 | OK Sharadar sample + OBSERVE_ONLY normalized JSON | Sharadar ACTIONS | Polygon corporate actions | existing paid/vendor | yes | `NASDAQ_DATA_LINK_API_KEY`, `QUANDL_API_KEY` | Use approved env-file or exported key only for read-only probes. | Observe-only P1 JSON normalizer and validator implemented. | Resolve actions through canonical security ids and add adjustment-factor validation. | Current artifact is sample-scale; security-id resolution remains source-symbol based. |
| `dataset_freshness` | FR-DH-009 | P1 | OK internal manifest + OBSERVE_ONLY normalized JSON | Hydration swarm summary and dataset manifests | Future canonical validators | internal | no | n/a | None. | Observe-only P1 JSON normalizer and validator implemented. | Add freshness-history retention and stale-source observation detection. | Not an external dataset; depends on upstream dataset metadata. |
| `fundamentals_pit` | FR-DH-004 / FR-DH-013 | P2 | OK Sharadar SF1 sample + OBSERVE_ONLY normalized JSON | Sharadar SF1 for canonical PIT fundamentals; SEC companyfacts for public smoke | SEC filings / Nasdaq Data Link | free smoke; paid/existing for normalized vendor feed | yes for Sharadar | `NASDAQ_DATA_LINK_API_KEY`, `QUANDL_API_KEY` | Use approved env-file or exported key only for read-only probes. | Observe-only P2 JSON normalizer and validator implemented. | Harden restatement/version policy, field map, and security-id resolution before feature consumption. | Canonical restatement/version policy pending. |
| `fundamental_features` | FR-DH-005 / FR-DH-013 | P2 | OBSERVE_ONLY feature JSON | Internal deterministic feature builder | none unless approved | internal | no | n/a | None. | Observe-only feature builder, definitions, coverage, and validator implemented. | Run sleeve parity before migration. | Security-id resolution and restatement/version policy remain observe-only. |
| `macro_regime_features` | FR-DH-006 / FR-DH-013 | P2 | OBSERVE_ONLY feature JSON | Internal deterministic feature builder from normalized macro/yield/credit/VIX artifacts | none unless approved | internal | no | n/a | None. | Observe-only feature builder and validator implemented. | Harden release-date policy, macro coverage, and regime-threshold governance before migration. | Release-date policy remains observe-only for public macro samples. |
| `macro_rates` | FR-DH-006 / FR-DH-013 | P2 | OK public sample + OBSERVE_ONLY normalized JSON | FRED public CSV | Treasury/public series mirrors | free | no | n/a | Approve release-date metadata policy. | Observe-only P2 JSON normalizer and validator implemented. | Add release-date calendar and revision labels. | FRED CSV lacks release-time metadata; PIT status remains observe-only. |
| `yield_curve` | FR-DH-006 / FR-DH-013 | P2 | OK public sample + OBSERVE_ONLY normalized JSON | FRED/Treasury public rates | Treasury daily curve | free | no | n/a | Choose canonical Treasury versus FRED mirror before promotion. | Observe-only P2 JSON normalizer and validator implemented. | Add publication-date policy and curve completeness checks. | Publication timestamp not yet captured. |
| `credit_spreads` | FR-DH-006 / FR-DH-013 | P2 | OK public sample + OBSERVE_ONLY normalized JSON | FRED spread proxies | Paid credit dataset if required | free for current proxy | no | n/a | Approve proxy series list before promotion. | Observe-only P2 JSON normalizer and validator implemented. | Add release-date calendar, unit checks, and revisions policy. | Series choice and revisions policy pending. |
| `vix_volatility_regime` | FR-DH-006 / FR-DH-013 | P2 | OK public sample + OBSERVE_ONLY normalized JSON | Yahoo chart public for smoke; CBOE/approved source for canonical | Polygon | free smoke; paid optional | no for smoke; optional paid fallback | `POLYGON_API_KEY` for Polygon fallback | Decide canonical VIX source before promotion. | Observe-only P2 JSON normalizer and validator implemented. | Add canonical source decision and market-calendar checks. | Canonical source not yet approved. |
| `insider_form4` | FR-DH-007 / FR-DH-013 | P2 | PARTIAL SEC sample + OBSERVE_ONLY filing-metadata JSON | SEC submissions/Form 4 filings | Parsed vendor mirror if approved | free | no | n/a | Approve transaction parser scope. | Observe-only P2 filing-metadata normalizer and validator implemented. | Build transaction-level Form 4 parser and amendment handling. | Current sample is issuer-limited and filing-level only. |
| `sec_8k_events` | FR-DH-008 / FR-DH-013 | P2 | PARTIAL SEC sample + OBSERVE_ONLY normalized JSON | SEC submissions and source filings | Parsed vendor mirror if approved | free | no | n/a | Approve item extraction policy. | Observe-only P2 JSON normalizer and validator implemented. | Add accession-level source-document parsing and event-date QA. | Current sample is issuer-limited. |
| `sec_10q_10k_metadata` | FR-DH-008 / FR-DH-013 | P2 | PARTIAL SEC sample + OBSERVE_ONLY normalized JSON | SEC submissions and source filings | Parsed vendor mirror if approved | free | no | n/a | None for sample. | Observe-only P2 JSON normalizer and validator implemented. | Add broader issuer coverage and CIK/security joins. | Current sample is issuer-limited. |
| `institutional_13f` | FR-DH-013 | P3 | PARTIAL SEC sample + OBSERVE_ONLY filing-metadata JSON | SEC 13F filings | Parsed vendor mirror if approved | free | no | n/a | Approve CUSIP/security-id mapping policy before holdings-level use. | Observe-only P3 filing-metadata normalizer and validator implemented. | Build holdings-level parser and CUSIP/security mapping. | CUSIP/security mapping and holdings parsing not solved. |
| `etf_index_constituents` | FR-DH-013 | P3 | OK Sharadar sample + OBSERVE_ONLY normalized JSON | Sharadar SP500 where entitled; approved ETF/index source TBD | ETF holdings/public source if PIT-safe | existing paid/vendor or source-specific | yes for Sharadar | `NASDAQ_DATA_LINK_API_KEY`, `QUANDL_API_KEY` | Use approved env-file or exported key only for read-only probes. | Observe-only P3 JSON normalizer and validator implemented. | Add PIT membership ranges and security-id joins. | No public PIT constituent source approved; sample not decision-grade. |
| `short_interest` | FR-DH-013 | P3 | SOURCE_UNAVAILABLE | FINRA/exchange source TBD | Vendor source if approved | free_or_paid_tbd | source-specific | n/a | Identify exact short-interest endpoint and publication-lag fields. | Not ready. | Approve source and schema. | No stable schema-approved FINRA endpoint configured. |
| `options_iv_open_interest` | FR-DH-013 | P3 | BLOCKED_CREDENTIALS | Polygon/CBOE/options vendor TBD | None approved | paid/vendor likely | yes | `POLYGON_API_KEY` or approved vendor key | Decide options vendor and credential policy. | Not ready. | Select vendor and define contract symbology schema. | No approved/options credentials in current shell. |
| `analyst_estimate_revisions` | FR-DH-013 | P3 | BLOCKED_CREDENTIALS | Estimates vendor TBD; Polygon candidate | None approved | paid/vendor | yes | `POLYGON_API_KEY` or approved estimates vendor key | Select estimates vendor. | Not ready. | Define revision chronology and publication-date policy. | No approved estimates credentials in current shell. |
| `news_metadata` | FR-DH-013 | P3 | PARTIAL GDELT sample + OBSERVE_ONLY normalized JSON | GDELT public news for smoke | Polygon/news vendor | free smoke; paid optional | no for GDELT; yes for paid fallback | `POLYGON_API_KEY` for Polygon fallback | Decide whether public metadata is sufficient or paid feed required. | Observe-only P3 JSON normalizer and validator implemented. | Add timestamp validation, duplicate handling, and source attribution policy. | Public-source timestamps require validation; paid fallback credentials absent. |
| `news_sentiment_embeddings` | FR-DH-013 | P4 | BLOCKED_ACCOUNT_REQUIRED | Internal derived model after news metadata approval | Approved vendor/model if selected | internal_or_paid_tbd | source/model-specific | n/a | Approve model/source and versioned feature policy. | Not ready. | Draft PIT-safe model versioning and reproducibility rules. | No approved sentiment/embedding source. |
| `alternative_datasets` | FR-DH-013 | P4 | BLOCKED_ACCOUNT_REQUIRED | None approved by default | Source-specific | paid_or_unknown | source-specific | n/a | Propose a specific dataset with license, cost, PIT, and validation review. | Not ready. | Keep blocked until a named dataset passes governance review. | No alternative dataset approved. |

## Acceptance Criteria

- Every FR-DH catalog dataset has a source-policy row.
- P1 through P4 normalization order is explicit.
- Sharadar/Nasdaq Data Link credential names match existing repo scripts.
- Missing current-shell credentials, auth/entitlement failures, and rate limits
  are distinct states.
- No runtime consumer is changed by this matrix.

## Validation Plan

- Run the FR-DH hydration swarm in `--dry-run` mode.
- Run the FR-DH hydration swarm in `--limit-sample` mode.
- Validate catalog, freshness, and hydration swarm artifacts.
- Run governance documentation tests and hygiene tests.

## Recommended Next Step

Use `outputs/data_trust/data_trust_summary.json` as the operator-facing
read-only trust summary, then harden dataset-specific coverage, release-date,
restatement/version, and security-id policies before sleeve migration. Keep
approved env-file or exported vendor keys outside the
repository, continue focused no-secret probes when source coverage changes, and
do not wire normalized data into sleeves until separate migration gates approve
consumption.
