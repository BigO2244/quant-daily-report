# Alpha Lab Data Spine Build

Date: 2026-07-15

Governance: RESEARCH_ONLY / NONEXECUTIONAL / NO_RUNTIME_CHANGE

## Outcome

The common data-spine infrastructure is implemented under
projects/alpha_lab/data_spine/. It emits immutable, checksummed external-data
bundles and contains no broker, execution, allocation, paper, live, production
cron, deployment, or strategy-registry integration.

## Executed acquisition

| Source | Result | Evidence |
|---|---|---|
| Kenneth French + AQR | Captured and normalized | 26,761 combined daily factor rows; FF5, momentum, short-term reversal, BAB, and QMJ plus raw industry/AQR vintages |
| FRED/ALFRED | Captured | Initial-release mode where supported; current-vintage fallback is explicitly labeled for daily series whose vintage query is unsupported |
| EIA natural gas + petroleum | Captured | Both public bulk archives plus 14,712 normalized observations for five declared controls |
| EIA electricity | Captured | Official 290,265,140-byte archive plus 1,472 rows for eight monthly generation/price controls; current-vintage proxy only |
| BEA input-output | Reference captured; API key pending | Public NAICS/industry concordance and official API guide retained; selected current-vintage tables require a free BEA key |
| USAspending | Capture in progress | No-key, checkpointed federal contract-award proxy using exact normalized issuer legal names; government-customer subgraph only |
| Alpha Vantage | Adapter complete; free key pending | Bounded current aggregate earnings-estimate and listing/delisting snapshots; not historical analyst-level PIT data |
| yfinance analyst proxy | Captured | 200-name current snapshot; 199 names with usable aggregate EPS/revenue estimates and revision/trend fields; forward-only, no analyst identity |
| OCC references | Adapter complete; automated capture blocked | OCC returned HTTP denial to the non-browser research client; contract-specific files require manual download/intake |
| Sharadar | Reference and SEP capture complete | Complete TICKERS and ACTIONS bundles retained; 21,845,096 SEP rows cover 12,554 listed/delisted ticker histories from 2011-01-03 through 2026-06-30; DAILY and SF1 are sample-only |
| SEC EDGAR | Captured and normalized | 2012-2026 filing index, Company Facts bulk, 58 quarterly insider archives, a compact filing-time facts panel, and 4,186,218 normalized non-derivative transactions |
| Analyst-estimate and supply-chain trials | Schema gates complete; no samples received | Vendor-neutral required-field contracts are ready |

## Sharadar decision

Do not renew blindly, but do not allow the remaining entitlement to expire
before attempting the capture.

The local machine currently has no discoverable canonical Sharadar artifacts:
no security master, SEP cache, or PIT liquidity panel was found in the local
Caerus checkouts. Therefore the previous conclusion that Sharadar was not
needed long term depended on retaining the already-built research artifacts.
That retention condition is not presently satisfied locally.

Credential handling decision recorded on 2026-07-15:

- canonical local file:
  `/Users/brettolson/Documents/Caerus/alpha-lab-project/.env`;
- canonical variable: `NASDAQ_DATA_LINK_API_KEY`;
- permissions: local-only mode `600`, with `.env` already ignored by git;
- tracked non-secret template:
  `projects/alpha_lab/data_spine/env.example`;
- the research CLI loads simple entries from this file automatically, never
  overrides caller-supplied environment values, and never persists the
  credential in a data bundle; and
- the previously displayed credential must be rotated and is not approved for
  storage or use.

Access and capture update recorded on 2026-07-15:

- a rotated key was installed in the canonical ignored local file;
- the confirmed current Sharadar entitlement remains available through
  2026-08-10; reassess renewal only after the scoped SEP/DAILY capture and
  retention-rights review;
- all five named tables passed a one-row endpoint audit, but stronger ticker and
  date probes subsequently established that only TICKERS, ACTIONS, and SEP have
  the required full coverage; DAILY and SF1 are restricted sample datasets;
