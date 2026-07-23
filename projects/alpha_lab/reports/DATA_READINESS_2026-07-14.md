# Alpha Lab Four-Lane Data Readiness Audit

Date: 2026-07-14

Repository state inspected: `4d15ade69799a0eff161d5e9819e4d9d574de66d` on `project/alpha-lab`

Scope: repository-local, read-only point-in-time data audit

Governance: `RESEARCH_ONLY` / `NON_EXECUTIONAL` / `NO_RUNTIME_CHANGE`

## Executive conclusion

No selected lane is ready for a decision-grade historical experiment from the
current worktree alone.

| Selected lane | Readiness | Binding reason |
|---|---|---|
| Earnings-revision drift | `BLOCKED_VENDOR` | The repository has earnings-event availability logic, but no analyst-level point-in-time estimates or revision lineage. The local Cygnus schema deliberately leaves reported/consensus fields null. |
| Options-information lead | `BLOCKED_VENDOR` | Existing options code constructs policy recommendations and measures a small set of executed contracts. There is no historical all-contract trade/quote/NBBO/open-interest tape from which informed-flow features can be reconstructed. |
| Insider-conviction clusters | `PARTIAL_LOCAL` | A PIT-aware SEC Form 4 parser and prior pilot review exist, but the current worktree lacks the raw Form 4 cache/output, canonical PIT security master, PIT price/liquidity panel, and the CIK input the builder actually reads. No paid vendor is inherently required. |
| Supply-chain shock diffusion | `BLOCKED_VENDOR` | No issuer-level, effective-dated customer/supplier relationship history exists locally. The local earnings tape can anchor publication time, but it cannot supply the missing historical network. |

`READY_LOCAL` means that every signal-defining input, its availability lineage,
and the required PIT identity/price joins are physically present in this
worktree. `PARTIAL_LOCAL` means material reusable code or data exists and the
missing source does not inherently require a commercial vendor.
`BLOCKED_VENDOR` means a defining historical signal input cannot be honestly
reconstructed from repository-local assets.

## Shared prerequisites

These blockers apply before any of the four lanes can make an alpha claim.

### 1. PIT universe and security identity: contract exists, raw assets absent

The canonical reader is `research/pit_universe.py`. The canonical artifact
family documented by `data/pit_universe/README.md` is:

- `data/pit_universe/security_master.csv`
- `data/pit_universe/membership_universe.csv`
- `data/pit_universe/membership_universe_large_cap.csv` for the
  `caerus_large_cap` family
- `data/pit_universe/symbol_history.csv`
- `data/pit_universe/security_events.csv`

The reader directly consumes the security master and membership files; symbol
history and security events are required supporting identity/action lineage for
the four experiments.

Only `data/pit_universe/README.md` is present in this worktree; the generated
CSV artifacts above are absent. The reader correctly raises
`PITUniverseUnavailable` and refuses to fall back to `data/universe.csv`.

The intended identity fields are `security_id` (Sharadar permaticker-backed),
`permaticker`, `ticker`, `name`, `exchange`, `category`, `firstpricedate`,
`lastpricedate`, `relatedtickers`, `membership_family`,
`membership_start_date`, `membership_end_date`, `source`, and `confidence`.

Even after restoring the previously generated assets, the current
`caerus_large_cap` family is not decision-grade: its historical membership was
formed with current `scalemarketcap`. The existing certification reports require
a PIT-valid, survivorship-free, security-ID-keyed, date-effective replacement.
Relevant evidence is in `reports/pit_universe_certification.md`,
`reports/decision_grade_pit_program_final_2026-06-22.md`, and
`reports/fr068_requirement_replacement_remediation_2026-06-23.md`.

### 2. Prices and liquidity: historical summary survives, canonical panel does not

`outputs/research/pit_liquidity/manifest.json` documents a previously built
7,845,012-row, 1,600-ticker panel covering 1997-12-31 through 2026-06-18 with:

`ticker`, `date`, `open`, `high`, `low`, `close`, `closeadj`, `volume`,
`dollar_volume`, `ADV_20`, `ADV_60`, `dollar_ADV_20`, `dollar_ADV_60`.

