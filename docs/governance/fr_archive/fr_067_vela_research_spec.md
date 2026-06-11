# FR-067 Vela Research Specification (Small-Cap Momentum Sleeve)

Status: Draft / STAGE0_PASSED 2026-06-10 (Sharadar coverage verified: 100/100,
complete_pct 1.0, median 0.999; approved as PIT price/security source). Vela
strategy build still gated on FR-068 PIT foundation + small-cap membership
(no S&P 600 in Sharadar — market-cap band or supplemental source required).
Owner: Caerus Research Program
Last Updated: 2026-06-10
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL

## Purpose

Caerus Vela (`caerus_vela`, proposed) is a small/mid-cap momentum research
strategy. Its objective is to express the existing, well-understood Caerus
momentum stack in the one part of the market where the program has a
structural advantage: capacity-constrained small-caps that institutional
managers are mandated out of and cannot trade at size.

Vela is not a change to Polaris, Orion, Lyra, Phoenix, paper execution, live
execution, broker submission, cron timing, or production portfolio
construction. The first pass is research-only and must not send orders. The
strategy name and any registry entry require explicit owner approval recorded
in `CURRENT_RESEARCH_ROADMAP.md` before code is written.

## Edge Thesis (who is the loser, and why do they persist)

Every Caerus strategy spec must name its counterparty. Vela's:

1. **Mandate-constrained institutions.** Funds above ~$100M cannot build
   meaningful positions in $300M-$2B market-cap names without moving the
   price, so documented momentum/quality premia in small-caps are
   under-arbitraged. The academic and practitioner literature consistently
   finds factor premia larger in small-caps than large-caps, net of realistic
   small-account costs.
2. **Liquidity-demanding sellers.** Wider spreads and thinner books mean
   forced or impatient sellers pay more; a patient small account collects.
3. **Crowding asymmetry.** Polaris competes directly with the most
   commoditized factor in institutional management (large-cap momentum).
   Vela's competition at a sub-$1M account scale is retail flow, not AQR.

At current account scale ($10K paper; realistic forward scale $25-500K), the
capacity constraint that stops institutions does not bind Caerus. This is the
program's only durable structural advantage and it should be tested
deliberately.

## Research Hypothesis

Primary test:

- A point-in-time small-cap universe, scored with the existing Caerus momentum
  composite (12-1, 6-1, 3-1 z-scores with vol adjustment), selected
  top-10/15 with inverse-vol weights and weekly rebalancing, produces a
  positive information ratio versus both SPY and a small-cap benchmark after
  aggressive cost assumptions, with excess-return correlation to Polaris low
  enough to qualify as a distinct return stream.

Pre-registered pass/fail criteria (recorded before any backtest runs; a
variant that fails is reported, not re-tuned until it passes):

| Criterion | Threshold |
|---|---|
| Net IR vs SPY (validation + holdout windows) | >= 0.30 |
| Net IR vs small-cap benchmark (IWM or S&P 600 proxy) | >= 0.20 |
| Excess-return correlation vs Polaris excess | <= 0.50 (target <= 0.30) |
| Max drawdown | <= 1.5x small-cap benchmark max DD over same window |
| Rank IC (composite score vs 20D forward returns) | >= 0.02, t-stat >= 2 |
| Cost sensitivity | thesis survives at 75 bps per side |

## Hard Dependency: Point-in-Time Universe (Stage 0)

This FR is **blocked** until a PIT small-cap universe exists. The
survivorship-bias failure of `data/universe.csv` (current 201 names applied
retroactively to 2014, documented in the 2026-06-10 fund review) must not be
repeated. Explicitly forbidden: hand-curating a list of current small/mid-cap
names and backtesting it historically.

Stage 0 requirements:

- Historical index membership with entry/exit dates. Preferred: S&P SmallCap
  600 constituent history; acceptable: Russell 2000 reconstitution history.
  Candidate sources: commercial (Norgate, Sharadar/Nasdaq Data Link, CRSP via
  academic access) or reconstructed from index provider announcements; the
  source decision is an open question recorded below.
- Delisted names included, with delisting returns handled conservatively
  (delisting proceeds at last trade minus a haircut, or vendor delist return).
- Price source validated for dead tickers: yfinance coverage of delisted
  small-caps is weak. If dead-ticker price history cannot be sourced, the
  backtest start date moves forward to where coverage is provably complete,
  and the limitation is recorded in every artifact.
- Universe snapshot artifact per rebalance date:
  `outputs/research/vela/universe/universe_<date>.csv` with membership-source
  metadata.

Stage 0 deliverables double as the PIT remediation pilot for the rest of the
program (Polaris/Orion/Lyra re-validation uses the same machinery on large-cap
membership).

## Universe Definition (per rebalance date, PIT)

- Index members as of that date (Stage 0 source), US common stock only.
- Price >= $3 at as-of close.
- 20D median dollar volume >= $2M (tradable for a small account; structurally
  unattractive to institutions).
- At least 252 trading days of adjusted OHLCV history.
- Exclude: ADRs, REITs in v0 (rate sensitivity confounds momentum), names
  with unresolved corporate-action anomalies, stale prices, or zero-volume
  days in the trailing 20.
- Benchmarks: IWM (primary), SPY (program-level comparison). Cash proxy: SGOV.

## Signal Framework

Deliberately reuse the proven Polaris stack for comparability — Vela tests a
*venue*, not a new signal:

```text
S_raw  = 0.45*z(r12_1) + 0.30*z(r6_1) + 0.15*z(r3_1) + 0.10*trend_flag
S_adj  = S_raw / max(ATR_20d_pct, 1.5%)
```

- No thematic overlay in v0 (the research digest watchlist is large-cap; mixing
  it in would contaminate the venue test).
- z-scores computed cross-sectionally within the PIT universe per date.
- Reuse `alpha_stack/features/trend.py` and `alpha_stack/research/metrics.py`
  where possible; do not fork feature code.

Variants:

- `vela_v0_weekly_top10`: weekly rebalance, top 10, inverse-vol weights.
- `vela_v1_top15_lowturnover`: top 15, rank-decay exit (enter top 10, exit
  below rank 30), reusing the Orion H2 exit logic.
- `vela_v2_quality_filter`: v1 plus a simple balance-sheet quality screen
  (positive trailing operating cash flow) **only if** a PIT-safe fundamental
  source exists; otherwise deferred.

## Entry, Exit, And Risk Rules

Entry (weekly, first trading day of week, from data available at prior close):

1. Build PIT universe and composite scores.
2. Enter names ranked in the top N with positive 6-1 momentum.
3. Skip entry entirely when the small-cap benchmark is below its 200DMA and
   breadth is washed out (reuse regime artifacts; record as `RISK_OFF_SKIP`).

Exit:

- Rank-decay exit (below rank 3x target count), hard stop at -20% from entry
  close, time stop at 120 trading days, data-quality exit on stale/anomalous
  prices, delisting handled per Stage 0 rules.

Risk controls (research-only):

- Max gross 80% (cash remainder), max single name 8%, max sector 30%,
  max positions 15, min basket 5.
- Volatility cap: exclude names with 20D realized vol > 100% annualized.
- Turnover guard: no churn while a holding satisfies hold rules.

## Cost Model

Small-caps are where cost assumptions go to die; pre-register them:

- Base case 50 bps per side; sensitivity at 25 / 75 / 100 bps.
- Slippage proxy validation: compare assumed costs against realized
  Alpaca paper fills if/when Vela reaches shadow (paper fills understate real
  impact; record this caveat in every artifact).
- Report net-of-cost numbers only in headline tables; gross is diagnostic.

## Walk-Forward Protocol (mandatory)

- Tune window: earliest reliable PIT date through 2019-12-31.
- Validation window: 2020-01-01 through 2023-12-31.
- Holdout: 2024-01-01 forward — run once, after variant selection is frozen.
- Any parameter change after touching the holdout restarts the protocol with
  the violation logged.

## Expected Artifacts

- `outputs/research/vela/<date>/vela_universe_snapshot.csv`
- `outputs/research/vela/<date>/vela_signal_frame.parquet`
- `outputs/research/vela/<date>/vela_rank_table.csv`
- `outputs/research/vela/<date>/vela_holdings.json`
- `outputs/research/vela/<date>/vela_backtest_summary.json`
- `outputs/research/vela/<date>/vela_decision_trace.json`
- `outputs/research/vela/performance/vela_nav_series.csv`
- `outputs/research/vela/performance/vela_summary.json`

All artifacts carry `schema_version`, `trade_date`, `strategy_id: caerus_vela`,
`governance_label: RESEARCH_ONLY`, `execution_impact: NON_EXECUTIONAL`,
universe-source metadata, and explicit PARTIAL/UNAVAILABLE statuses.

## Integration Points

- New module `research/vela/` (universe.py, strategy.py, backtest.py,
  artifacts.py) plus `scripts/research/run_vela_research.py`.
- No changes to cron execution phases, broker code, paper/live execution, or
  the strategy registry until owner approval per
  `CURRENT_RESEARCH_ROADMAP.md` Section 6.
- Shadow integration follows the same dynamic-strategy-slug path specified in
  FR-051 (shared prerequisite: shadow tracking must drop hard-coded
  three-strategy assumptions).

## Promotion-Readiness Plan

Backtest evidence alone is never promotion-eligible. Minimum before any
promotion discussion: 90 clean forward shadow trading days on the honest
universe, unbroken NAV chain (FR-066 canonical series as the comparison
anchor), pre-registered criteria met out-of-sample, correlation and
attribution distinct from Polaris/Orion, and operator review of the worst 10
trades.

## Risks, Assumptions, And Open Questions

Risks:

- PIT membership and delisted-price data are the entire ballgame; a cheap
  source that silently drops dead tickers reproduces the exact bias this FR
  exists to eliminate.
- Small-cap momentum has brutal crash risk (e.g., momentum crashes in
  2009-style reversals); the risk-off skip rule is a blunt mitigation and its
  cost must be measured, not assumed.
- Paper fills will materially understate real small-cap costs; live promotion
  criteria must include a realized-slippage audit.
- Account scale today ($10K) supports ~10 positions at ~$800 each; fractional
  shares mitigate but odd-lot execution adds noise.

Open questions (answer before Stage 1):

1. Universe/membership source: Sharadar is conditional pending trial verification
   by `scripts/research/verify_sharadar_coverage.py`; if coverage is inadequate,
   Norgate, CRSP/WRDS, or other vendors remain under evaluation.
2. IWM vs S&P 600 (VIOO/IJR) as primary benchmark.
3. Should the regime risk-off gate reuse the existing four-dimension regime
   engine (large-cap inputs) or a small-cap-native breadth measure?
4. REIT/biotech exclusion: principled or performance-chasing? Decide and
   freeze before tuning.