- access-audit manifest:
  `outputs/research/alpha_lab/data_spine/sharadar_access/20260715T180453Z-35685fa55785/manifest.json`;
- TICKERS capture: 62,487 rows across 7 pages, 46,365,087 bytes, SHA-256
  `4bef9da4e4e0d57e0b1f27c791ae21afec3aeabf27b3ceb4d5ada965a49f878d`;
- TICKERS manifest:
  `outputs/research/alpha_lab/data_spine/sharadar_tickers/20260715T180509Z-34c6cde6342f/manifest.json`;
- ACTIONS capture: 670,195 rows across 68 pages, 101,739,935 bytes, SHA-256
  `e44b4dec00bf5dc6643d4cc3bb75a33701f1543c708231e5007a320a720b6acb`;
- ACTIONS manifest:
  `outputs/research/alpha_lab/data_spine/sharadar_actions/20260715T180518Z-c6dd93eff60d/manifest.json`;
- a date-effective security-existence universe now contains 19,627 security
  histories, 19,538 effective CIK mappings, and 12,554 listed/delisted common-
  equity ticker histories in the scoped SEP capture universe;
- the SEP collector used deterministic ticker-chunk checkpoints and bounded
  request retries, so a transient provider timeout resumes without losing
  completed chunks;
- the final SEP bundle contains 21,845,096 rows, 12,554 observed ticker
  histories, 2,217 completed pages, and SHA-256
  `9d74795900d16a40521854e322ad3be754b9dc34bec8e284c2dd7bd5383f8895`;
- the compiled price/liquidity and characteristics panels each contain
  21,840,452 security-days across 12,554 security histories; market-cap,
  book-to-market, beta, and 20-day volatility coverage is 80.74%, 75.49%,
  99.87%, and 99.89%, respectively; returns use adjacent provider total-return
  ratios, while unadjusted closes and reconstructed unadjusted opens preserve
  actual price levels without double-applying dated split actions;
- both reference bundle manifests record `credential_value_persisted: false`; and
- the post-capture production-boundary attestation is `CLEAN` with no findings.

The scoped SEP capture covers 2011-01-01 through 2026-06-30 and includes all
common-equity ticker histories active at any point in that window, rather than
only today's names. SEC Company Facts replaces SF1 for filing-time shares,
equity, assets, liabilities, revenue, earnings, and EPS where the public filing
taxonomy provides them. DAILY and SF1 must not be used as historical evidence
under the current entitlement.

Required next access action:

1. preserve the completed bundle and provider-license evidence before
   2026-08-10;
2. resolve or explicitly preregister a conservative treatment for delisting
   settlement returns, which the free stack does not independently verify; and
3. reassess renewal only if ongoing post-expiry Sharadar updates are needed.

If those captures complete with adequate coverage and permitted retention, the
$50 recurring plan is not required solely to run the frozen historical
experiments. A future subscription or replacement would still be needed for
ongoing post-expiry updates, newly listed/delisted securities, corporate
actions, and continued forward PIT research.

## SEC and remaining free-source status

- SEC needs no API key. The existing declared Caerus research identity was used
  as an ephemeral User-Agent and was not persisted in any data artifact.
- The SEC filing index contains 6,787,313 Form 4/4-A/8-K/8-K-A rows from 2012
  through 2026. The Company Facts bulk archive is preserved, and the compact
  panel contains 7,586,559 filing-time facts across 19,987 issuer CIKs.
- The 58 official quarterly insider archives cover 2012 Q1 through 2026 Q2.
  Their 4,186,218-row normalized tape is discovery data, not certified event
  time: exact EDGAR acceptance time and original XML/amendment lineage remain
  mandatory under the frozen insider hypothesis.
