# RESEARCH MEMO — Concentration on the TRUE live signal: verdict

- **Date:** 2026-07-14
- **Question:** does the combined-allocator conviction the live pilot actually trades rank finely within its book — rescuing concentration where momentum-only could not?
- **Status:** verdict issued AFTER adversarial review (D), which re-computed every load-bearing number, BROKE one headline sub-claim and WEAKENED another; both are restated below per D's required wording. The decision-grade conclusion survived.
- **Relation to prior study** (outputs/research/concentration_thesis_2026-07-14/): this closes its #1 open hole.

## VERDICT: the momentum-only finding is CONFIRMED, not overturned. Concentration does not earn its keep on the live signal either.

**Decision-grade statement (adversarially validated as safe and conservative): we cannot confirm that the live conviction ranks finely within its book, and the burden of proof for shrinking the broad book to a 3-5 name book is not met.** If anything the measured null is optimistic: the allocator was tuned in-sample on this very window (git-verified), which should inflate, not depress, its IC.

### What was established

1. **The true live signal was captured, not proxied.** 93 recorded pre-concentration broad-book days (2026-02-03 → 2026-07-07; concentration went always-on 2026-07-08) of the exact combined `target_weight` that `core/concentration.py` ranks. Momentum-only is formally NOT a stand-in (Spearman −0.16 vs recorded; top-5 overlap 0/5) — the prior study's biggest caveat is now a measured fact.
2. **Within-book rank IC ≈ 0 at every horizon** (primary 73-day 4-sleeve subset: +0.013/+0.034/+0.030/−0.007 at 1/5/10/21d, all |t| ≤ 0.74). Rank 1-5 does not out-earn rank 11+ (|t| ≤ 0.34). The book's weights are non-degenerate (5% ties), so this is signal quality, not tie artifacts.
3. **The descriptive sweep agrees:** concentrated books (waterfill N3/N5) were the weakest, the broad book better, all trailing SPY over the window — descriptive only (94 days; Sharpe CIs span [−2, +4.7]).
4. **RESTATED per adversarial review (original claim BROKEN):** momentum did NOT out-rank the live blend on identical books. The reported +0.30 (t 6.2) head-to-head was an artifact of a >0 filter that dropped ~20% of names — the negative-momentum, higher-forward-return ones — from momentum's side only. On identical books momentum's 10d IC is +0.073 (t≈0.84): the same null. **There is no evidence any signal in the stack ranks finely within the top of the funnel; the "blend averaged away ordering information" story is withdrawn.**
5. **RESTATED per adversarial review (claim WEAKENED):** the pooled ≈0 IC is not a stationary null. It is the cancellation of a significantly negative high-VIX sub-era (10d IC −0.10, t −2.3) and a positive low-VIX sub-era (+0.13, t +2.1) — both small samples, both entangled with in-sample code evolution during the window. Do not read either sub-era as actionable; do read the instability as further reason not to trust fine ranking.

### The one operationally pointed observation (small-sample, flagged as such)

The live VIX ladder tightens N toward 3 exactly in stress — and the (small, non-decision-grade) high-VIX sub-era is where the within-book ranking measured *inverted* (−0.10, t −2.3). The current config concentrates hardest precisely where the evidence, such as it is, is least favorable to ranking. This does not prove harm; it does align with, and mildly strengthen, the prior memo's floor recommendation.

## IMPLICATION FOR THE PILOT (unchanged from the prior memo — now on firmer ground)

The prior recommendations stand, and #1 is reinforced:
1. **Raise the VIX-ladder floor: top-N ∈ [3,7] → [5,7].** (Prior basis: top-5 > top-3 in 87% of decade-long bootstraps. Now added: the live signal shows no fine ranking to justify N=3, and its stress-era ranking measured inverted.)
2. Do not tighten below 5; widening toward 7-10 costs nothing measurable and buys drawdown relief (prior study).
3. Stress-test MAX_WEIGHT 0.50 → ~0.30 — with no fine-ranking evidence on ANY signal, a 0.50 single-name bet has no skill justification.
4. Still no regime-conditional N: the regime sub-era signs are provocative but small-sample, tuning-contaminated, and inconsistent with the prior study's regime instability.
5. Keep recording pre-concentration signals (see below) — the single cheapest way to make this question answerable properly.

## What the data cannot yet answer (stated plainly)

- A weak-but-real fine-ranking IC (e.g. 0.05, economically meaningful) is invisible at this sample: minimum detectable IC ≈ 0.09-0.17. The verdict is "cannot confirm," not "disproven."
- Whether the high-VIX ranking inversion is real (n small, in-sample-tuned window).
- Whether any of this holds out-of-era: the signal definition itself evolved during Feb-Jul (era mixing verified by git); the 4-sleeve subset is the cleanest but still spans code changes.
- Long-horizon behavior of the live blend — only ~4.5 months of it exists anywhere.

## Data-collection recommendation (makes the next pass decisive)

The pipeline stopped persisting the broad-book conviction when concentration went always-on (2026-07-08). **Persist the PRE-concentration combined conviction (and per-sleeve component scores, never persisted at all) as a daily research artifact.** Cost: one JSON per day. Benefit: every future re-run of this study gets gold data instead of reconstruction archaeology. (Research artifact suggestion only — no pipeline change made.)

## Provenance
- Workstreams: A (signal extraction + validation), B (IC/sweep/head-to-head), D (adversarial re-computation; found the >0-filter artifact and the era cancellation; confirmed no survivorship — all 128 tickers present, absent dates are market holidays; found the 93-vs-94 discrepancy = an all-cash day, immaterial).
- Nothing operational touched; the armed pilot untouched; all artifacts under this directory.
