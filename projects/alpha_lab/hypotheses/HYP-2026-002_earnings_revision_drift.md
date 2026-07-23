# HYP-2026-002 — Earnings-Revision Drift

Governance label: `RESEARCH_ONLY`

Execution impact: `NON_EXECUTIONAL`

Canonical lineage: this is the Alpha Lab preregistration for a new test of the
existing Caerus Cygnus / FR-051 `earnings_drift` research family. It does not
create, rename, reactivate, promote, or alter a canonical strategy.

## Claim

Among liquid, point-in-time-eligible US equities following a completed earnings
event, a broad long-only basket with unusually positive analyst EPS/revenue
revision breadth and magnitude will earn positive factor-adjusted, transaction-
cost-net returns over the next 20 trading days relative to both the eligible
universe and a price-momentum baseline.

## Initial classification

`ALPHA_CANDIDATE`

## Economic mechanism

- Analysts update forecasts asynchronously after new operating information, and
  institutions with review or mandate constraints may rebalance gradually.
- Breadth across independent analysts is harder to manufacture than a single
  target-price change and should identify information that is still diffusing.
- The effect may persist because complete point-in-time estimate history is
  licensed, operationally difficult to reconstruct, and easy to contaminate
  with vendor-restated consensus snapshots.
- Caerus can capture it with a multi-week long-only holding period, conservative
  next-session entry, and a broad basket rather than relying on fine rank order.
- A return explained by market beta, sector, size, value, profitability,
  investment, momentum, or low-volatility exposure is factor harvest, not the
  alpha claimed here.

## Prediction

- Expected sign: positive for the highest revision-score cohort relative to the
  equal-weight eligible universe and momentum-matched controls.
- Forecast horizon: 20 trading days from the first eligible portfolio entry.
- Expected decay: strongest during days 2-20 after the revision cluster and
  substantially weaker by day 60.
- Applicable universe: PIT-active US common equities with close >= `$5`,
  trailing 20-day median dollar volume >= `$20M`, valid corporate-action history,
  and at least three distinct contributing analysts for FY1 estimates.
- Conditions where it should be strongest: broad upward revisions following a
  positive or non-failed earnings reaction, low analyst disagreement, and no
  conflicting company guidance.
- Conditions where it should fail: one-analyst changes, high-dispersion or stale
  consensus, immediate full price adjustment, broad risk-on momentum rallies,
  or revisions published too late to enter economically.

## Point-in-time data contract

- Source: a licensed analyst-level estimate archive such as LSEG I/B/E/S, or an
  alternate source only after a field-level PIT audit proves equivalent original
  publication timestamps and revision lineage; earnings releases/8-K filings
  from SEC EDGAR; canonical FR-068 PIT security/universe artifacts; adjusted
  OHLCV and corporate actions from the approved research price source.
- Observation timestamp: each individual analyst forecast's original vendor
  publication timestamp; each reported actual/guidance item's source timestamp;
  daily prices as of the official market close.
- Availability timestamp: the later of vendor publication and Caerus ingestion.
  Same-day analyst observations are eligible only for a decision after the close
  and an entry at the next regular-session open. Restated consensus snapshots
  without original constituent timestamps are ineligible.
- Security identifier: vendor estimate entity ID mapped through effective-dated
  CIK/CUSIP/FIGI/security-ID lineage; ticker is display-only.
- Delisting and corporate-action handling: membership is resolved as of each
  decision date; delisted securities remain in the panel; splits, mergers,
  spin-offs, symbol changes, and estimate-per-share histories must be adjusted
  on a consistent PIT basis.
- Missing-data rule: fail closed for unknown publication time, unresolved entity
  mapping, fewer than three contributing analysts, absent PIT membership, stale
  price, missing benchmark, or incomplete adjustment lineage. Missing is never
  encoded as neutral or zero.
- Known coverage limitations: vendor history, contributor coverage, small-cap
  coverage, fiscal-period remapping, and treatment of withdrawn forecasts vary
  through time. The current `scalemarketcap` large-cap family is not accepted as
  PIT-valid membership until the FR-068 blocker is resolved.

## Frozen signal and portfolio

- Decision cadence: weekly, after Friday close; enter at the next regular-session
  open. Holiday weeks use the last open session.
- Event eligibility: the most recent earnings release must have been publicly
  available 1-60 trading days before the decision and the next earnings event
  must not be scheduled within five trading days.
- Revision window: the 20 trading days ending at the decision timestamp.
- Primary score, computed cross-sectionally within sector and then percentile-
  ranked:
  - `40%` net FY1 EPS revision breadth: `(up analysts - down analysts) / distinct analysts`;
  - `30%` median analyst FY1 EPS percentage revision, winsorized at the
    contemporaneous 1st/99th percentiles;
  - `15%` FY1 revenue-consensus percentage change;
  - `15%` reduction in FY1 EPS forecast dispersion, scored positively only when
    the consensus change is positive.
- Select the top decile, capped at 10 names; equal weight; maximum 10% per name;
  unfilled weight remains cash. Hold 20 trading days, with overlapping weekly
  vintages combined and each name capped at 10%.
