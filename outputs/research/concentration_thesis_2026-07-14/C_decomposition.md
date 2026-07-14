# Workstream C — Decomposing WHY Concentration Behaves As Observed

Date: 2026-07-14 · RESEARCH_ONLY / NON_EXECUTIONAL · Author: Workstream C
Inputs: A's clean PIT sweep (`A_bias_audit_and_clean_sweep.md`, `METHODOLOGY.md`), A's 25 daily series,
`ranking_tables_top20_clean.parquet`, `signals_largecap_pit.parquet`, `regime_spy_trend.csv`, B's live shadow.
Primary series for all attribution: **A's EW, clean window 2014-01-02→2024-12-31, no-settle, 5 bps** (reproduced to
the decimal: top1 0.748 / top3 0.808 / top5 0.957 / top10 0.904 / full 0.786 Sharpe — matches A exactly).

## TL;DR verdict (load-bearing results stated plainly)

1. **Selection precision is a "top-decile-vs-rest" signal, NOT a fine-rank signal.** Momentum's cross-sectional IC over the
   full universe is real but tiny (mean IC ≈ 0.019–0.028, t≈4.8 at 1d). **Inside the top 20, finer ordering carries NO
   positive signal — it is mildly PERVERSE: IC-within-top-20 = −0.012/−0.018/−0.026 (t = −2.4/−3.8/−5.5).** Rank 6-10
   forward returns are *lower* than rank 11-20. So **ranks 1-10 are statistically exchangeable** (rank 1 excepted — see below).
   **Therefore top-5's edge over top-10 is NOT selection skill.** It is the same top-decile signal packed into fewer names.
2. **The concentration "edge" is idiosyncratic-variance harvesting and rests on ~5 lottery names.** 78% of top-5 variance is
   idiosyncratic (96% at top-1). Excluding the single best lifetime name (AXSM) cuts top-5's Sharpe edge over top-10 from
   +0.046 to **+0.011**; excluding the 5 best names **inverts it to −0.022**.
3. **The N≈5 ridge is NOT statistically distinguishable from a plateau.** Block-bootstrap: top-5 beats top-10 in only
   **59.3%** of resamples (Sharpe-diff 95% CI [−0.26, +0.33], straddles 0). top-5 is the argmax-N in only 33% of resamples.
   The ridge also **moves by subperiod**: full-book is best 2014-2018; top-5 best 2019-2024. The whole premium is a
   post-2019 mega-cap-momentum-era phenomenon. **top-5 DOES beat top-3 robustly (86.7%)** — over-concentration below ~5 is
   the one thing the data does reject.
4. **Costs do NOT explain top-5 > top-10** (top-5 turnover is actually *higher*): gross edge +0.049 Sharpe ≈ net edge +0.053.
5. **Regime**: under the pre-committed 200dma definition the top-5-over-top-10 edge is small in BOTH states (+0.03 / +0.07);
   under a VIX alternate it flips to living entirely in stress (+0.26). The two disagree → the regime location is **not
   robust**, which is itself evidence the edge is noise, not a stable regime effect.

---

## (a) Single-name idiosyncratic variance

### Variance decomposition (single-factor OLS on SPY, daily net, clean window) — `C_variance_decomp.csv`
| Level | β(SPY) | R²(market) | **Idio fraction** | Total vol (ann) | Idio vol (ann) |
|---|---:|---:|---:|---:|---:|
| top1 | 1.21 | 0.043 | **0.957** | 100.3% | 98.2% |
| top3 | 1.44 | 0.154 | **0.846** | 62.6% | 57.5% |
| top5 | 1.39 | 0.217 | **0.783** | 51.1% | 45.2% |
| top10 | 1.35 | 0.305 | **0.695** | 41.9% | 34.9% |
| full | 1.07 | 0.864 | 0.136 | 19.7% | 7.3% |

