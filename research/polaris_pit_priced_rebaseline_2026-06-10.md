# Polaris PIT Priced Rebaseline — FR-068 Phase 3

Date: 2026-06-10
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Local-only: yes. VM/execution/model/cron/registry changes: none. Holdout: untouched.

Machine-readable: `outputs/research/pit_rebaseline/polaris_priced_2026-06-10.json`.

## 1. Method (only the universe changed)

Committed Polaris momentum baseline, unchanged: `research.alpha_lab_v1.signals`
`build_alpha_lab_signal_frame` (momentum_score = 0.5·r12_1 + 0.3·r6_1 + 0.2·r3,
close-only) + `research.alpha_lab_v2.engine` `run_backtest` with the
`baseline_top10_daily` spec (daily rebalance, equal-weight top 10, 10 bps).

The **only** difference between legs is the universe source:

- **Legacy** = `data/universe.csv` (static 200 current names).
- **PIT** = `caerus_large_cap` membership (1,600 names incl. 354 delisted),
  per-date eligibility.

Both legs priced from the **same** local Sharadar SEP adjusted close
(`data/research_cache/sharadar_sep/`); SPY from the local matrix for the regime
input. Pricing both legs from one source isolates the universe effect. Window
**2014-01-02 → 2024-12-31**; the 2025+ holdout is excluded by slicing all prices
to ≤ end date. Ranking / sizing / costs / rebalance are frozen.

Coverage: Legacy 197/200 priced (BK, GOOG, MMC absent from SEP); PIT 1,580/1,600
priced (20 absent are 2025+ listings irrelevant to the ≤2024 window).

## 2. Legacy vs PIT metrics

| Metric | Legacy (200 survivors) | PIT (caerus_large_cap) | Δ (Legacy − PIT) |
|---|---:|---:|---:|
| CAGR | 28.83% | 30.68% | −1.85 pts |
| Sharpe | **1.054** | **0.851** | **+0.203 (−19.3%)** |
| Sortino | 1.36 | 1.245 | +0.115 |
| Max drawdown | −43.2% | **−54.4%** | **+11.2 pts shallower** |
| Avg turnover | 0.156 | 0.175 | −0.019 |
| Win rate | 56.5% | 54.1% | +2.4 pts |
| Avg holding period | 18.5 d | 16.5 d | +2.0 d |
| Beta vs SPY | 1.244 | 1.351 | −0.107 |
| Excess vs SPY (cum) | 12.17 | 15.23 | −3.06 |

## 3. Performance deltas — interpretation

The survivor-curated universe **overstated risk-adjusted quality and understated
tail risk**:

- **Sharpe overstated** 1.054 → 0.851 (a **19.3% relative haircut**).
- **Max drawdown understated** −43.2% → −54.4% (**11.2 pts deeper** on the honest
  universe).
- **Raw CAGR slightly understated** (28.83% → 30.68%): the honest large-cap
  universe offers more high-momentum candidates, lifting gross return while
  worsening risk.

Net: the legacy backtest looked *higher-quality and safer* than Polaris actually
is on the honest universe.

## 4. Top-25 attribution (Legacy − PIT contribution difference)

Full table in the JSON. Dominant pattern:

- **NVDA**: Legacy 0.513 → PIT 0.058 (Δ +0.455). Legacy over-concentrated in
  NVDA; in the honest universe NVDA competes with many more high-momentum
  large-caps and is selected less often (`shared_selection_difference`).
- **PIT-only momentum names** the curated 200 excluded — ENPH (+0.44), PLUG
  (+0.30), AXSM (+0.30), GME (+0.29), NVAX (+0.23), CVNA (+0.20), ARWR, CELH,
  NIO, OBE — all `broader_universe_composition`. These speculative high-momentum
  large-caps drove PIT's higher raw CAGR **and** its deeper drawdown / lower
  Sharpe.

**Channel finding:** the top movers are **active large-caps excluded by curation**
(`broader_universe_composition`), **not delisted losers**. For this large-cap
momentum strategy the dominant survivorship effect is *universe curation*
(the 200 quietly omits volatile momentum large-caps), not delisted-loser drag —
delisted names did not appear among the top contributors. Channels are tagged per
ticker in the JSON: `delisted_universe_addition`, `broader_universe_composition`,
`shared_selection_difference`, `ipo_lookahead_or_price_availability`,
`legacy_only`.

## 5. Governance

- **Classification: MATERIAL.** Decision-grade metrics (Sharpe, drawdown) are
  materially distorted by survivor curation (Sharpe −19.3%, drawdown +11.2 pts),
  even though raw CAGR direction is mixed.
- **Are legacy Polaris results decision-grade?** No — materially distorted; the
  risk-adjusted headline and drawdown were biased by the survivor-curated universe.
- **Recommendation:**
  1. Retain legacy as `legacy_current_universe` (non-decision-grade lineage).
  2. Adopt the PIT result as canonical evidence.
  3. Require `universe_method = pit_universe` in all promotion evidence.
  4. Re-evaluate Polaris promotion using the PIT Sharpe (0.85) and drawdown
     (−54%), not the survivor-curated 1.05 / −43%.
- **Confidence: HIGH** — real priced run on the committed harness, both legs
  SEP-priced, window holdout-excluded.

## 6. Caveats

- `scalemarketcap` is *current* scale (PIT-approximate); a name's large-cap
  membership uses today's scale classification, not as-of-date market cap. A
  DAILY-marketcap PIT family would refine this.
- The PIT universe (≈1,200–1,300 per date) is broader than the 200; breadth is a
  legitimate part of the honest correction (you should consider all large-caps,
  not only survivors), but it means the delta blends breadth and survivorship.
- Both legs use SEP `closeadj`; the legacy figure (CAGR 28.83 / Sharpe 1.05) is a
  SEP-priced reproduction and differs modestly from the audit's yfinance-harness
  A-variant (24.99 / 0.969) due to price source and harness, not universe.
- The committed baseline includes SPY as a selectable candidate (identical in
  both legs); it is excluded from the attribution display only.

## 7. Constraints honored

No production Polaris, execution, model/ranking/sizing/risk, transaction-cost,
cron, or `strategy_registry.json` changes. No tuning. No holdout access (≤
2024-12-31). The only difference between legs is the universe source. Artifacts
deterministic; no API key exposed.