- A deterministic selector identified 316,822 original-filing candidates tied
  to plausible natural-person open-market purchases. The time-stratified
  500-filing audit preserved and parsed all 500 originals and recovered exact
  acceptance time and issuer identity in 100% of the sample. Reconciliation to
  the lossy quarterly extract was 98.0% for owner sets, 96.6% for purchase
  transaction fields at the extract's two-decimal precision, 95.4% for purchase
  value within 10 bps, and 90.2% for role flags. This fails the frozen 99%
  lineage gate and proves that original XML, not the quarterly flat extract,
  must be the canonical full-history input.
- EIA needs no key for the captured gas/petroleum bulk data. A free EIA key is
  needed only for narrow API queries; the full electricity archive and selected
  controls are already captured without one.

Free-source continuation update (2026-07-15 21:56 ET):

- the official SEC bulk submissions archive is preserved in immutable bundle
  `sec_submissions/20260715T210359Z-28aac69e380e` (1.55 GB);
- the corrected SEC Item 2.02 tape contains 257,996 frozen-window rows. SEC
  acceptance remains filing availability, not issuer announcement time;
- the original 8-K work queue contains 313,450 deduplicated earnings/delisting
  filings. The delisting sub-index contains 55,454 filing candidates joined to
  8,196 of 17,712 scoped actions and remains candidate evidence only;
- the Sharadar SF1 bulk export was attempted before expiry and confirmed the
  entitlement restriction: the archive contains only 60 data rows (30 sample
  tickers, two annual periods), so SEC filing-time facts remain the historical
  fundamentals source;
- the official EIA electricity archive and 1,472 selected monthly controls were
  captured; and
- BEA reference files were captured. The ignored `.env` still needs the free
  `BEA_API_KEY` and `ALPHA_VANTAGE_API_KEY` before those keyed proxy collectors
  can acquire data. The Alpha Vantage adapter was exercised against its IBM
  demo response only; that one-name validation is not universe coverage; and
- a first yfinance current aggregate analyst snapshot captured all 200 requested
  names, with 199 usable records. It is explicitly not a historical PIT or
  analyst-level substitute; and
- the first USAspending trial exposed multiple recipient identities behind some
  exact-normalized names. Those trial edges were rejected; the current v3
  collector accepts only a unique exact recipient search term and records
  ambiguous names instead of guessing an issuer relationship. Its first v3
  partition retained 985 award edges for two unique issuer matches, rejected
  one ambiguous name, and had zero errors or truncations; and
- the partitioned original Form 4 and combined 8-K collectors are validated and
  resumable. They are long-running fair-access jobs rather than instant bulk
  downloads.

## Irreducible frozen-contract gaps

- HYP-2026-002 still needs licensed analyst-level historical estimates with
  original publication, correction, withdrawal, and contributor lineage.
- HYP-2026-004 still needs historical option trades, quotes, open interest, and
  surface/Greek inputs. The free yfinance collector is forward proxy evidence,
  not a historical replacement.
- HYP-2026-005 still needs effective-dated supplier/customer relationship
  history with source and announcement lineage.
- True delisting settlement returns are not independently available in the free
  stack; the compiled price panel must remain fail-closed on that certification
  until a defensible source or conservative preregistered treatment is approved.

## Post-build frozen data gates

The final 2026-07-15 19:40 UTC gate run verified every frozen specification hash and
read no returns or holdout observations. Shared security identity, security-
existence membership, CIK mapping, factors, sectors, commodities, and filing-
time characteristics now pass. All four experiments remain `BLOCKED_DATA`:

| Hypothesis | Remaining blocked assets |
|---|---|
| HYP-2026-002 earnings revisions | delisting-settlement certification; earnings-event tape; licensed analyst-estimate history |
| HYP-2026-003 insider clusters | delisting-settlement certification; original-XML Form 4 full-history lineage |
| HYP-2026-004 options information | delisting-settlement certification; earnings-event tape; historical option trade/quote tape |
| HYP-2026-005 supply-chain diffusion | delisting-settlement certification; earnings-event tape; analyst history; effective-dated supply-chain graph |

This is a successful fail-closed data build, not evidence for or against alpha.

No paid purchase has been made or authorized by this build.
