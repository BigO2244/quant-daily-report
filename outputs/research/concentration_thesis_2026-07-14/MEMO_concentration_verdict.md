# RESEARCH MEMO — Does concentration add or destroy alpha in Caerus?

- **Date:** 2026-07-14
- **Status:** Verdict issued AFTER adversarial methodology review (D) — all load-bearing claims re-computed hostile and survived, with the caveats below incorporated.
- **Scope:** research only; no live/config change made or implied as urgent. The pilot's current setting (top-N(vix) ∈ [3,7], MAX_WEIGHT 0.50) is unchanged.
- Workstream reports in this directory: A (bias audit + clean sweep), B (live shadow + power), C (decomposition), D (adversarial review).

## VERDICT

**Concentration in Caerus neither adds nor destroys alpha through skill — it is a variance dial on a coarse signal, and both evidence sources that claimed otherwise were broken in identifiable, now-quantified ways.**

1. **The backtest's "concentration wins" (top-5 46%/1.27 vs top-10 32%/1.11) was inflated by bias.** Reproduced exactly (Alpha Lab v2 `h6_top5_daily`), then corrected: holdout-window inclusion (−0.165 Sharpe), survivorship/universe composition (−0.285 Sharpe, drawdown −41%→−70%). The cleaned edge (top5 0.957 vs top10 0.904) is small, and it is **not stable**: it flips sign under a defensible universe age-gate (+0.13 → −0.12, adversarial finding N1) and essentially vanishes under live-realistic waterfill sizing (0.941 vs 0.935, N3). It is carried by a handful of lucky names — delete the best 5 lifetime names and the edge inverts (C's fragility test).
2. **The live shadow's "concentration loses by 22-24%" was a measurement artifact.** The baseline's cumulative return included a +28-42% rally from BEFORE the alpha books existed (42 obs vs 14 obs). Like-for-like over the common window: Polaris_Alpha −4.0pp, Orion_Alpha **+5.7pp (outperforms)**; p ≈ 0.27/0.28; ~81-87 trading days needed for 80% power vs 14 available. The live evidence is noise-dominated in BOTH directions — it neither condemns nor vindicates concentration. (Gross of the shadows' forced cash, the Polaris basket lagged −8.5pp — so the construction mismatch does not excuse the concentrated basket either; D-N4.)
3. **The mechanism is settled and it is the heart of the answer: there is no selection precision inside the top decile.** Within-top-20 rank IC is *negative* at every horizon (−0.012/−0.018/−0.027 at 1d/5d/21d, t up to −5.5 — and most negative at 21d, defeating the "short-horizon reversal artifact" objection). Ranks 2-10 are exchangeable; rank 6-10 forward returns are *below* rank 11-20. The momentum signal works as top-decile-vs-rest; the fine ordering that concentration relies on carries no information. Therefore concentrating from 10 names to 5 changes variance, not expected skill capture.
4. **What concentration definitely does is buy tail risk:** max drawdown worsens monotonically (full −40% → top10 −52% → top5 −69% → top1 −92%), 78% of top-5 variance is idiosyncratic (96% at top-1), and the blowups are identifiable meme/reversal events. Costs are NOT the driver (flat ~250 bps/yr across N); the regime story is too unstable across definitions to act on.
5. **The one finding robust to everything (86.7% of block-bootstrap resamples, both block lengths, both subperiods): top-5 beats top-3. Going below ~5 names is the single repeatable mistake in the whole space.** Notably, that is where the live VIX ladder currently goes (N→3-4 in stress) and where both live shadow sleeves sit (top-3/top-4).

**Which source to trust:** neither, as stated. Trust the cleaned mechanism evidence (points 3-5), which both sources converge on once their defects are removed.

## RECOMMENDATION for the pilot (no urgency — pilot unchanged tonight)

1. **Raise the concentration floor: top-N(vix) ∈ [3,7] → [5,7].** This is the only change the evidence *robustly* supports (top-5 > top-3 at 87%). It removes the one repeatable mistake without touching anything the data is agnostic about.
2. **Do not tighten further; consider widening toward 7-10 for drawdown relief.** The Sharpe cost of N=7-10 vs 5 is indistinguishable from zero (bootstrap CI [−0.26, +0.33]) while max drawdown improves −69% → −52%. For a $500 cash-account pilot proving out execution, the drawdown relief is worth more than an unmeasurable Sharpe delta.
3. **Stress-test MAX_WEIGHT 0.50 downward (~0.30).** With zero within-decile precision, a 0.50 single-name cap is pure idiosyncratic tail exposure with no skill justification. Not urgent; model it first with the existing sweep artifacts.
4. **Do NOT make it regime-conditional yet.** The two regime definitions disagree on where the (small) edge lives; 80 transitions of history produce an unstable answer. Revisit with more data.
5. **Keep the shadow sleeves running ~4 months** (to n≈81-87) before drawing ANY live conclusion — and fix their construction (equal-weight, 0.20/0.25 cap, forced cash) to mirror the live waterfill/0.50/fully-invested config, or they will never be a fair test of the live setting.
6. **Follow-up research (the biggest open hole):** re-run the sweep on the TRUE live combined-allocator conviction signal. Everything above measures the momentum-only book; mapping it to the live config assumes momentum_score ≈ live conviction, which is unverified (flagged by A, C, and D).

## What the data cannot yet answer (stated plainly)

- Whether any of this transfers to the live combined-allocator signal (see #6) — the single biggest caveat.
- Whether concentration has a real regime dependence (underpowered, definition-unstable).
- True trading costs for the small/illiquid-at-entry names that carry the historical edge (5 bps flat is optimistic there; D-N2).
- Anything at all from 14 days of live shadow (need ~4 months).
- Whether the post-2019 mega-cap-momentum era that produced the apparent top-5 premium persists — the ridge did not exist 2014-2018.

## Provenance & honesty notes

- The 46%/1.27 claim reproduces exactly from committed code; nothing was fabricated upstream.
- The "PIT" universe is PIT-approximate: static `scalemarketcap` + membership-from-IPO leaks size/age information (D-N1) — this is why the clean edge is presented as a range/sign-unstable, not a number.
- All sweep grids were pre-registered in METHODOLOGY.md and reported in full; the primary regime definition was pre-committed; the adversarial review found no p-hacking or convenient errors.
- Known cosmetic defect: `claim_repro.json` mislabels the cost parameter (25 vs effective 10 bps) — non-material (D-N5).