The referenced raw panel,
`outputs/research/pit_liquidity/pit_liquidity_panel.csv`, is absent in this
worktree. The tracked manifest and
`outputs/research/pit_liquidity/pit_liquidity_diagnostics.json` are evidence of
a prior build, not substitutes for the underlying observations.

`alpha_stack_cache/prices/_matrix_prices_2007_2026.parquet` is present, but it is
a wide adjusted-close matrix for the current-name set. Repository audits describe
it as approximately 203 columns, current survivors only, without PIT membership
or delisted constituents. It is acceptable for a smoke test and SPY series, not
for official cross-sectional alpha evidence.

The previously reported security-ID replay panel under
`outputs/research/canonical_pit_replay/` is also absent from this worktree.

### 3. Issuer mapping: useful current map, no historical master

`data/alpha_stack_cache/edgar/sec_ticker_map.json` is present with 197 current
ticker-to-CIK mappings. `data/security_master/manual_aliases.json` covers only
`BK -> BNY`, `FB -> META`, and `SQ -> XYZ`. Neither source is effective-dated.

Both the Cygnus event builder and the Form 4 builder actually load
`cik_mapping_results.csv`; that file is absent. Current-ticker mapping must not
be projected backward across ticker changes, mergers, spin-offs, or delistings.

### 4. Corporate actions and factor controls

The shared panel must contain unadjusted and adjusted OHLCV, adjustment factors,
cash dividends, splits, spin-offs, mergers, delisting/terminal returns, and the
timestamp at which each correction became available. The current wide price
matrix alone does not meet that contract.

No canonical persisted standard-factor panel was identified locally for market,
size, value, profitability/quality, investment, momentum, low volatility,
industry, and beta controls. Each experiment needs one common, versioned factor
panel so a lane is not credited for repackaging Caerus's existing market or
momentum exposures.

### 5. Common provenance envelope

Every acquired record should be immutable and carry at minimum:

- `source_record_id`, `source_name`, and `source_uri`
- `security_id` plus the source/vendor identifier
- `source_published_at` or exchange event time
- `available_at` for the model
- `retrieved_at` and `ingested_at`
- `revision_id`, `supersedes_id`, and cancellation/correction status
- `source_sha256` and immutable raw-payload path
- `decision_timestamp` and first eligible `tradable_date`
- dataset version, entitlement/license identifier, and coverage report

No experiment should silently substitute processing time, filing date, vendor
"as of" labels, or today's ticker for true availability and identity lineage.

## Lane 1: Earnings-revision drift

Readiness: `BLOCKED_VENDOR`

### What exists locally

`research/cygnus/events.py` builds an SEC EDGAR 8-K Item 2.02 event tape using
`acceptanceDateTime`. `research/cygnus/artifacts.py` defines these fields:

`ticker`, `cik10`, `fiscal_period`, `announcement_date`, `announcement_time`,
`availability_date`, `acceptance_datetime_utc`, `acceptance_datetime_et`,
`acceptance_timestamp_present`, `filing_date`, `items`,
`has_financial_exhibit_item`, `accession_number`, `primary_document`,
`reported_eps`, `consensus_eps`, `reported_revenue`, `consensus_revenue`,
`guidance_signal`, `event_class`, `source`, `ingested_at`.

The critical fields `reported_eps`, `consensus_eps`, `reported_revenue`,
`consensus_revenue`, and `guidance_signal` are explicitly emitted as `None` in
`research/cygnus/events.py`. There is no `outputs/research/cygnus/` directory in
this worktree. The 153 local
`data/alpha_stack_cache/edgar/facts_*.parquet` files may support some reported
fundamental features, but they do not contain analyst expectations or revision
history. The v0 backtest also expects `data/fundamental/<ticker>.parquet`, which
is absent.

The canonical governance packet,
`docs/governance/fr_active/fr_069_cygnus_onboarding_packet.md`, records v0 as
shelved and v1 as gated on vendor-backed PIT consensus/EPS-surprise data. A
price-reaction-only rerun would retest the shelved v0 family; it would not test
the selected earnings-revision hypothesis.

### Point-in-time leakage risks

