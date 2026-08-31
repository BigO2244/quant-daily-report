# HYP-2026-003 — Insider-Conviction Clusters

Governance label: `RESEARCH_ONLY`

Execution impact: `NON_EXECUTIONAL`

Canonical lineage: this tests the Form 4 event family already identified as a
Cassiopeia research direction. It does not create, rename, activate, promote,
or alter Cassiopeia or any canonical strategy.

## Claim

When at least two independent corporate insiders commit personal capital to
open-market purchases in the same issuer within 10 calendar days, a broad
long-only basket entered only after the second filing is publicly available
will earn positive factor-adjusted, transaction-cost-net returns over the next
60 trading days relative to otherwise comparable single-insider purchases.

## Initial classification

`ALPHA_CANDIDATE`

## Economic mechanism

- Open-market purchases expose insiders to loss and are less mechanically
  motivated than grants, exercises, gifts, or tax-withholding sales.
- Agreement by independent insiders may aggregate private assessments of
  valuation or operating prospects while filtering one person's liquidity or
  signaling motive.
- Filing delays, sparse events, identity resolution, and legal transaction-code
  parsing make the signal operationally harder to capture and may preserve it.
- Caerus can wait for public SEC acceptance, enter conservatively at the next
  session, hold for months, and diversify across issuer clusters.
- A small-company, value, reversal, sector, or market-beta premium is not the
  selection alpha claimed here and must be controlled explicitly.

## Prediction

- Expected sign: positive for qualifying purchase clusters relative to single-
  purchase and matched non-event baselines.
- Forecast horizon: 60 trading days after the eligible cluster completes.
- Expected decay: little reliable advantage at 1 day; increasing differentiation
  over 20-60 days; no required persistence beyond 120 days.
- Applicable universe: PIT-active US common equities with price >= `$5` and
  trailing 20-day median dollar volume >= `$5M` on the eligible entry date.
- Conditions where it should be strongest: purchases by operating executives,
  larger purchase value relative to issuer market capitalization, and agreement
  among three or more unrelated insiders.
- Conditions where it should fail: grants/exercises mislabeled as purchases,
  controlled-person duplicates, purchases made under prearranged plans, highly
  distressed financing contexts, or returns entirely explained by size/value/
  reversal exposure.

## Point-in-time data contract

- Source: SEC EDGAR submissions plus original Form 4 XML and amendments; canonical
  PIT security/universe artifacts; adjusted OHLCV/corporate actions from the
  approved research source. SEC structured extracts may aid discovery but do not
  replace original filing lineage.
- Observation timestamp: transaction date and each filing's EDGAR
  `acceptanceDateTime`, preserved in UTC and America/New_York.
- Availability timestamp: EDGAR acceptance is authoritative. Every filing is
  eligible no earlier than the next full regular-session open after acceptance,
  regardless of whether it was accepted premarket or intraday.
- Security identifier: issuer CIK plus effective-dated security ID; reporting-
  owner CIK is the primary person identity. Ticker is display-only.
- Delisting and corporate-action handling: retain delisted issuers and failed
  companies; resolve membership on the entry date; adjust prices and share counts
  for splits/mergers/spin-offs without rewriting historical filing fields.
- Missing-data rule: fail closed on missing acceptance time, unparseable XML,
  unresolved issuer/person identity, ambiguous derivative/non-derivative status,
  absent PIT membership, missing price/liquidity, or amendment lineage. No event
  is inferred from transaction date alone.
- Known coverage limitations: late filings; amended filings; historical owner-
  identity aliases; indirect holdings and controlled entities; inconsistent
  officer-title strings; footnote-only 10b5-1 or relationship disclosures; and
  lower large-cap event frequency.

## Frozen event, signal, and portfolio

- Eligible purchase: non-derivative Form 4 transaction code `P`, positive shares
  and cash price, natural-person officer/director/10% owner, not marked as an
  exercise, grant, gift, tax withholding, issuer repurchase, or derivative.
- Cluster: at least two independent reporting-owner CIKs whose eligible filings
  for one issuer are accepted within 10 calendar days. Owners under common
  control, duplicate beneficial-owner vehicles, and amended repeats count once.
- Event time: the acceptance time of the filing that first satisfies the second-
  independent-insider requirement. A 60-trading-day issuer cooldown begins at
  entry; later purchases enrich attribution but do not create overlapping events.
- Primary score:
  - `50%` percentile rank of distinct eligible buyers, capped at four;
  - `30%` percentile rank of aggregate purchase dollars / PIT market cap;
  - `20%` role score, with CEO/CFO `1.0`, other named executive officer `0.75`,
    other officer `0.50`, and director/10% owner `0.25`, averaged across buyers.
- Enter at the next regular-session open after cluster completion; hold 60 trading
  days. Equal weight active events, maximum 10% per issuer, maximum 10 names;
  unused weight remains cash. No price confirmation, stop, optimization, or
  regime overlay is used.

## Baselines and risk model

- Primary investable baseline: all otherwise eligible single-insider code-`P`
  purchases, using the same entry, 60-day hold, issuer cooldown, portfolio caps,
  and cost model. The first eligible filing creates a single-purchase event. If
  a later filing first completes a qualifying cluster, the existing single
  position continues under its original frozen hold, while the cluster-
  completing filing creates only the cluster event and never a second single
  event. Later purchases during the cluster cooldown enrich attribution only.
  No earlier single event is removed or reclassified using future information.
  Filings with the exact same SEC acceptance timestamp are evaluated as one
  public-information batch; if that batch completes a cluster, none of its
  filings creates an artificial earlier single event.
