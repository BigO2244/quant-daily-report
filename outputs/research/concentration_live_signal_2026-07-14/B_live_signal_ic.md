# Workstream B — Within-Book Rank-IC of the LIVE Combined-Allocator Conviction

Date: 2026-07-14 · Governance: RESEARCH_ONLY / NON_EXECUTIONAL · Read-only over all operational artifacts (armed pilot untouched; no env/config/VM writes).
Output dir: `outputs/research/concentration_live_signal_2026-07-14/` (deliverables prefixed `B_`).

## THE ONE QUESTION
Does the live combined-allocator signal rank **finely within its own book** (positive within-top-N IC ⇒ top-5 captures real skill), or is it **coarse** like momentum-only, whose within-top-20 IC was *negative* at every horizon (−0.012/−0.018/−0.026, t −2.4/−3.8/−5.5 in the prior study)?

## HEADLINE VERDICT
**Cannot confirm fine ranking. The live conviction's within-book IC is statistically indistinguishable from zero at every horizon — it is neither the negative/perverse ordering that momentum-only showed, nor a demonstrable positive fine-rank skill.** With 73 four-sleeve days × ~17 names the minimum detectable IC (80% power) is ≈ 0.09–0.17; the observed ICs (+0.01 to +0.03) sit far inside that noise band. So the honest verdict is **"cannot confirm fine ranking,"** not "no fine ranking." Concentration therefore has **not** been shown to earn its keep on the live signal, and the descriptive sweep + zero bucket-spread give no independent support that it does.

---

## DATA (pre-registered before computing)

- **Signal:** `A_recorded_signals_panel.csv`, `concentration_status == pre_concentration_broad`. The delivered CSV contains **93** broad-book dates (2026-02-03 → 2026-07-07), not 94 as the A-handoff prose stated (one-day discrepancy — I use the 93 actually present). Per-name conviction = combined-allocator `target_weight`, the exact key `core/concentration.py:104` ranks by.
- **PRIMARY subset (declared before results): the 73 full 4-sleeve days** (2026-03-24 → 2026-07-07, `n_sleeves_day >= 4`) — the truest picture of the live combined conviction. The full 93-day panel is reported alongside but mixes in legacy equal-weight eras (see ties).
- **Prices / forward returns:** yfinance auto-adjusted close (total-return proxy), one consistent series per name, 2026-01-15 → 2026-07-13 (real data ends 2026-07-13, the last close before "today" 2026-07-14). **Cross-validated vs the SEP `closeadj` cache on the overlap: daily-return correlation median 1.000, mean 0.9954, 99% of names > 0.99.** BK (21 appearances, all ≤ 2026-06-03) backfilled from the flow_detection yfinance panel (return-basis ~0.94-consistent, minor name); MMC (1 appearance) unavailable everywhere → dropped on that one day. These drop at most one name from the affected day's book.
- **Return alignment:** conviction is decided at `asof_date` (T-1 close) and tradeable on `date`. Forward horizon-h return = `close[date+h]/close[date] − 1` on the common trading-day calendar (positional shift). This is the standard, mildly-conservative convention (it forgoes the T-1→T close move a same-close entry would capture).
- **Horizons:** 1/5/10/21 trading days. Because forward windows overlap across consecutive days, aggregation uses **Newey-West HAC** standard errors (Bartlett, lag = h) as the primary t-stat, plus a **non-overlapping subsample** t (every h-th day) and a **day-level sign test** (binomial on the sign of the daily IC vs 0.5). The 21d subsample has only ≈3 independent blocks → its subsample-t is reported but **uninformative and ignored**.

### Power / minimum detectable IC (stated up front)
Primary 4-sleeve panel: HAC SE of the mean daily IC runs 0.034–0.061 → **MDE at 80% power ≈ 0.094 / 0.129 / 0.170 / 0.109** at 1/5/10/21d. Any true IC smaller than that is undetectable here. 73–93 days is adequate for *descriptive* within-book IC and top-N-vs-rest, **underpowered** for a significance verdict on a small IC. This matches A's power note (~81–87 obs for 80% power).

---

## TASK 1 — WITHIN-BOOK RANK-IC (the decidable question)

### Live conviction (`target_weight`), Spearman(conviction, forward return) computed within each day's book