- A vendor may overwrite prior consensus instead of preserving each analyst's
  original publication and revision timestamps.
- Consensus measured after the earnings release can be compared accidentally
  with the reported result as if it were the pre-event expectation.
- Post-event revisions can enter a signal before the individual revision was
  published or disseminated.
- Fiscal-period, GAAP/non-GAAP basis, split basis, currency, and continuing-
  operations mismatches can create false surprise and revision magnitudes.
- Today's analyst roster or consensus contributor count can be projected into
  history.
- Announcement calendars and "confirmed" times can be revised after the event.
- The preserved 2025-forward Cygnus holdout must remain uninspected until the
  frozen experiment explicitly consumes it.

### Minimum acquisition contract

Consensus snapshots alone are insufficient because the proposed signal requires
revision breadth and analyst independence. Acquire analyst-level history with:

- stable issuer/security ID and vendor entity ID;
- stable analyst and broker IDs;
- measure (`EPS`, `revenue`), fiscal period/end, horizon, currency, unit,
  accounting basis, and per-share adjustment basis;
- original estimate value and `published_at`;
- every revision, withdrawal, confirmation, and supersession with event time;
- vendor receive/availability time and later correction lineage;
- consensus composition at each decision timestamp: contributor IDs/count,
  mean, median, high, low, dispersion, and calculation method;
- reported actual with original publication time and later restatement lineage;
- survivorship-free issuer coverage, including delisted names;
- rights to retain immutable raw snapshots and reproduce historical queries.

Acceptance gate: for sampled events, the dataset must reproduce the exact
pre-announcement contributor set and every post-event revision using only rows
whose `available_at <= decision_timestamp`.

## Lane 2: Options-information lead

Readiness: `BLOCKED_VENDOR`

### What exists locally

`core/options_overlay_shadow.py` produces policy-derived fields such as
`strategy`, `target_dte`, `expiry`, `premium_budget_dollars`,
`contracts_recommended`, `strike`, and option-leg kind. Those are constructed
recommendations based on SPY spot, regime, and portfolio state; they are not
observed option-market data.

`scripts/build_tca.py` can request Alpaca daily and one-minute option bars for a
small set of OCC symbols found in the broker ledger. It stores bar timestamps and
OHLC-style values for those selected contracts. The same code explicitly returns
no historical quote for an OCC option because the connected source does not
provide the required option quote history. No option-related output or raw option
cache is present in this worktree.

This local surface cannot reconstruct signed flow, trade-to-NBBO location,
volume-weighted strike displacement, synthetic forwards, skew, term structure,
or source diversity across the listed option universe.

### Point-in-time leakage risks

- Joining a trade to a quote posted after the trade rather than the last valid
  disseminated NBBO at or before it.
- Using corrected/consolidated trade records without retaining original and
  correction sequence lineage.
- Applying end-of-day open interest intraday or assigning it to the wrong trade
  date.
- Computing Greeks/IV with future underlying price, future volatility, revised
  rates/dividends, or a stale quote.
- Losing adjusted/renamed/expired contracts and thereby retaining only contracts
  visible today.
- Omitting zero-flow eligible names, which conditions the sample on observed
  activity.
- Treating raw call buying as bullish without delta, moneyness, multi-leg,
  spread, condition-code, and dealer-side controls.
- Mixing earnings-event flow with ordinary flow using a revised event calendar.

### Minimum acquisition contract

Acquire a full-universe historical option trade and quote feed, not only bars
for contracts Caerus happened to trade. Required fields are:

- immutable option security ID, raw OCC symbol, root, underlying stable
  `security_id`, call/put, strike, expiration, style, multiplier, and corporate-
  action adjustment lineage;
- trade exchange timestamp, SIP/receive timestamp, sequence number, price, size,
  exchange, sale condition, correction/cancel/bust status, and trade ID;
- contemporaneous bid, ask, bid size, ask size, quote timestamp, exchange/NBBO
  flag, quote condition, and sequence ID;
- synchronized underlying bid/ask/trade with timestamp and stable identity;
- open interest with its actual publication/availability timestamp;
- implied volatility and Greeks with methodology/version, or the complete PIT
  inputs needed to reproduce them: rates, dividends, borrow assumption, spot,
  and quote;
