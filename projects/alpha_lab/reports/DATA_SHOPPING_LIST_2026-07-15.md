# Alpha Lab Data Shopping List

Date: 2026-07-15

Scope: shared point-in-time research spine plus the four frozen experiments:
earnings revisions, insider conviction, options information, and supply-chain
shock diffusion.

Governance: `RESEARCH_ONLY` / `NON_EXECUTIONAL` / `NO_RUNTIME_CHANGE`

Pricing basis: provider pages and interactive calculators checked on 2026-07-15.
Prices exclude tax, data-delivery, commercial-use, redistribution, and exchange
pass-through fees unless the provider page explicitly includes them. A
"planning allowance" is an internal procurement reserve, not a vendor quote.

## CIO answer

Owner direction recorded 2026-07-15: **defer the Cboe purchase and exhaust the
free forward proxy first.** The revised minimum honest procurement plan is:

1. ingest the free sources and exhaust the active Sharadar SEP/TICKERS/ACTIONS
   entitlement before buying a second equity-data spine;
2. operate the standalone research-only yfinance current-chain proxy and insider lane for `$0`
   incremental data cost;
3. defer the exact two-month Cboe options pilot until at least 60 matured proxy
   cohorts support a preliminary spend review;
4. obtain free, narrowly scoped evaluation extracts from LSEG and FactSet before
   discussing full-feed licenses; and
5. buy no full historical signal feed until its sample passes the frozen
   timestamp, identifier, amendment, and corporate-action audits.

The minimum near-term external-data budget is therefore **`$0`**, assuming the
existing Sharadar entitlement remains usable and LSEG/FactSet provide evaluation
samples or trials. The documented **`$1,410` one-time** Cboe cart remains the
conditional next purchase, not an approved or immediate outlay.

## Required shopping list

