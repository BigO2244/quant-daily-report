# HYP-2026-004 — Options-Information Lead

Governance label: `RESEARCH_ONLY`

Execution impact: `NON_EXECUTIONAL`

Canonical lineage: this is an Alpha Lab research hypothesis, not a change to the
existing Caerus options overlay. The candidate trades only a hypothetical
long-only equity shadow book; it does not recommend, construct, or submit options
or equity orders.

## Claim

Among liquid optionable US equities, a broad long-only basket selected by
concordant bullish delta-adjusted option flow, volume-weighted strike location,
and call-versus-put implied-volatility change will earn positive factor-adjusted,
transaction-cost-net equity returns over the next five trading days relative to
both the eligible universe and a simple put/call-volume signal.

## Initial classification

`ALPHA_CANDIDATE`

## Economic mechanism

- Informed, levered, or mandate-constrained participants may express views in
  options before equity-only investors fully update prices.
- Strike and expiry choice can encode direction and horizon that raw option
  volume discards; agreement across flow and volatility surfaces should filter
  mechanical or speculative noise.
- Persistence may survive because trade/quote alignment, complex-order removal,
  corporate-action lineage, Greeks, and realistic spreads make historical
  reconstruction expensive and fragile.
- Caerus can capture a signal through liquid equities at the next session rather
  than trading the options themselves.
- Volatility risk premium, earnings anticipation, stock momentum, dealer hedging,
  and bid/ask classification error are rival explanations, not alpha.

## Prediction

- Expected sign: positive equity return for the top composite-score decile.
- Forecast horizon: five trading days from next-session entry.
- Expected decay: largest on days 1-5 and economically exhausted by day 20.
- Applicable universe: PIT-active US common equities with price >= `$5`, 20-day
  median equity dollar volume >= `$20M`, daily option volume >= 500 contracts,
  aggregate open interest >= 1,000 contracts, and valid one-minute option NBBO/
  underlying quote alignment.
- Conditions where it should be strongest: multiple independent bullish trades,
  liquid single-leg contracts 15-60 calendar days to expiry, and agreement
  between signed flow and volatility-surface change.
- Conditions where it should fail: complex/stock-option packages, stale or wide
  quotes, market-maker inventory rebalancing, earnings-volatility demand,
  expiration week, corporate actions, or flow already mirrored by stock returns.

## Point-in-time data contract

- Source: exchange-grade historical option trades and quotes with condition
  codes, prevailing NBBO, underlying quote, open interest and Greeks (for example
  Cboe DataShop or demonstrably equivalent OPRA-derived data); approved equity
  OHLCV/corporate actions; earnings-event timestamps; canonical PIT security/
  universe artifacts; yield/dividend inputs used for Greeks.
- Observation timestamp: exchange trade timestamp at millisecond precision,
  contemporaneous option NBBO and underlying quote, and end-of-day open-interest/
  volatility-surface snapshot with its published availability.
- Availability timestamp: only trades/quotes observable by 15:45 ET are used for
  an after-close decision and next-session-open equity entry. Open interest is
  lagged one full trading day unless the source proves earlier availability.
- Security identifier: OCC option symbol decomposed to effective-dated underlying
  security ID, expiration, strike, type, and deliverable; underlying ticker is
  display-only.
- Delisting and corporate-action handling: retain delisted underlyings; use OCC
  adjustment memos/deliverables for splits, mergers, special dividends, and
  symbol changes; contracts with unresolved nonstandard deliverables are excluded.
- Missing-data rule: fail closed on missing/crossed NBBO, zero bid, unresolved
  condition code, absent Greeks/underlying quote, option-security mapping error,
  missing PIT membership, or corporate-action ambiguity. Midpoint-ambiguous
  trades are excluded, not assigned a direction.
- Known coverage limitations: vendor cost/history, OPRA condition-code changes,
  quote latency, inability to observe trader identity or package intent, model-
  dependent Greeks, stale open interest, and survivorship in optionable-universe
  lists.

## Frozen signal and portfolio

- Observation window: regular-session trades from 09:35 through 15:45 ET;
  expiries 15-60 calendar days; absolute delta 0.20-0.80.
- Exclude complex/multi-leg, auction, late, corrected, out-of-sequence,
  contingent, crossed-market, nonstandard-deliverable, and midpoint-ambiguous
  records. A trade is buyer initiated only at or above `mid + 0.10 * spread` and
  seller initiated only at or below `mid - 0.10 * spread`.
- Bullish signed delta dollars: call buys and put sells positive; call sells and
  put buys negative. Normalize net signed delta dollars by total absolute delta
  dollars for the underlying-day.
- Primary score, sector-neutralized and percentile-ranked:
  - `50%` normalized signed delta-dollar imbalance;
  - `30%` signed volume-weighted log strike/spot displacement, with bullish
    buyer-initiated calls and seller-initiated puts carrying their classified
    direction;
  - `20%` one-day change in 25-delta call-minus-put implied volatility for the
    30-day interpolated tenor.
- Require all three components positive and at least 20 classified trades from
  at least three exchanges. Select the top decile, capped at 10 names; equal
  weight; maximum 10% per name; unused weight remains cash.