- an explicit multi-leg/complex-order indicator when available;
- complete eligible-chain snapshots so zero-flow contracts and names remain in
  the denominator;
- delisted underlyings, expired contracts, adjustment memos, and symbol history;
- immutable raw retention, coverage diagnostics, and correction lineage.

Acceptance gate: a sampled trade day must be replayable in event-time order,
including cancels/corrections, with no quote or open-interest observation made
available before its source timestamp.

## Lane 3: Insider-conviction clusters

Readiness: `PARTIAL_LOCAL`

### What exists locally

`scripts/research/build_cassiopeia_phase_c_form4_event_tape.py` is a reusable
PIT-aware reference builder. It fetches full SEC submissions history plus Form 4
XML, caches raw payloads, and maps EDGAR acceptance time to a tradable date. Its
parsed fields include:

- issuer: `issuer_cik`, `issuer_ticker`, `period_of_report`;
- owner: `name`, `cik`, `officer_title`, `is_director`, `is_officer`,
  `is_ten_percent_owner`, `role`, `role_weight`;
- transaction: `transaction_code`, `transaction_date`, `shares`, `price`,
  `transaction_value`;
- filing/event: `accession_number`, `primary_document`, `filing_date`,
  `acceptanceDateTime`, `acceptance_datetime_utc`,
  `acceptance_datetime_et`, `event_date`, `announcement_time`, `tradable_date`,
  `source_url`, `source_document`;
- PIT/measurement: `security_id`, `pit_validity_flag`, `exclusion_reason`,
  `reason_codes`, forward returns, SPY-relative returns, ADV, capacity, and an
  implementation-shortfall proxy.

The prior review at
`docs/governance/fr_active/fr_069_cassiopeia_phase_c_form4_review.md` reports a
pilot of 50 raw filings, 16 PIT-valid filings, five unique tickers, and no usable
purchase cohort. It is evidence that the pipeline shape was exercised, not a
decision-grade local dataset. The corresponding Form 4 JSON, SEC cache, and
checkpoint are absent from `outputs/research/cassiopeia/` in this worktree.

The builder's universe loader reads only `cik_mapping_results.csv`, which is
absent. Its required `data/pit_universe/security_master.csv` and
`outputs/research/pit_liquidity/pit_liquidity_panel.csv` inputs are also absent.
As checked out, the builder would produce an empty universe rather than a full
historical event tape.

The tracked 13D artifact at
`outputs/research/cassiopeia/cassiopeia_phase_b_13d_event_tape_2026-06-18.json`
demonstrates a reusable acceptance-time, event, PIT-join, and forward-return
schema. It is not Form 4 evidence and must not be substituted for the insider
signal.

### Point-in-time leakage and inference risks

- Use SEC acceptance time, never the underlying transaction date, as public
  availability. Filing delay must be an explicit feature/diagnostic.
- Deduplicate owners across legal vehicles and normalize owner identity before
  calling purchases a multi-insider cluster.
- Same-issuer, same-day filings and overlapping 20/60-day returns are not
  independent observations.
- Today's issuer ticker/CIK map can misassign historical filings.
- Form 4/A amendments, cancelled transactions, footnotes, direct/indirect
  ownership, and 10b5-1 indicators can change interpretation.
- Grants, exercises, gifts, tax withholding, derivative conversions, and sales
  must not enter an open-market-purchase cohort merely because shares changed.
- Missing/failed XML parses must remain visible in coverage and selection-bias
  diagnostics.

### Minimum acquisition/hydration contract

No commercial vendor is required for a minimum honest build. Hydrate immutable
SEC submissions and primary Form 4/Form 4-A documents with:

- accession number, form type, filing date, full `acceptanceDateTime`, source URL,
  raw document hash, retrieval time, and amendment lineage;
- issuer CIK plus effective-dated stable `security_id` and ticker;
- reporting-owner CIK/name plus normalized person/entity ID;
- director/officer/10% owner flags and original officer title;
- transaction code/date, acquired/disposed code, shares, price, value, security
  title, direct/indirect ownership, post-transaction holdings, 10b5-1 indicator
  when reported, and footnote references/text;