| Priority | Data / preferred source | Needed for | Free or paid | Cost and cadence | Subscribe or download | Decision |
|---:|---|---|---|---|---|---|
| 1 | Sharadar Core US Equities: SEP prices including delisted names, TICKERS/permaticker identity, and ACTIONS | Shared PIT security existence, prices, liquidity, and corporate actions; all four lanes | Paid; currently entitled through 2026-08-10 | **Incremental `$0` during the current access window.** Recurring renewal is `$50` for the present plan; business/fund use and derived-data retention still require license review. | [Sharadar publisher page](https://data.nasdaq.com/publishers/SHARADAR), [coverage](https://www.sharadar.com/data), [Nasdaq Data Link account access](https://data.nasdaq.com/about) | **Capture in progress; do not repurchase yet.** TICKERS and ACTIONS are complete; the 2011-2026 survivorship-aware SEP stream is resumable. Strong probes show SF1 and DAILY are sample-only, so SEC Company Facts and causal price-derived characteristics replace them. |
| 1 | SEC EDGAR original Form 4/4-A XML, 8-K/8-K-A filings, acceptance timestamps, accession IDs, filing indexes, and XBRL facts | Insider events, earnings-event availability, issuer identity support | Free | `$0`; ongoing download/API access | [EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [access guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data), [bulk submissions ZIP](https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip) | **Download now.** Use SEC acceptance time as availability; retain amendments rather than overwriting originals; respect the SEC request-rate policy. |
| 1 | SEC CIK/ticker/exchange associations | Current issuer cross-check and forward CIK mapping | Free | `$0`; snapshot each refresh | [SEC mapping JSON](https://www.sec.gov/files/company_tickers_exchange.json) | **Download now, but never use alone as a historical security master.** CIK is the issuer identity; ticker remains date-effective display metadata. |
| 1 | Kenneth French daily FF5, momentum, short-term reversal, and industry portfolios | Market beta, size, value, profitability, investment, momentum, reversal, and industry attribution | Free | `$0`; snapshot/hash each research release | [Kenneth French Data Library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html) | **Download now.** Use one common versioned factor panel for every lane. |
| 1 | AQR Betting Against Beta and Quality Minus Junk research factors | Low-volatility/BAB and quality controls | Free public research download | `$0`; files update with a publication lag | [AQR Data Library](https://www.aqr.com/Insights/Datasets), [methodology and update notes](https://www.aqr.com/insights/datasets/about-the-aqr-data-library) | **Download now.** Treat as hypothetical research portfolios and preserve the downloaded vintage. |
| 1 | FRED/ALFRED rates, liquidity, credit, inflation, and volatility series | Shared macro controls and supply-chain shock controls | Free | `$0`; free API key/account | [FRED API](https://fred.stlouisfed.org/docs/api/fred/), [observations and realtime parameters](https://fred.stlouisfed.org/docs/api/fred/series_observations.html), [vintage dates](https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html) | **Register and ingest now.** Use ALFRED vintages for revisable series and store observation, release, and retrieval dates separately. |
| 1 | EIA petroleum, natural-gas, electricity, and energy-fundamental series | Commodity/input-cost controls for supply-chain diffusion | Free | `$0`; free API key, bulk files require no key | [EIA Open Data](https://www.eia.gov/opendata/), [API registration](https://www.eia.gov/opendata/register.php) | **Ingest now.** Enforce the source's release timestamp and preserve corrected vintages. |
| 1 | LSEG I/B/E/S analyst detail, actuals, History, and Point-in-Time Estimates | Frozen earnings-revision signal; analyst breadth; supply-chain customer estimate shock | Paid, custom quote | No public amount. PIT refresh is documented as weekly and History delivery as monthly. **Request a `$0` evaluation sample first.** Do not set a full-feed budget until LSEG quotes the exact research license. | [I/B/E/S Estimates](https://www.lseg.com/en/data-analytics/financial-data/company-data/ibes-estimates) | **Request a 100-security, two-earnings-season extract.** It must include analyst-level EPS and revenue forecasts, original timestamps, withdrawals, actuals, fiscal-period lineage, and PIT symbology. |
| 1A | FactSet Estimates Detail plus Estimates Point-in-Time Consensus | Alternative/bid comparison for the same earnings-revision contract | Paid, custom quote; trial offered | Public amount unavailable. **`$0` trial first**; full feed is recurring and quote-based. | [Estimates Detail](https://www.factset.com/marketplace/catalog/product/factset-estimates-detail), [PIT Consensus](https://www.factset.com/marketplace/catalog/product/factset-estimates-point-in-time-consensus) | Ask FactSet to bundle the estimates trial with Revere below. PIT consensus alone is insufficient because it cannot prove analyst breadth; Detail must retain original revisions and withdrawals. |
| 1 | Cboe Option Trades with Calcs | Frozen options trade classification: OPRA-wide prints, execution exchange, contemporaneous NBBO and underlying bid/ask, IV, and Greeks | Paid; historical purchase is one-time | **`$990` one-time** for the exact pilot priced on 2026-07-15: 50 specified liquid underlyings; 2024-01-02–2024-01-31 plus 2024-07-01–2024-07-31; Calcs included. Daily subscriptions are recurring and separately priced. | [Cboe Option Trades](https://datashop.cboe.com/option-trades) | **Buy only after confirming license and file sample.** This is the primary trade/NBBO input for the cheapest honest options test. |
| 1 | Cboe Option EOD Summary with Calcs | Matching 15:45/EOD complete option-series snapshots, NBBO/size, underlying quotes, volume, VWAP, open interest, IV, and Greeks | Paid; historical purchase is one-time | **`$420` one-time** for the same 50 underlyings and two date windows, Calcs included, priced on 2026-07-15. | [Cboe Option EOD Summary](https://datashop.cboe.com/option-eod-summary) | **Buy with Option Trades.** It supplies the chain denominator, open-interest gate, and 15:45 volatility surface that the trade tape alone does not. |
| 1 | OCC contract-adjustment information memos and special-settlement records | Option root/deliverable lineage for splits, mergers, special dividends, cash settlements, and symbol changes | Free for the public search/reports | `$0`; download/snapshot relevant records | [OCC Information Memos](https://infomemo.theocc.com/infomemo/search-memo), [Equity Special Settlements](https://www.theocc.com/market-data/market-data-reports/series-and-trading-data/equity-special-settlements) | **Use for the pilot.** Exclude any contract whose historical deliverable cannot be resolved. |
| 1 | FactSet Revere Supply Chain Relationships | PIT customer/supplier graph, direction, relationship context, disclosed revenue dependency, and graph history | Paid, custom quote; trial offered | Public amount unavailable. **`$0` trial first.** A full recurring data-feed license requires a quote. | [FactSet Supply Chain Relationships](https://www.factset.com/marketplace/catalog/product/factset-supply-chain-relationships) | **Request the frozen 100-edge/30-customer-event sample.** Require source document, first-known/publication timestamp, effective dates, direction, dependency, termination status, and historical as-of extracts. |

## Alternatives and validation tools

These are useful negotiating leverage or engineering aids. They are not all
substitutes for the preferred source under the frozen contracts.

| Candidate | Role | Free or paid | Cost and cadence | Link | Buy rule |
|---|---|---|---|---|---|
| Intrinio analyst/EPS/sales estimates | Lower-cost earnings-revision candidate | Paid recurring; trial available | **`$1,250/month+`** Enterprise floor (`$15,000/year+`); historical access has an additional one-time fee that is not posted | [Analyst Estimates](https://intrinio.com/products/analyst-ratings), [EPS Estimates](https://intrinio.com/products/eps-estimates), [Sales Estimates](https://intrinio.com/products/sales-estimates), [pricing](https://intrinio.com/pricing) | Trial only until a sample proves analyst-level original timestamps, withdrawals, revenue/EPS lineage, and PIT identifiers. |
| Databento OPRA | Raw options ingestion/mapping and exchange-total parity | Usage-based paid historical data; introductory credit | Exact `$ / GB` estimator; **`$125` historical-data credit** for new users; no historical subscription required | [Databento pricing](https://databento.com/pricing) | Use the credit for engineering only. It does not by itself prove the complete Greeks and OCC-adjustment lineage frozen for the experiment. |
| Cboe Enhanced Trade-by-Trade | Ground-truth audit of our aggressor-side classifier | Paid recurring or one-time history; trial available | **Up to one month free for first-time users; `$8,000` per historical month; `$12,000/month` subscription** | [product](https://datashop.cboe.com/enhanced-us-options-trade-by-trade-execution-detail), [Cboe fee schedule](https://cdn.cboe.com/resources/membership/Cboe_FeeSchedule.pdf) | Take the free month only for classifier validation. It is C1-only and cannot satisfy the frozen three-exchange/OPRA-wide requirement. |
| Intrinio options | Cheap schema/feature engineering | Paid recurring; trial available | **`$150/month`** Individual (`$1,800/year`) | [Intrinio pricing](https://intrinio.com/pricing) | Engineering only. Public coverage does not establish full trade-by-trade condition codes plus contemporaneous trade/NBBO alignment, so it cannot support the alpha conclusion. |
| OCC Product/Series file | Scalable authoritative option product/series reference | Paid recurring | **`$1,750/month`** non-distribution (`$21,000/year`) or `$3,000/month` distribution for non-clearing members | [OCC Data Sales](https://www.theocc.com/market-data/market-data-reports/other-market-data-info/data-sales) | Do not buy for the two-month pilot. Consider only if manual memo/contract mapping passes and the program scales. |
| S&P Global Business Relationships Analytics | Supply-chain graph fallback | Paid, custom quote | No public price | [S&P product page](https://www.marketplace.spglobal.com/en/datasets/business-relationships-analytics-%281739270615%29) | Require disclosed/raw—not modeled—relationships with source, first-known date, direction, dependency, and termination date. |
| Norgate US Stocks Platinum | Equity PIT spine fallback if Sharadar cannot be restored | Paid recurring | **`$630/year`** or **`$346.50/6 months`** | [packages/pricing](https://norgatedata.com/stockmarketpackages.php), [subscribe/trial](https://norgatedata.com/subscribe/subscribe.php) | Do not buy alongside Sharadar. Windows dependency, export limits, and commercial/fund licensing must be resolved first. |
| CFTC Commitments of Traders | Optional commodity-positioning control | Free | `$0`; weekly release | [historical files](https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed/index.htm), [release schedule](https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm) | Optional. Timestamp the signal on Friday release, not the report's Tuesday observation date. |
| Nasdaq Trader symbol directories | Current listing cross-check and forward snapshots | Free | `$0`; updated during the trading day | [symbol directory definitions/files](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs) | Cross-check only. It is not a historical membership, delisting, or identifier-lineage source. |

## Recommended procurement sequence

### Phase 0 — `$0` now

- confirm the existing Nasdaq Data Link/Sharadar entitlement, exact tables, and
  permitted use;
- download and version SEC, French, AQR, FRED/ALFRED, EIA, OCC, and current
  symbol-mapping inputs;
- request the combined FactSet Estimates Detail/PIT/Revere trial;
- request the matching 100-security I/B/E/S evaluation extract; and
- use Databento's `$125` credit only for raw OPRA schema and mapping work.

### Phase 1 — `$0` forward proxy observation

Use the standalone research automation under `projects/alpha_lab/options_proxy/` to
capture current yfinance chains at or after 15:45 ET. Review proxy coverage at
20 matured cohorts and preliminary data-spend merit at 60. Proxy evidence never
satisfies the frozen trade-level hypothesis and never authorizes promotion.

### Phase 2 — conditional `$1,410` one-time

Purchase the two Cboe historical products together only after sample layouts and
license terms pass review **and** the free proxy earns a preliminary spend
review:

| Cboe pilot component | One-time price |
|---|---:|
| Option Trades, two non-adjacent 2024 months, 50 underlyings, Calcs included | `$990` |
| Option EOD Summary, matching symbols/months, Calcs included | `$420` |
| **Total** | **`$1,410`** |

This cart is intentionally narrow. It is sufficient to attempt the frozen
mapping, volume-parity, manual-classification, chain/OI, and directional checks;
it is not a license for the full 2014-2026 experiment.

The 50-underlying calculator basket was:

`AAPL MSFT NVDA AMZN GOOGL META AVGO TSLA BRK.B JPM LLY V UNH XOM COST MA`
`HD PG JNJ NFLX ABBV BAC KO CRM CVX ORCL WMT MRK AMD PEP CSCO TMO MCD ACN`
`IBM GE CAT QCOM TXN AMGN GS HON RTX LIN DIS NKE UPS LOW SBUX SPY`.

### Phase 3 — conditional only

- If I/B/E/S and FactSet samples both pass, select one estimates source based on
  timestamp fidelity, analyst breadth, identifier lineage, use rights, and the
  scoped/full-history quote—not brand.
- If Revere's 100-edge sample passes its historical as-of audit, negotiate a
  research-only extract before a recurring full feed.
- If the Cboe pilot passes, obtain a full-history quote sized to the exact
  optionable universe and required 2014-2026 windows. Do not extrapolate the
  `$1,410` pilot cart into a full-history budget.
- If Sharadar cannot be restored or cannot supply a decision-grade large-cap
  membership/characteristics spine, compare its commercial renewal quote with
  the Norgate fallback and any permissible derived-membership method before
  buying a second vendor.

## Data acceptance gates

No paid dataset counts as acquired merely because it can be downloaded. Before
the full experiment, it must pass all applicable gates:

- immutable raw files, checksums, data dictionary, license, and retrieval time;
- source event time and model `available_at` time kept separately;
- effective-dated issuer/security/option identifiers, including delistings and
  corporate actions;
- amendments, withdrawals, restatements, and terminations retained rather than
  overwritten;
- historical membership and missingness that do not depend on today's
  survivors;
- vendor aggregates reconciled to independent totals where available; and
- no forward-return join until the frozen signal artifacts and audit evidence
  are sealed.

## Do not buy as decision-grade evidence

- current consensus snapshots without analyst-level revision history;
- current customer/supplier graphs backfilled into the past without first-known
  timestamps;
- free/current ticker lists presented as historical universe membership;
- retail option chains or EOD bars presented as trade/prevailing-NBBO history;
- C1-only native-side data presented as OPRA-wide, multi-exchange coverage; or
- yfinance-derived fundamentals or analyst fields presented as point-in-time.

Those products may accelerate schema development, but they cannot support a
decision-grade alpha claim under the frozen hypotheses.