- Decide after close, enter equity at next regular-session open, and hold five
  trading days. Overlapping daily vintages are combined subject to the 10% cap.
  No options positions, price momentum, optimization, stop, or regime overlay.

## Baselines and risk model

- Primary investable baseline: equal-weight eligible optionable-equity universe
  with the same next-open entry, five-day hold, caps, and equity cost model.
- Simple signal baseline: lowest-decile end-of-day put/call contract-volume ratio
  using the same option filters and portfolio construction.
- Factor controls: daily `MKT-RF`, `SMB`, `HML`, `RMW`, `CMA`, `UMD`, a
  predeclared low-volatility/BAB proxy, sector returns, underlying beta, realized
  volatility, prior 5/20-day return, and earnings-announcement indicators.
- Portfolio-utility comparator, if not an alpha claim: matched-date Polaris,
  Orion, Lyra, and SPY returns; these cannot tune the option signal.

## Frozen experiment

- Primary metric: annualized factor-adjusted intercept of candidate-minus-
  eligible-universe equity returns, net of equity implementation costs.
- Secondary diagnostics: five-day residual rank IC; bucket monotonicity; 1/2/10/
  20-day decay; raw/SPY-relative return; simple-baseline difference; classified-
  trade coverage; spread and quote quality; earnings/non-earnings cohorts;
  factor exposures; stock-volume/return controls; turnover; drawdown; capacity;
  contributor concentration; and overlap/correlation with existing sleeves.
- Walk-forward design: 2014-2018 discovery/calibration only; expanding annual
  walk-forward evaluation for 2019-2024. Trade classification thresholds and
  surface interpolation are frozen before any forward return join.
- Untouched challenge period: 2025-01-01 through 2026-06-30, read once after raw
  reconstruction parity, all code tests, and all variant results are recorded.
- Cost and capacity assumptions: hypothetical equity entry/exit only, next open;
  15 bps per side base and 30 bps per side stress unless quote-based equity spread
  plus impact is higher. Orders must be <= 5% of trailing equity ADV at `$100K`,
  `$1M`, and `$10M`; option capacity is diagnostic because the strategy does not
  trade options.
- Maximum variants in this family: 6 total, including the primary. Allowed
  alternatives are flow-only, strike-only, volatility-change-only, no-earnings-
  within-five-days, 10-day hold, and 20-day hold. Classification thresholds,
  delta/expiry bands, or weights otherwise require a new hypothesis.
- Multiple-testing correction: Romano-Wolf/max-T stationary-block bootstrap at
  one-sided `alpha=0.10` across all six variants, resampling calendar weeks and
  keeping all same-underlying observations within a block.

## Pass criteria

Further work is justified only if the frozen primary variant:

1. has challenge-period annualized factor-adjusted net alpha >= 3% with the
   adjusted one-sided 90% lower confidence bound above zero;
2. has positive five-day residual rank IC in at least four of the six 2019-2024
   validation years and in the challenge period;
3. beats the simple put/call baseline, remains positive at 2x equity costs, and
   remains positive outside earnings windows;
4. retains at least 70% classified-trade/quote coverage after frozen filters and
   is not dependent on one vendor condition-code regime;
5. derives no more than 20% of active return from one issuer, 50% from the top
   five issuers, or 50% from one calendar year; and
6. supports `$1M` reference equity capital under the 5%-ADV rule.

Passing authorizes only continued research or a separately approved equity
forward-shadow request. It does not authorize option or equity execution.

## Kill criteria

Kill or park if exchange-grade PIT reconstruction cannot be licensed or audited;
trade-side classification has <70% usable coverage or fails vendor parity;
corporate-action/OCC lineage is unresolved; challenge alpha is non-positive;
corrected significance fails; 2x costs erase the result; earnings, volatility,
momentum, or contemporaneous stock pressure explains it; contributor/capacity
limits fail; or small quote/classification changes reverse the sign. Any tuning
of filters or weights on the challenge period voids the experiment.

## Cheapest honest next test

Purchase or obtain only two non-adjacent months of full trade/NBBO/underlying-
quote data for 50 liquid underlyings, including one earnings-heavy month. Prove
99%+ contract/OCC/corporate-action mapping, reconcile aggregate volume with
exchange totals, audit 200 classified trades manually, and compute the frozen
five-day residual rank direction without parameter search. Mapping/parity
failure, <70% classified coverage, or non-positive direction stops a full
historical license/build.

## Freeze record

- Frozen by: Brett Olson, CIO, via explicit `FREEZE HYPOTHESIS, then RUN EXPERIMENT`; drafted by Codex.
- Frozen at: 2026-07-14, America/New_York.
- Spec hash: `sha256:b486b0cb6caf81537842582768f8f855231c601319b0b2570832ed79f5817319` (all bytes before `## Freeze record`).
- Code hash: no experiment code existed at freeze; repository baseline `4d15ade69799a0eff161d5e9819e4d9d574de66d`.
- Data snapshot/hash: `NOT_ACQUIRED`; vendor sample acquisition and PIT audit are part of the frozen experiment.