**Concentration is almost entirely a bet on idiosyncratic (single-name) variance.** At top-5, 78% of portfolio variance is
name-specific; at top-1, 96%. Total volatility explodes as you concentrate (top-1 runs ~100%/yr vol). Market/factor exposure
is a minority of risk everywhere except the full book. This is the mechanical source of both the higher realized returns and
the catastrophic drawdowns — you are buying a bigger dose of un-diversified name risk.

### How the −92% / −69% drawdowns arise (which names/dates; one blowup or many?)
Reconstructed EW gross books reproduce A (top5 gross Sharpe 1.000, top10 0.954). MDD windows:

- **top-1: MDD −91% (2021-06-08 → 2023-10-25).** Driven by **meme/parabolic-reversal blowups**: GME (−0.68 contribution,
  held 117d), AAOI (−0.40), AMC (−0.37), GOTU (−0.31), RGC, MDGL. 26 distinct names held, but the **worst 3 names = 53% of
  all losses** — i.e. a handful of single-name catastrophes, GME alone the largest. Rank-1 momentum repeatedly latched onto
  vertical names at their tops and rode them down.
- **top-5: MDD −68% (2015-06-19 → 2016-02-09).** A **diffuse sector unwind** (2015-16 biotech/momentum crash): SRPT (−0.18),
  BLUE (−0.15), CEAYY, STPFQ, GLOB, ITCI, SKX, EXEL. 27 names, worst-3 = only 39% of losses — more spread across a correlated
  momentum cohort than any single blowup.

So the two catastrophes have different anatomy: **top-1 = idiosyncratic single-name blowups; top-5 = a correlated
momentum-factor unwind.** Both are the idiosyncratic-variance channel cashing out on the downside.