- all non-derivative and derivative rows, while the frozen signal admits only
  predeclared open-market purchase codes;
- complete filing coverage and parse-failure counts for the eligible universe.

Acceptance gate: restore and certify the shared PIT identity/price inputs, then
demonstrate full-window accession coverage, deterministic amendment handling,
person-level cluster dedupe, and a non-empty purchase cohort before any return
test is interpreted.

## Lane 4: Supply-chain shock diffusion

Readiness: `BLOCKED_VENDOR`

### What exists locally

No issuer-level customer/supplier edge file, effective-dated relationship table,
revenue-dependency history, or signal builder was identified under `data/`,
`outputs/`, `research/`, or `scripts/research/`.

`research/cygnus/events.py` can provide PIT-aware customer earnings-event
availability once its missing inputs are restored. The SEC fact cache may supply
some reported company values. Neither creates a historical relationship graph.
The relationship-graph language in the research MCP architecture is object
metadata architecture, not an issuer supply-chain dataset, and must not be used
as if it were one.

If the upstream customer shock is defined using analyst revisions or consensus
surprise, this lane also inherits the earnings-revision vendor blocker. Replacing
that shock with a price gap or generic news score would change the economic
hypothesis and must be frozen as a different experiment.

### Point-in-time leakage risks

- Projecting today's customer/supplier graph or current revenue dependency into
  earlier years.
- Using a relationship before its first public/vendor-observed timestamp.
- Ignoring edge termination, direction changes, confidence downgrades, mergers,
  ticker changes, and supplier/customer spin-offs.
- Treating a stated customer relationship as material without the PIT revenue
  share or a frozen missing-value rule.
- Propagating a customer event before the event itself was public.
- Crediting network diffusion for common industry, market, commodity, or
  simultaneous macro news.
- Selecting only relationships that remain visible today and omitting dead
  issuers/edges.
- Searching multi-hop paths after seeing returns.

### Minimum acquisition contract

Acquire historical snapshots or event-sourced edge history with:

- stable `edge_id` and source vendor record ID;
- supplier and customer stable security/entity IDs plus historical tickers;
- explicit edge direction and relationship type;
- `effective_start`, `effective_end`, `first_observed_at`, `available_at`,
  `last_confirmed_at`, and deletion/termination timestamp;
- revenue dependency percentage or disclosed range, period, currency/basis, and
  missing-value meaning;
- source filing/document, source publication timestamp, source hash, extraction
  method, and confidence;
- every revision and supersession, not a current snapshot with an "as of" label;
- inactive/delisted entities and historical corporate-action lineage;
- full coverage denominators so no-edge and unknown-edge cases are distinct.

The upstream shock table must independently carry customer `security_id`, event
ID, `available_at`, shock components, pre-event expectation basis, and first
tradable timestamp. The first frozen version should be one-hop only; adding
multi-hop propagation would be a new experiment/trial.

Acceptance gate: reconstruct several known historical relationships from only
records available on each decision date, including one changed/terminated edge,
and prove that later relationship updates do not alter prior snapshots.

## Data-first implementation order

The defensible order from this audit is:

1. Restore/certify the shared PIT universe, identity, price, liquidity,
   corporate-action, and factor panels.
2. Use insider conviction as the reference lane after public SEC hydration; it
   is the only selected model without an inherent commercial-data dependency.
3. Select and validate an analyst-level estimates vendor before implementing the
   earnings-revision signal.
4. Acquire and validate effective-dated one-hop supply-chain history; reuse the
   frozen customer-shock tape rather than inventing a second earnings pipeline.
5. Acquire a bounded sample of event-time option trades and quotes and prove
   deterministic feature reconstruction before committing to a broad license.

Fail-closed rule: tracked manifests, governance reviews, current-ticker maps,
current-universe prices, and policy-generated options recommendations may support
engineering smoke tests, but none may be relabeled as the missing raw PIT signal
data.

## Boundary attestation

This audit did not fetch network data, inspect the preserved Cygnus holdout,
run a return experiment, activate a shadow model, or change trading, allocation,
execution, broker, risk, scheduler, cron, paper, or live behavior.