| Panel | Horizon | n days | mean IC | HAC t | subsample t | frac days +ve | sign-test p | MDE(80%) |
|---|---|---|---|---|---|---|---|---|
| **4-sleeve 73d (PRIMARY)** | 1d | 69 | **+0.013** | 0.37 | 0.34 | 0.507 | 1.00 | 0.094 |
| | 5d | 68 | **+0.034** | 0.74 | 0.53 | 0.529 | 0.72 | 0.129 |
| | 10d | 63 | **+0.030** | 0.49 | 0.49 | 0.476 | 0.80 | 0.170 |
| | 21d | 53 | **−0.007** | −0.19 | (n=3, ignore) | 0.509 | 1.00 | 0.109 |
| Full 93d | 1d | 79 | +0.003 | 0.10 | 0.09 | 0.519 | 0.82 | 0.088 |
| | 5d | 78 | +0.053 | 1.15 | 0.59 | 0.551 | 0.43 | 0.129 |
| | 10d | 73 | +0.064 | 1.08 | 0.21 | 0.493 | 1.00 | 0.165 |
| | 21d | 63 | +0.034 | 0.74 | (n=3, ignore) | 0.571 | 0.31 | 0.131 |

**Read:** every mean IC is inside its own noise band. The point estimates lean **weakly positive** at 5–10d (especially on the full panel, +0.05/+0.06) but no HAC t reaches 1.2 and no sign test is significant. **This is categorically different from momentum-only's within-top-20 IC, which was reliably NEGATIVE (t −2.4/−3.8/−5.5).** The live signal is not perverse — it just isn't demonstrably fine. Verdict: **cannot confirm fine ranking; can rule out the strong negative ordering momentum showed.**

### Bucket spreads — forward return of conviction rank 1-5 vs 6-10 vs 11+ (per day, averaged)
`B_bucket_spreads.csv`. The decision-relevant number is **top-5 minus 11+** (does the top of the book out-earn the tail?):

| Panel | Horizon | rank1-5 | rank6-10 | rank11+ | top5 − 11+ | paired t |
|---|---|---|---|---|---|---|
| 4-sleeve 73d | 1d | +0.14% | +0.28% | +0.17% | −0.03% | −0.12 |
| | 5d | +1.19% | +1.29% | +1.17% | +0.02% | 0.04 |
| | 10d | +3.33% | +2.57% | +3.13% | +0.21% | 0.34 |
| | 21d | +7.67% | +5.00% | +7.63% | +0.03% | 0.03 |
| Full 93d | 10d | +2.55% | +1.80% | +3.11% | +0.05% | 0.08 |

**The top of the conviction book does not out-earn the tail at any horizon (|t| ≤ 0.34).** If anything the middle bucket (6-10) is the weakest and rank 11+ is as strong as rank 1-5. This independently corroborates the IC≈0 finding: **concentrating into the highest-conviction names captures no measurable extra forward return** in this window.

### Momentum head-to-head on IDENTICAL days & books (the cleanest comparison)
Momentum conviction = `conviction_momentum_only` from A's reconstruction parquet (valid *as the momentum signal*), merged onto the exact recorded (date, ticker) rows and scored the same way. Parquet ends 2026-06-09 → 53 four-sleeve overlap days, 70 full-panel. Same forward returns, same books:

| Signal on same books | Horizon | mean IC | HAC t | sign-test p |
|---|---|---|---|---|
| **Live conviction** (4-sleeve, matched 53d) | 5d | +0.005 | 0.12 | 0.27 |
| | 10d | **−0.015** | −0.26 | 0.41 |
| | 21d | −0.015 | −0.38 | 1.00 |
| **Momentum** (4-sleeve, same 53d) | 5d | **+0.203** | 2.43 | 0.0003 |
| | 10d | **+0.304** | **6.17** | 1e-8 |
| | 21d | **+0.242** | 3.68 | 3e-5 |

**On identical data, plain momentum ordered the live book strongly and significantly (10d IC +0.30, t 6.17, 89% of days positive) while the live combined conviction did not (10d IC −0.015, t −0.26).** In this window the ordering information that *would* have fine-ranked the book is exactly what the multi-sleeve blend (value + quality + mean-reversion = ~48% of book weight, mean-reversion being anti-momentum by construction) dilutes or inverts. See the synthesis for the crucial generalization caveat — this is a **momentum-favorable 53-day window**, and the prior decade shows momentum's within-book edge does **not** persist.

---