- Simple signal baseline: equal-weight PIT universe matched on date, sector,
  log market cap, book-to-market proxy, prior 60-day return, and liquidity.
- Factor controls: daily `MKT-RF`, `SMB`, `HML`, `RMW`, `CMA`, `UMD`, a
  predeclared low-volatility/BAB proxy, and sector returns; matched-event CARs use
  the same characteristics measured before filing availability.
- Portfolio-utility comparator, if not an alpha claim: matched-date Polaris,
  Orion, Lyra, and SPY return series, not used to select parameters.

## Frozen experiment

- Primary metric: 60-trading-day factor-adjusted net calendar-time portfolio
  alpha of clusters minus otherwise eligible single purchases.
- Secondary diagnostics: matched-event residual CAR at 5/20/60/120 days; hit
  rate; cluster-size and role cohorts; rank IC; raw/SPY-relative return; filing
  delay; event coverage; overlapping-position count; turnover; drawdown; sector/
  factor exposures; existing-sleeve overlap/correlation; issuer/event/year
  concentration; capacity; and 2x-cost sensitivity.
- Walk-forward design: 2012-2018 discovery/calibration only; expanding annual
  walk-forward evaluation for 2019-2024. Owner/issuer identity rules are frozen
  before returns are joined. Each issuer-event cluster is one inference unit;
  calendar-time and event inference cluster by issuer and event month.
- Untouched challenge period: 2025-01-01 through 2026-06-30, preserving the
  existing Form 4 2025+ holdout. It is read once after all eligibility,
  deduplication, and scoring validations pass.
- Cost and capacity assumptions: next-open execution; 15 bps per side base and
  30 bps per side stress unless quote-based spread plus impact is higher. Each
  order must be <= 5% of trailing 20-day median dollar volume at `$100K`, `$1M`,
  and `$10M` reference capital; capacity failures remain in diagnostics.
- Maximum variants in this family: 5 total, including the primary. Allowed
  alternatives are CEO/CFO-required, three-insider minimum, purchase-value floor
  of 5 bps of market cap, and 20-day hold. The 120-day return remains a
  descriptive secondary diagnostic only and cannot enter promotion, parameter
  selection, or the multiple-testing family. Cluster windows other than 10
  calendar days require a new hypothesis ID.
- Multiple-testing correction: Romano-Wolf/max-T block bootstrap at one-sided
  `alpha=0.10` across all five variants; resample issuer clusters and event months
  so same-issuer and same-market-event observations never become independent.

## Pass criteria

Further work is justified only if the frozen primary variant:

1. has challenge-period 60-day cluster-minus-single-purchase net alpha >= 2%
   annualized with the adjusted one-sided 90% lower confidence bound above zero;
2. has positive factor-adjusted cluster event CAR at both 20 and 60 days in at
   least four of the six 2019-2024 validation years and in the challenge period;
3. includes at least 200 independent issuer clusters historically and at least
   30 in the challenge period;
4. remains positive at 2x costs and after size/value/reversal matching;
5. derives no more than 20% of active return from one issuer, 50% from the top
   five issuers, or 50% from one calendar year; and
6. supports `$1M` reference capital under the 5%-ADV rule.

Passing supports continued research or a separately approved forward-shadow
request, not Cassiopeia activation or promotion.

## Kill criteria

Kill or park if filing/entity lineage is not PIT-safe; cluster independence
cannot be resolved; fewer than 200 historical or 30 challenge clusters survive;
challenge alpha is non-positive; corrected significance fails; 2x costs erase
the edge; size/value/reversal controls explain it; contributor limits fail;
capacity is below `$1M`; or minor identity/dollar filters reverse the sign.
Any use of the preserved holdout to choose owner roles, cluster windows, or
dollar thresholds voids the experiment.

## Cheapest honest next test

Rebuild a stratified sample of at least 500 original Form 4 filings from SEC XML,
including amendments and indirect holdings. Blind to forward returns, require
99%+ agreement for acceptance time, transaction code, issuer, owner CIK,
purchase dollars, role, and independent-person deduplication. Then compare only
the frozen 60-day residual CAR of qualifying clusters with eligible single
purchases in the non-holdout sample. Failure of identity/PIT reconciliation,
adequate cluster count, or positive direction stops the full build.

## Freeze record

- Frozen by: Brett Olson, CIO, via explicit `FREEZE HYPOTHESIS, then RUN EXPERIMENT`; drafted by Codex.
- Frozen at: 2026-07-14, America/New_York.
- Governance clarification approved by Brett Olson on 2026-08-03 before any
  return or challenge-period access: causal single comparator uses Option B;
  120-day hold removed from the formal variant family and retained only as a
  descriptive diagnostic.
- Original spec hash: `sha256:e2195542db5acee6c5825b6ee8fa660e212c4ba632b26d72d43c3e4b3f919992`.
- Spec hash: `sha256:c8426e20909f2a45e936b06339defae99b06989c3fbce16333456cf418d3f75b` (all bytes before `## Freeze record`).
- Code hash: no experiment code existed at freeze; repository baseline `4d15ade69799a0eff161d5e9819e4d9d574de66d`.
- Data snapshot/hash: `NOT_ACQUIRED`; SEC acquisition and PIT audit are part of the frozen experiment.
