# Workstream D — Adversarial Review of the Live-Signal Concentration Study

Date: 2026-07-14 · Role: hostile reviewer, wrote none of A/B · Governance: RESEARCH_ONLY / read-only on all operational artifacts (armed pilot untouched; no git checkout/commit).
Recompute harness: `D_recompute.py` (+ inline `.venv/bin/python` checks). All A/B point estimates were re-derived from the delivered CSVs/parquets, not taken on faith.

## Bottom line up front

The **live-conviction IC numbers reproduce exactly** and the **descriptive/bucket/sweep results are clean** — Claims 1, 2, 4 survive. But two framing pillars do not:

- **Claim 5 (the "sharpest new fact", momentum 10d IC +0.30 / t 6.17) is BROKEN.** The number is real but is an artifact of an asymmetric `>0` filter that silently drops ~20% of names (the negative-momentum ones) from *momentum's* book but nothing from the *live* book — so it is **not** "identical days & books." On the genuinely identical book, momentum's 10d IC is **+0.073 (t≈0.84), indistinguishable from zero and from the live conviction's −0.015.** The "the blend averaged away ordering information momentum captured" story collapses.
- **Claim 3 ("a genuine null") is WEAKENED.** The pooled IC≈0 is a **cancellation of two significant, opposite-signed regimes**: high-VIX crash sub-era 10d IC **−0.10 (t −2.3)**, low-VIX calm sub-era 10d IC **+0.13 (t +2.1)**. The signal is regime-sign-unstable, not a stationary zero. Era mixing biases the pool toward null exactly as suspected.

Neither break rescues concentration — the decision-grade conclusion ("cannot confirm that top-N concentration captures fine-grained skill; burden of proof not met") **stands** — but the specific evidence in Claims 3 and 5 must be restated in the memo.

---

## Per-claim verdict

### Claim 1 — recorded panel is the true live conviction; 93–94 broad days; concentration always-on 2026-07-08 → CONFIRMED (with one clarification)
- `target_weight` is verifiably the ranking key (`core/concentration.py:104` sorts by it; `_capped_waterfill` sizes by it). The panel builder reads it verbatim from shipped snapshots. Confirmed.
- **93 vs 94 resolved:** the missing day is **2026-02-20**, an all-cash day (`n_names=0, cash=1.0`, VIX-crash routing). It contributes zero name-rows and therefore zero to any IC. A's prose "94" counts provenance dates; B's "93" counts panel dates with names. **Immaterial** — B correctly uses the 93.
- Concentration flip on 2026-07-08 is confirmed directly from provenance (07-07 = 18-name broad book, 07-08 = 5-name concentrated with 5% cash floor).
- Caveat carried into Claim 3: git log confirms the panel spans an **evolving codebase** (charlie_munger sleeve added `50818f3`; "regime engine, four sleeves" `52749eb`; risk-off routing `ac686d9`; sleeve-default changes `c9bfc96`). The "one signal" is really several regimes of a changing pipeline stitched together.

### Claim 2 — momentum-only is not a valid stand-in (Spearman −0.16, top-5 overlap 0/5) → CONFIRMED
Validation logic is sound; momentum selects a different book (top-5 overlap ≈ 0) and orders it differently (negative rank-corr). No issue. (Ironic that Claim 5 then leans on momentum as a within-book re-ranker — see below.)