- No price momentum, optimization, regime switch, stop-loss, or dynamic
  reweighting enters the primary signal.

## Baselines and risk model

- Primary investable baseline: equal-weight eligible-universe portfolio using
  identical entry, holding, cash, and cost conventions.
- Simple signal baseline: top-decile 12-1 price momentum with the same universe,
  weekly rebalance, 10-name cap, and 20-day hold.
- Factor controls: daily `MKT-RF`, `SMB`, `HML`, `RMW`, `CMA`, `UMD`, a
  predeclared low-volatility/BAB proxy, and sector returns; also report beta-
  normalized and momentum-matched results. Factors are evaluation controls, not
  signal inputs.
- Portfolio-utility comparator, if not an alpha claim: Polaris, Orion, and Lyra
  shadow return series over exactly matched dates, reported separately from the
  primary alpha test.

## Frozen experiment

- Primary metric: annualized intercept of the transaction-cost-net daily return
  difference between the candidate and primary investable baseline under the
  frozen factor model.
- Secondary diagnostics: Spearman rank IC for 20-day residual returns; top-
  decile minus universe return; bucket monotonicity; raw/SPY-relative return;
  turnover; drawdown; hit rate; sector/factor exposure; overlap and return
  correlation with existing sleeves; contributor concentration; event-age
  decay; capacity; and results at 2x costs.
- Walk-forward design: 2014-2018 is the only discovery/calibration window.
  Expanding-window models are frozen at each year-end and evaluated one calendar
  year ahead for 2019-2024. Cross-sectional transforms may use only information
  available on each date; no test-year outcome may change a later feature or
  threshold under this hypothesis.
- Untouched challenge period: 2025-01-01 through 2026-06-30, consistent with the
  preserved Cygnus 2025+ holdout. It is read once, after code/data validation and
  all variant results are permanently recorded.
- Cost and capacity assumptions: next-open equity execution; 15 bps per side
  base all-in cost and 30 bps per side stress cost unless quote-based spread plus
  impact is higher. Each proposed order must be <= 5% of trailing 20-day median
  dollar volume at reference capitals of `$100K`, `$1M`, and `$10M`; report the
  binding capacity rather than silently dropping names.
- Maximum variants in this family: 6 total, including the primary. The only
  allowed alternatives are breadth-only, magnitude-only, EPS-plus-revenue
  without dispersion, earnings-confirmed revisions, 10-day hold, and 40-day
  hold. Any other change requires a new hypothesis ID.
- Multiple-testing correction: Romano-Wolf/max-T stationary-block bootstrap
  family-wise error control at one-sided `alpha=0.10` across all six registered
  variants. Blocks are calendar weeks; issuer observations remain in the same
  resample block.

## Pass criteria

Further work is justified only if the primary variant, without selecting an
alternate after seeing the holdout:

1. has positive challenge-period factor-adjusted net alpha with the adjusted
   one-sided 90% lower confidence bound above zero and annualized alpha >= 2%;
2. has positive 20-day residual rank IC in at least four of the six 2019-2024
   walk-forward years and in the locked challenge period;
3. remains positive at 2x costs and after beta/momentum matching;
4. does not derive more than 20% of cumulative active return from one issuer or
   more than 50% from the best five issuers; and
5. supports at least `$1M` reference capital under the 5%-ADV rule and is not
   confined to one sector or one market regime.

Meeting these gates supports continued research or a separately approved
forward-shadow request. It does not promote or activate Cygnus.

## Kill criteria

Kill or park the thesis if PIT publication lineage cannot be proven; the
challenge-period primary alpha is non-positive; the effect fails the corrected
test; 2x costs erase it; the result is market/momentum/sector exposure; fewer
than 200 independent issuer-revision clusters survive; contributor or year
concentration breaches the pass limits; capacity is below `$1M`; or a small
window/threshold perturbation reverses the sign. Reading or tuning on the
preserved holdout before lock is a governance failure and voids this experiment.

## Cheapest honest next test

Obtain a narrow licensed sample covering at least 100 securities across two
earnings seasons. Reconstruct every analyst forecast from original timestamps,
compare reconstructed daily consensus with vendor snapshots, and require 99%+
timestamp/entity/corporate-action reconciliation. Then test only whether the
frozen score has positive 20-day residual rank IC and monotonic buckets. Failure
of PIT reconstruction or a non-positive directional result stops the full
history purchase/build.

## Freeze record

- Frozen by: Brett Olson, CIO, via explicit `FREEZE HYPOTHESIS, then RUN EXPERIMENT`; drafted by Codex.
- Frozen at: 2026-07-14, America/New_York.
- Spec hash: `sha256:c3b59171815ede2baaa2023e8ab339fe60a0fd40047f2061b1a234dd155a6423` (all bytes before `## Freeze record`).
- Code hash: no experiment code existed at freeze; repository baseline `4d15ade69799a0eff161d5e9819e4d9d574de66d`.
- Data snapshot/hash: `NOT_ACQUIRED`; acquisition and PIT audit are part of the frozen experiment.