## TASK 2 — COST-AWARE SWEEP ON THE LIVE SIGNAL (descriptive context, NOT inferential)

**CRITICAL FRAMING (stated in the artifact per the mission): 4.5 months / 93 rebalances cannot rank N levels by Sharpe. The IC above is the inferential result; this sweep is descriptive context only.** `B_sweep_table.csv`. N ∈ {1,3,5,7,10,full} over the 93 recorded days; ranking by recorded `target_weight`; **live waterfill** (0.50 cap, 5% cash, `core.concentration._capped_waterfill` imported read-only) and an **equal-weight** variant; **5 bps** half-spread cost on turnover; **T+1 settled-cash staging** (reusing the prior study's `stage_settled`). Return convention: 1-trading-day-forward return per recorded rebalance, compounded (descriptive series, not a continuous NAV — recorded dates are non-contiguous); Sharpe annualized ×√252 with **iid bootstrap 95% CIs** (5000 resamples).

| sizing | level | avg N | cum ret | ann Sharpe | Sharpe 95% CI | max DD | ann turnover | hit rate |
|---|---|---|---|---|---|---|---|---|
| waterfill | N1 | 1.0 | +6.6% | 1.31 | **[−1.90, 4.44]** | −5.5% | 110 | 0.495 |
| waterfill | N3 | 3.0 | +3.7% | 0.57 | [−2.71, 3.76] | −5.9% | 134 | 0.441 |
| waterfill | N5 | 5.0 | +6.7% | 0.97 | [−2.18, 4.31] | −6.7% | 127 | 0.484 |
| waterfill | N7 | 7.0 | +9.6% | 1.40 | [−1.78, 4.62] | −7.1% | 121 | 0.538 |
| waterfill | N10 | 9.8 | +10.4% | 1.57 | [−1.60, 4.74] | −4.2% | 114 | 0.559 |
| waterfill | full | 17.2 | +10.0% | 1.48 | [−1.70, 4.72] | −6.4% | 104 | 0.538 |
| ew | full | 16.7 | +6.0% | 0.86 | [−2.29, 4.16] | −10.3% | 99 | 0.527 |
| bench | SPY | 1 | +11.5% | 2.29 | [−1.12, 5.78] | −6.6% | 0 | 0.505 |

**Every Sharpe CI spans from strongly negative to strongly positive** — the sweep cannot distinguish any level from any other, or from SPY. Taking the point estimates purely descriptively: **concentration does not help.** N3/N5 (Sharpe 0.57/0.97) are the *weakest* levels; the broad book N10/full (1.48–1.57) edges them out; and **all trail SPY (2.29)** over this window. This directionally agrees with the IC≈0 and flat bucket spreads: shrinking the book added drawdown and turnover without adding return. But the CIs mean this is *context, not proof*.

---

## TASK 3 — TIE / DEGENERACY (the structural check A flagged)

A noted the live conviction weights are post-allocator (already normalized/combined). If the top of the book is weight-flat, rank-IC on `target_weight` understates the underlying score's discrimination *and* concentration is selecting arbitrarily among ties. Quantified (`B_daily_series/tie_stats_*.csv`):

| sleeve-era | n dates | mean frac tied | days fully flat | mean CV(weight) | mean #distinct in top-5 |
|---|---|---|---|---|---|
| 1-sleeve (legacy) | 13 | 0.77 | **0.62** | 0.06 | 1.4 |
| 2-sleeve | 3 | 0.83 | **0.67** | 0.12 | 1.0 |
| 3-sleeve | 4 | 0.62 | 0.00 | 0.25 | 3.0 |
| **4-sleeve (PRIMARY)** | 73 | **0.05** | **0.00** | **0.39** | **4.88** |

**Two distinct findings:**
1. **In the primary 4-sleeve regime the conviction is NOT degenerate** — only 5% of names tied, zero fully-flat days, weight CV 0.39, and the top-5 are essentially always 5 distinct weights (4.88/5). So **the IC≈0 result is real signal-quality, not a tie artifact.** The 4-sleeve conviction genuinely discriminates its names by weight; that ordering just doesn't predict forward returns.
2. **The legacy eras (Feb–Mar, 16 of 93 broad days) ARE degenerate** — 62–67% of those days are *fully flat* equal-weight books (7 or 10 names), so on ~11% of the full-panel days "concentration" would be selecting among exact ties broken alphabetically. This is itself a finding for anyone reading the full-panel numbers: part of the early history cannot support any ranking claim at all.

Net: rank-IC on `target_weight` does **not** materially understate discrimination in the regime that matters (4-sleeve) — the weights are well-spread there — so the "cannot confirm fine ranking" verdict stands on non-degenerate data.

---

## SYNTHESIS — does concentration earn its keep on the live signal?

**Where the live signal AGREES with the momentum-only study:** the *conclusion* is the same even though the signal differs. Momentum's within-top-20 IC was negative and its top-5 edge was idiosyncratic-variance harvesting, not selection skill (ranks 1-10 exchangeable). The **live** conviction's within-book IC is ≈ 0 and its top-5-vs-tail bucket spread is ≈ 0 — i.e. **the live signal also fails to rank finely within its book.** Both studies land on: *the top of the book is not where the return is; concentration = taking more un-diversified name variance for no measured ordering payoff.* The live descriptive sweep even reproduces the shape (concentrated N3/N5 no better — here worse — than the broad book).

**Where they DIVERGE:** the live signal is **not perverse**. Momentum's fine ordering was mildly negative (statistically harmful); the live conviction's is a genuine null (weakly positive point estimates, not significant). So the live signal does not *rescue* concentration, but it also isn't actively mis-ranking the way momentum-only was.

**The sharpest new fact:** on identical days/books, momentum would have fine-ranked the live book strongly (10d IC +0.30, t 6.17) where the live conviction did not. **Heavy caveat, load-bearing:** this is a 53-day, Feb–Jun-2026, momentum-favorable window, and the prior *decade* shows momentum's within-book (top-20) IC is negative and non-durable. So this is **not** evidence that "momentum is the better live signal." It is evidence that (a) fine-grained within-book return-ordering information *existed* in this window, and (b) the live blend did not capture it — its multi-sleeve construction (≈48% value+quality, plus an anti-momentum reversion sleeve) averaged that information away. Whether that is good (durable diversification) or bad (throwing away in-window alpha) cannot be settled on 53 days.

### Provisional answer (hedged exactly as far as the data allows)
**Concentration has NOT been shown to earn its keep on the live signal, and the evidence available leans against it — but the within-book IC is a null, not a proven negative.** Three independent lines agree: (1) within-book IC ≈ 0 at every horizon, well below the ~0.09–0.17 detectable floor; (2) rank-1-5 does not out-earn rank-11+ (spread t ≤ 0.34); (3) the descriptive sweep shows concentrated books no better — mildly worse — than the broad book, all trailing SPY, with Sharpe CIs too wide to rank. Because the conviction is **non-degenerate** in the 4-sleeve regime, this null reflects the signal's genuine (lack of) fine-ranking power, not a measurement artifact. **The correct decision-grade statement is "cannot confirm that top-N concentration captures fine-grained skill in the live signal; the burden of proof for turning the broad book into a 3–5-name book is not met on 4.5 months of live conviction."** A positive fine-rank verdict would require either more history or a signal whose within-book IC clears the detectable floor — neither is in hand.

## Measured / Modeled / Assumed
- **MEASURED:** all recorded `target_weight` (verbatim from A's panel); all forward returns (real yfinance adjusted closes, SEP-validated at ~1.0 return-corr); all IC / bucket / tie statistics; the sweep P&L on real prices.
- **MODELED:** 5 bps half-spread slippage; T+1 settled-cash staging (prior study's mechanics); iid Sharpe bootstrap; momentum conviction = `momentum_score` (A's PIT reconstruction).
- **ASSUMED:** yfinance auto-adjusted close = true total return; BK's flow_detection prices interchangeable with the yf basis (return-corr ~0.94, 21 minor rows); market impact ≈ 0; forward windows truncate naturally at 2026-07-13 for late dates (21d horizon has full data only through ~2026-06-11 signal dates).

## Reproduce
`B_fetch_prices.py` (price panel + SEP cross-check) → `B_price_panel.parquet`; `B_analysis.py` (IC + momentum h2h) → `B_ic_tables.csv`, `B_daily_series/*_ic_*.csv`; `B_ties_buckets.py` → `B_bucket_spreads.csv`, `B_daily_series/tie_stats_*.csv`; `B_sweep.py` → `B_sweep_table.csv`, `B_daily_series/sweep_*.csv`.