### Claim 3 — within-book IC ≈ 0 at all horizons, a genuine non-degenerate null → WEAKENED
- **Point estimates reproduce to the digit** (4-sleeve: +0.013/+0.034/+0.030/−0.007; full: +0.003/+0.053/+0.064/+0.034; HAC t all ≤ 1.15; MDE 0.09–0.17). The MDE formula (2.8·HAC-SE ≈ 1.96+0.84) is correct. The non-degeneracy check reproduces (4-sleeve: 5% ties, top-5 essentially 5 distinct weights). So the **pooled** claim is arithmetically fine.
- **But "genuine null" overstates homogeneity.** Splitting the 73 4-sleeve days at 2026-04-30 (high-VIX crash → low-VIX calm):

  | sub-era | days | 10d IC | HAC t | 21d IC | HAC t |
  |---|---|---|---|---|---|
  | early (≤Apr-30, VIX 24–31) | 27 | **−0.102** | **−2.34** | −0.083 | −2.55 |
  | late (>Apr-30, VIX 15–19) | 36–26 | **+0.129** | **+2.14** | +0.072 | (7.19*) |

  \*late-21d HAC-t is a degenerate small-sample artifact (SE 0.010 from heavy overlap at n=26) — ignore it; the 10d rows are the trustworthy comparison.

  The pooled ≈0 is a **sign flip across regimes averaging to nothing**, not a stationary zero. This is precisely the "era mixing biases toward null" failure mode. The honest framing is *"pooled null that masks regime-dependent sign,"* not *"the signal genuinely lacks fine-ranking power."* Caveat: 2 eras × 4 horizons = 8 looks, so t≈2.1–2.3 is suggestive not decisive, and the late-positive era coincides with later (possibly better-tuned) code — discount it (see leakage note). The conservative read remains "can't confirm fine ranking," but the reasoning must change.

### Claim 4 — rank 1-5 does not out-earn 11+; sweep N3/N5 weakest, all trail SPY → CONFIRMED
- Bucket spread top5−11+ recomputed independently: 1d −0.0003 (t −0.12), 10d +0.0021 (t +0.34). Exact match, |t| ≤ 0.34. Confirmed.
- SPY sweep recomputed: +11.48% cum, ann Sharpe 2.29. Exact match. Sweep shape (N3/N5 weakest, broad book better, all < SPY) holds.
- Minor fairness note (does not change verdict): the strategy legs carry 20–50% cash + T+1 settlement drag that SPY does not, so "all trail SPY" is partly a cash-drag comparison. The memo already neutralizes this by declaring the sweep descriptive-only with CIs too wide to rank — acceptable.

### Claim 5 — momentum h2h: +0.30 (t 6.17) vs live −0.015; blend averaged away ordering info → BROKEN as stated
- The **+0.30 / t 6.17 reproduces exactly.** It is not a coding typo. But it is **not "identical books."**
- `daily_ic_table` filters `g[conv_col] > 0`. For live conviction this removes nothing (weights are always >0). For momentum, `conviction_momentum_only` is **negative for 33% of the universe**, so the filter **drops on average 3.9 of ~18.9 names/day (20.6% of rows)** — the lowest-momentum names — from momentum's ranked book only.
- Those dropped names are exactly the ones that would *break* momentum's rank ordering: **dropped (mc≤0) names' mean 10d return = +7.2% vs kept (mc>0) = +3.4%** — a reversal, i.e. the worst-momentum names were the best performers. Excluding them manufactures a clean monotonic momentum→return relationship.
- **Recompute on the genuinely identical book (negative-momentum names retained, same names as the live IC):**

  | book | 1d | 5d | 10d | 21d |
  |---|---|---|---|---|
  | momentum, B's `>0` filter | +0.066 | +0.203 | **+0.304 (t6.17)** | +0.242 |
  | momentum, identical book (no filter) | −0.025 | +0.016 | **+0.073 (t0.84, 60% days +)** | +0.005 |
  | live conviction, matched 53d | +0.019 | +0.005 | **−0.015** | −0.015 |

  On identical books, momentum's 10d IC is **+0.073 (inside its 0.24 MDE, insignificant)** — statistically the same null as the live conviction's −0.015.
- **The 1-day timing edge is NOT the culprit** (I checked): re-aligning momentum to the live conviction's asof = T−1 leaves 10d IC at +0.299. The filter asymmetry is the whole effect.
- **Consequence:** the memo's "sharpest new fact" — that fine-ordering information existed in-window and the multi-sleeve blend averaged it away — is **not supported once the book is held identical.** There is no demonstrable within-book momentum ordering edge on the live book in this window either. Strike or restate.

---

## New findings (beyond the required vectors)