### Fragility test — does the extra return survive excluding the top contributors? `C_dd.py`
Lifetime best contributors to top-5: **AXSM, NVAX, ENPH, CVNA, AAOI, MDGL** (the exact "active large-caps the static-200
omits" A flagged). Re-running top-5 EW gross with these names deleted from the universe:

| Excluded | top-5 Sharpe | top-5 CAGR | **top-5 − top-10 Sharpe edge** |
|---|---:|---:|---:|
| none (baseline) | 1.000 | 46.4% | **+0.046** |
| top-1 name (AXSM) | 0.919 | 40.4% | **+0.011** |
| top-3 names | 0.897 | 39.1% | +0.065 (reshuffle bounce) |
| top-5 names | 0.813 | 33.0% | **−0.022 (inverts)** |

**Removing ONE name (AXSM) erases ~76% of top-5's edge over top-10; removing five names inverts it.** The concentration
premium is not broad-based — it is carried by a tiny number of huge idiosyncratic winners. This is the single strongest
piece of evidence that the effect is variance-harvesting / luck, not durable skill. (The top-3 non-monotonicity is because
deleting names also reshuffles which names fall into top-10; the top-1 and top-5 legs are the clean reads.)

---

## (b) Regime dependence

**PRE-COMMITTED PRIMARY regime definition (stated before looking at splits): `spy_above_200dma`** from A's
`regime_spy_trend.csv` — above = trending/risk-on, below = chop/risk-off. Days = 2767, **85.3% risk-on, 80 regime
transitions (~40 risk-off episodes)** → per-regime stats are NOT driven by 2-3 episodes. `C_regime_primary.csv`.

| Level | risk-on Sharpe | risk-on CAGR | risk-off Sharpe | risk-off CAGR |
|---|---:|---:|---:|---:|
| top1 | 0.825 | 44.4% | 0.230 | −16.4% |
| top3 | 0.952 | 49.9% | 0.020 | −18.5% |
| top5 | 1.078 | 51.1% | 0.383 | 5.0% |
| top10 | 1.049 | 40.1% | 0.316 | 2.4% |
| full | 0.762 | 10.9% | 1.066 | 37.9% |
| SPY | 0.819 | 10.3% | 1.014 | 31.2% |

Two facts:
1. **The concentration premium as a whole lives in risk-on.** Concentrated momentum (top1/3/5) makes almost all its money
   when SPY is above its 200dma; in risk-off, top-1/top-3 collapse (Sharpe 0.02–0.23, negative CAGR) while the **full book
   and SPY do BETTER in risk-off** (Sharpe ~1.0) — a diversified mean-reversion effect. Concentration is a leveraged bet on
   trend continuation.
2. **But top-5's edge SPECIFICALLY over top-10 is small in BOTH regimes: +0.029 (risk-on), +0.067 (risk-off).** It does NOT
   live entirely in one regime under the primary definition. So the *5-vs-10 choice* is not a regime call; the *degree of
   concentration overall* is.

### Sensitivity (NOT primary): VIX binary at 20 (live LOW threshold) — `C_regime_vix_sensitivity.csv`
72% of days VIX<20, 166 transitions. Here momentum earns spectacularly in high-VIX (all levels Sharpe 1.2–1.6, as VIX spikes
cluster near rebounds under the t→t+1 convention), and **top-5's edge over top-10 flips to living entirely in stress:
−0.034 (calm) vs +0.259 (VIX≥20).** This DISAGREES with the 200dma read on *where* the edge sits. **The two definitions
giving different regime-locations is the honest result: the top-5-over-top-10 edge is too small and unstable to be pinned to
any regime — reporting either single split as "the" regime story would be p-hacking.** (Guardrail worked: I committed to
200dma first; VIX is shown only to demonstrate non-robustness.)

---

## (c) Selection precision — THE HEART

Built forward returns (1d/5d/21d) for every PIT-eligible, signal-ready name from the close matrix; ranked among selectable
names each date (rank 1 = highest momentum_score, matching the engine). `C_bucket_spreads.csv`, `C_ic_summary.csv`,
`C_ic_within_top20.csv`, `C_per_rank.csv`, `C_bucket_ttests.csv`.

### Forward-return by rank bucket (mean per-name fwd return, %)
| Bucket | 1d | 5d | 21d |
|---|---:|---:|---:|
| rank 1-5 | 0.203 | 1.006 | 4.073 |
| rank 6-10 | 0.114 | 0.511 | 3.054 |
| rank 11-20 | 0.144 | **0.837** | **3.283** |
| rank 21-50 | 0.123 | 0.580 | 2.285 |
| rank 51+ | 0.058 | 0.292 | 1.231 |

**The signal is "top decile vs the rest": rank 1-5 and 11-20 both clearly beat 21-50 and 51+. But the ordering INSIDE the top
is broken — rank 11-20 BEATS rank 6-10 at every horizon.** That is the fingerprint of exchangeable-within-noise ranks.

### rank[1-5] vs rank[6-10] spread + t-tests (`C_bucket_ttests.csv`)
| Horizon | 1-5 vs 6-10 (bps) | t (naive) | t (non-overlap) | 6-10 vs 11-20 (bps) | t (naive) |
|---|---:|---:|---:|---:|---:|
| 1d | +8.9 | 1.59 | 1.59 | −2.9 | −0.72 |
| 5d | +49.5 | 3.82 | **1.49** | −32.6 | −3.46 |
| 21d | +101.9 | 3.58 | **0.81** | −22.9 | −1.15 |

The 1-5 > 6-10 gap looks significant on overlapping windows but **collapses to insignificance on non-overlapping sampling
(t 1.49 at 5d, 0.81 at 21d)** — the apparent significance was overlap-inflated. Meanwhile 6-10 < 11-20 is *negative*. There
is no clean monotone gradient within the top 20.

### Rank IC (daily Spearman) — `C_ic_summary.csv`
| Horizon | mean IC (full universe) | t (non-overlap) | **mean IC within top-20** | **t** |
|---|---:|---:|---:|---:|
| 1d | +0.0188 | 4.80 | **−0.0115** | **−2.45** |
| 5d | +0.0239 | 2.70 | **−0.0177** | **−3.84** |
| 21d | +0.0278 | 1.93 | **−0.0262** | **−5.45** |

**Full-universe IC is positive but tiny (~0.02) — momentum weakly separates top-decile from bottom. IC restricted to the
top-20 is NEGATIVE and significant** — within the strongest-momentum names, higher rank predicts *lower* forward return (a
short-horizon reversal / crowding effect among parabolic names). **Fine ordering inside the top decile is not merely
uninformative; it is mildly counterproductive.**

### Per-exact-rank forward return (`C_per_rank.csv`) — the one nuance
Rank 1 IS genuinely special (h21 = 566 bps vs rank-5 = 327, rank-10 = 452), but ranks 2–20 are noisy and non-monotone
(rank 10 ≈ rank 2; rank 16 among the best at 5d). **Rank 1's extra return is exactly the idiosyncratic-lottery channel from
(a)** — it comes bundled with the highest variance and the −92% drawdown, so on a Sharpe basis top-1 is the *worst* book
(0.748). Rank 1 aside, ranks 2-10 are exchangeable.

**VERDICT (c):** If ranks 1-10 were exchangeable, top-5's edge over top-10 is luck/variance, not skill — and the evidence
says they are effectively exchangeable (negative within-top-20 IC; 6-10 < 11-20; 1-5 vs 6-10 insignificant non-overlapping).
The predictive content is "own the top decile," which top-10 captures as well as top-5. **Top-5's realized out-performance is
concentration of the same signal into fewer, higher-variance names — luck-of-the-draw on which of ~10 exchangeable names
happen to be the big winners — not superior selection.**

---

## (d) Cost / turnover drag — `C_cost_edge.csv`, A's `settled_cash_drag_table.csv`

| Level | ann turnover | cost drag (bps/yr) | gross Sharpe | net Sharpe | gross CAGR | net CAGR |
|---|---:|---:|---:|---:|---:|---:|
| top1 | 54.6 | 273 | 0.775 | 0.748 | 37.0% | 33.3% |
| top3 | 49.5 | 248 | 0.848 | 0.808 | 40.5% | 37.0% |
| top5 | 50.3 | 251 | 1.006 | 0.957 | 46.9% | 43.2% |
| top10 | 44.2 | 221 | 0.957 | 0.904 | 36.7% | 33.7% |
| full | 0.2 | 1 | 0.787 | 0.786 | 14.5% | 14.5% |

- **top-5 − top-10: GROSS Sharpe edge +0.049, NET +0.053; GROSS CAGR edge +10.1pp, NET +9.5pp.** Costs eat essentially none
  of it — and top-5 actually turns *more* than top-10 (50.3 vs 44.2×/yr), so if anything costs work *against* top-5. **Cost
  drag is NOT the reason top-5 beats top-10.** The edge is a gross-return phenomenon (i.e. the variance/luck channel above).
- Cost drag is material in absolute terms (~250 bps/yr for concentrated books vs ~1 bps for the full book) but roughly flat
  across N∈{1,3,5,10}, so it does not shape the *interior* ranking.
- **Settled-cash T+1 drag (A, MODELED):** small and mixed-sign — top3 −316, top5 −110, top10 +247 bps/yr; single-name top1 ~0.
  Noise for a ~5-name daily rotation, not a dominant channel.

---

## (e) Robustness of the N≈5 ridge

### Block bootstrap — 21-day blocks, 5000 draws, **paired** across N (same resampled dates for all levels) — `C_bootstrap_ridge.json`
- P(top-5 Sharpe > top-10) = **0.593**  ·  P(top-5 > top-3) = **0.867**  ·  P(top-5 > top-1) = 0.753  ·  P(top-5 > full) = 0.672
- **Argmax-N distribution: top5 33.2%, top10 24.0%, full 23.0%, top1 15.5%, top3 4.3%.**
- Sharpe[top5 − top10] = +0.036, 95% CI **[−0.261, +0.325]** (straddles 0).
- Sharpe[top5 − top3] = +0.142, 95% CI [−0.111, +0.399].

**The N≈5 interior optimum is NOT statistically distinguishable from top-10 or the full book — N∈{5,10,full} is a plateau
within noise.** The one robust ordering is **top-5 > top-3 (87%)**: going *below* ~5 names is a real, repeatable mistake
(the market-neutral-to-negative Sharpe of top-3/top-1 is not luck). So the data supports "don't over-concentrate below ~5,"
but does NOT support "5 is uniquely optimal."

### Subperiod stability — `C_subperiod.csv`
| Period | top1 | top3 | top5 | top10 | full | **argmax** |
|---|---:|---:|---:|---:|---:|---|
| 2014-2018 | 0.107 | 0.602 | 0.444 | 0.515 | **0.726** | **full** |
| 2019-2024 | 1.144 | 0.956 | **1.310** | 1.167 | 0.848 | **top5** |

**The ridge moves.** In 2014-2018 concentration did not help at all — the full book had the best Sharpe and top-5 (0.444)
underperformed top-10 and full. The entire "top-5 is best" result comes from **2019-2024** (the mega-cap / COVID-recovery
momentum era). This is a strong out-of-regime fragility warning: the N≈5 optimum is period-specific, consistent with the
fragility (a) and bootstrap findings.

---

## SYNTHESIS — Attribution of the concentration effect

Decomposing the observed top-5-over-top-10 realized edge (net Sharpe +0.053, CAGR +9.5pp) and the concentration gradient generally:

| Channel | Contribution | Strongest single evidence | Solid vs suggestive |
|---|---|---|---|
| **(a) Idiosyncratic variance / luck** | **DOMINANT.** The edge is a few big single-name winners amplified by un-diversified variance. | Deleting 1 name (AXSM) cuts the top-5−top-10 edge +0.046→+0.011; deleting 5 inverts it. 78% of top-5 variance is idiosyncratic. | **SOLID** |
| **(c) Genuine selection precision** | **NEARLY ZERO within the top decile.** Momentum works only as "top-decile vs rest" (IC~0.02); intra-top-20 IC is −0.01 to −0.03 (significant, perverse). Ranks 2-10 exchangeable; only rank 1 carries extra (lottery) return. | IC-within-top-20 = −0.012/−0.018/−0.026 (t −2.4/−3.8/−5.5); rank 11-20 fwd return > rank 6-10. | **SOLID** |
| **(b) Regime** | **MINOR / NON-ROBUST for the 5-vs-10 choice.** Concentration overall is a risk-on/trend bet (top-1/3 collapse in risk-off), but the specific top-5>top-10 edge is small in both 200dma states and its VIX location is unstable. | 200dma edge +0.03/+0.07 both states; VIX edge −0.03/+0.26 — definitions disagree. | **SUGGESTIVE** (direction of the broad concentration/trend link is solid; the 5-vs-10 regime attribution is not) |
| **(d) Costs / turnover** | **NOT a driver of the interior ranking.** ~250 bps/yr, roughly flat across N; top-5 turns more than top-10 yet still wins gross. | Gross edge +0.049 ≈ net edge +0.053. | **SOLID** |

**Bottom line:** The concentration effect is **~variance-and-luck (a) with essentially no fine-rank selection skill (c),
not materially a regime or cost story.** Momentum gives you a weak top-decile signal; concentrating it into 5 names instead
of 10 does not add information, it adds idiosyncratic variance whose realized payoff in 2019-2024 happened to be positive and
rode on ~5 names. The N≈5 "ridge" is a plateau (top-5≈top-10≈full within bootstrap noise) that only appeared post-2019; the
only robust rule is **don't go below ~5 names** (top-3/top-1 are reliably worse).

### Mapping to the live question (conviction-waterfill top-N(vix) ∈ [3,7], 0.50 cap)

- **The [3,7] band mostly straddles the plateau, but its lower half is the danger zone.** Bootstrap says top-5≈top-10 (edge
  not distinguishable), and top-5 robustly beats top-3. When VIX pushes N toward 3-4 (the live sleeves run N=3-4 today; live
  VIX-HIGH maps to 4 positions, CRISIS to 2), the config sits on the **wrong side of the one robust result** — in the
  low-Sharpe, catastrophic-drawdown regime (top-3 Sharpe 0.81 vs top-5 0.96; top-1 MDD −92%). A's live-lag read is consistent.
- **No selection-precision justification for tight N.** Since intra-top-decile ranking is uninformative-to-perverse (c),
  there is no signal reason to prefer the top 3-4 conviction names over the top 8-10. Widening the floor of the VIX ladder
  from 3 toward ~7-10 would **cut idiosyncratic tail risk (−69%→−52% MDD from top5→top10) at negligible expected-Sharpe cost**
  (edge indistinguishable from zero in the bootstrap). The 0.50 cap compounds concentration risk further by allowing one
  waterfilled name to dominate — worth stress-testing against a lower cap given (a).
- **Do not treat "5 is optimal" as a law** (A's explicit caution, now quantified): it is a post-2019, single-realization,
  ~5-name-dependent plateau point. B's live shadow (noise-dominated, ±4-6pp over 14 days) cannot distinguish these configs
  yet, which is itself consistent with the plateau.

## Artifact inventory (absolute paths under `outputs/research/concentration_thesis_2026-07-14/`)
- `C_decomposition.md` (this)
- `artifacts/C_variance_decomp.csv` — (a) idio-fraction/beta/R²/vol by N
- `artifacts/C_regime_primary.csv` — (b) 200dma per-regime Sharpe/CAGR/MDD (PRIMARY)
- `artifacts/C_regime_vix_sensitivity.csv` — (b) VIX<20 alternate (SENSITIVITY ONLY)
- `artifacts/C_bucket_spreads.csv`, `C_bucket_ttests.csv` — (c) rank-bucket forward returns + t-tests
- `artifacts/C_ic_summary.csv`, `C_ic_within_top20.csv`, `C_ic_timeseries_h{1,5,21}.csv` — (c) IC full & within-top-20
- `artifacts/C_per_rank.csv` — (c) per-exact-rank forward returns 1..20
- `artifacts/C_cost_edge.csv` — (d) gross vs net Sharpe/CAGR, turnover, cost drag by N
- `artifacts/C_bootstrap_ridge.json` — (e) block-bootstrap ridge probabilities + CIs
- `artifacts/C_subperiod.csv` — (e) 2014-2018 vs 2019-2024 Sharpe by N
- `scripts_c/{c_core.py, c_rank.py, c_dd.py, c_vix.py}` — rerunnable methods. Order: `c_core` (a/b/d/e from A's
  daily series) and `c_rank` (c, writes a ~120MB `C_ranked_forward.parquet` cache — intentionally NOT committed) are
  independent; `c_dd` (a: drawdown + fragility) consumes the parquet, so run `c_rank` first. `c_vix` is the (b) sensitivity.

## Limitations (loud — pre-empting the methodologist)
1. **Inherits A's signal divergence.** This decomposes the *momentum* book that produced the 46%/1.27 claim, NOT the live
   combined-allocator conviction. IC/rank findings are about `momentum_score`; the live composite score could rank differently
   (though A found the waterfill uses momentum_score as proxy, so this is the closest available mirror).
2. **Variance decomposition is single-factor (SPY only).** A full Fama-French/sector model would reassign some "idiosyncratic"
   variance to size/value/sector factors — the true stock-specific fraction is an upper bound. Directionally robust (R² rises
   monotonically with N regardless).
3. **IC t-stats: overlapping horizons.** I report both naive and non-overlapping (every h-th day) t-stats; the non-overlap
   values are the honest ones for h>1 and I lean on them. No Newey-West beyond that.
4. **Fragility test is gross and deletes names for the whole history** (a strong, deliberately adversarial perturbation); it
   shows the edge is thin, not that these specific names were unforecastable ex-ante. Point stands: the edge is not broad.
5. **Bootstrap is moving-block (21d), paired across N.** Preserves ~1-month autocorrelation and cross-N pairing; does not model
   parameter/universe uncertainty. Single price realization (A's limitation #6) still applies underneath.
6. **Regime = binary.** Both 200dma and VIX-20 are two-state; a richer multi-state HMM could localize differently. Reported
   two definitions precisely to avoid over-fitting one.