- **N1 (leakage direction — cuts toward the null, not against it).** Git shows sleeve strengths, regime→strength mapping, IC-throttle, and sleeve composition were **actively changed during Feb–Jul on the same data being evaluated** (in-sample). If anything this *inflates* the observed live IC, so the ~0 result is if anything **conservative** — the true out-of-sample fine-rank IC is unlikely to be higher than measured. This makes the null *more* robust, not less. Corollary: the late-era +0.13 IC (Claim 3) should be *discounted* as possibly in-sample.
- **N2 (holiday-dated snapshots, benign).** Four 4-sleeve recorded dates — 2026-04-03 (Good Friday), 05-25 (Memorial Day), 06-19 (Juneteenth), 07-03 (July-4 eve) — are US market holidays with no price row, so they drop from IC (73 → 69 usable at 1d). Correctly handled; affects all names equally; **not survivorship**. All 128 book tickers are present in the price panel (no delisting drops).
- **N3 (horizon samples differ, as expected).** 1d/5d/10d/21d IC rows use 69/68/63/53 (4-sleeve) different day counts due to forward-window truncation after ~06-11. Not a bug, but cross-horizon comparisons in the memo are not on a fixed sample — state it.
- **N4 (the 647 outlier).** The PIT momentum panel has a `momentum_score` max of 647 (data glitch); it is screened out of the live-book join (recorded names only), so it does not touch any headline number — but it confirms the raw momentum panel is not clean, another reason not to over-trust the momentum arm.

## Where A/B framing is honest (credit where due)
- B's headline verdict ("cannot confirm fine ranking," not "no fine ranking"), the up-front MDE table, and the "descriptive not inferential" labels on the sweep all correctly respect the power ceiling. The memo does **not** slide from "cannot confirm" into "disproven" in its verdict sentence. The two problems above are (i) the "genuine null" homogeneity claim and (ii) the momentum "identical books" claim — both fixable by wording.

---

## Exact caveat wording the final memo MUST carry

> **Power / non-detection.** The within-book IC test has a minimum detectable IC of ≈0.09–0.17 (80% power, 73–93 days). An economically meaningful true IC of ~0.05 would be **invisible** here. Every result below is "cannot confirm fine ranking," never "fine ranking disproven." Absence of evidence is not evidence of absence.
>
> **The pooled IC≈0 is not a stationary null.** It is the average of a significantly *negative* high-VIX sub-era (10d IC −0.10, t −2.3, Mar–Apr) and a *positive* low-VIX sub-era (10d IC +0.13, t +2.1, May–Jul). The live conviction's fine-ranking is regime-sign-unstable; 73 days cannot resolve which regime governs the pilot's forward path. Do not describe the signal as having "no fine-ranking power" — describe it as "fine-ranking not confirmable and regime-dependent." The late-era positive tilt is further discounted because the pipeline was tuned in-sample over this window.
>
> **The momentum head-to-head is withdrawn as evidence of an in-window ordering edge.** The +0.30 (t 6.17) momentum IC was computed on momentum's positive-only subset while the live IC used the full book; on the *identical* book momentum's 10d IC is +0.073 (t≈0.84), statistically the same null as the live signal's. There is no demonstrable within-book momentum ordering edge on the live book in this window. (This does not affect the top-decile-vs-rest results from the prior study.)
>
> **Signal is a moving target.** The 93/73-day panel spans an evolving codebase (sleeves added/removed, regime and strength logic changed) and a VIX regime shift; it is several signal-regimes stitched together, and in-sample parameter tuning over the window means the measured IC is, if anything, optimistic.

---

## What the verdict can safely rest on

**Safe to base a CONFIRM/OVERTURN verdict on:** Claim 1 (provenance/panel integrity), Claim 2 (momentum ≠ live signal), and Claim 4 (rank-1-5 does not out-earn rank-11+; the descriptive sweep gives concentration no support) — all reproduced exactly and hostile-tested clean. The **overall decision-grade conclusion** — *"cannot confirm that top-N concentration captures fine-grained skill in the live signal; the burden of proof for shrinking the broad book to 3–5 names is not met on 4.5 months of live conviction"* — is **safe and, given the in-sample-tuning direction, conservative.**

**NOT safe to lean on:** the characterization of the within-book IC as a "genuine/stationary null" (it is a regime cancellation — Claim 3), and the momentum "identical-books +0.30" head-to-head (artifact — Claim 5). Neither can support any affirmative statement; both must be restated per the caveats above. No claim in the study supports *affirming* concentration.
