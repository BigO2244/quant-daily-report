# Workstream D — Adversarial Methodology Review

Date: 2026-07-14 · RESEARCH_ONLY / NON_EXECUTIONAL · Role: adversarial methodologist (broke nothing I wrote — I wrote none of it).
Method: re-ran every load-bearing number from source data with an independent from-scratch harness where possible, not just
recomputed from the authors' own output CSVs. Scratch: `scratchpad/D_*.py`. Pilot untouched; all reads read-only.

## TL;DR for the memo

**I could not break the core verdict. The five claims survive genuinely hostile re-computation — every load-bearing number
reproduces, several to the 3rd–4th decimal, including from a harness I wrote independently of `engine_ws_a.py`.** The verdict
("concentration is variance/luck harvesting with no fine-rank skill; N≈5 is a fragile post-2019 plateau, not a law; the live
lag is underpowered noise") is **safe to base the memo on.**

But I found **five real weaknesses** that require caveats — none fatal, three of which actually *strengthen* the skeptical
verdict, two of which mean specific numbers must not be quoted as stable point estimates:

1. **The "PIT" universe has a genuine look-ahead leak** (static `scalemarketcap`, membership from IPO) that makes the
   headline top5−top10 edge swing from **+0.13 to −0.12** depending on a defensible eligibility choice. → the *edge number*
   is not a stable estimate (reinforces C's plateau).
2. **"Survivorship is the dominant bias" is a loose label** — the B3 delta is substantially a **breadth/composition** effect,
   not delisted-losers vanishing (verified: dead names are priced through death, only ~13% of slots).
3. **The cost model is optimistic for exactly the names that carry the edge** (AXSM/AAOI/ARWR were micro-caps when momentum
   first bought them; 5 bps ≠ their real spread). → the thin concentration edge is even thinner net of realistic costs.
4. **Under the live-realistic waterfill sizing, the N≈5 peak essentially vanishes** (top5 0.941 ≈ top10 0.935). A's framing
   that "the live sleeves sit on the wrong side of the N≈5 ridge" is overstated — there is barely a ridge under live sizing.
5. **B's "-4pp, roughly flat" reframing is cash-flattered**; gross-of-cash (closer to the fully-invested live config) the
   Polaris concentrated basket lagged **−8.5pp**. The construction mismatch cuts partly *against* the live config, not for it.

**The single biggest structural caveat (both A and C flag it, and it undercuts mapping ANY of this onto the live book):**
the entire chain measures the **momentum-only** book that produced the 46%/1.27 claim, NOT the live combined-allocator
conviction. Everything downstream assumes `momentum_score ≈ live conviction`. That assumption is unverified.

---

## Per-claim verdicts (with re-computed numbers)

### CLAIM 1 (A — provenance, bias audit) → **CONFIRMED on reproduction; LABEL WEAKENED; +2 new caveats**

- **46%/1.27 reproduces exactly.** `claim_repro.json`: top5 CAGR 0.4602 / Sharpe 1.27, top10 0.3141 / 1.101. Matches the
  published `h6_top5_daily` object. **Confirmed.**
- **Bias table B1–B4 reproduces** (`bias_audit_table.csv` = doc): B1 1.105, B2 1.193, B3 0.908 (MDD −70.2%), B4 0.869. The
  B3 top10 (0.851 / −54.4%) independently equals the prior FR-068 PIT rebaseline — real cross-check, not circular.
- **No survivorship leak *inside* the PIT panel.** I verified the SEP cache carries delisted names through death (ATVI end
  2023-10, TWTR 2022-10, priced to their last day) and that **eventually-delisted names fill ~13% of top-5 and top-10
  selection slots** — they are held and carry their crashes, they do not silently vanish. The PIT machinery is honest here.
- **Membership mask timing is correct** — `signal_ready & pit_eligible` at formation date, rank on `momentum_score` through
  close[t], return `close[t+1]/close[t]` (`.shift(-1)`). No formation/return look-ahead in the mask. B4 bounds the
  trade-at-signal-close optimism at 0.04 Sharpe.

**WEAKENED — the word "survivorship":** the B3 delta (Sharpe 1.193→0.908, MDD −40%→−70%, CAGR ~flat) is *mostly a breadth /
universe-composition* effect (200→1,250 names), not classic delisted-loser survivorship. A itself says the dominant channel is
"active large-caps the 200 omits (ENPH, PLUG, CVNA…)." Verified: top-5 slot share is dominated by ARWR/ENPH/MDGL/AXSM/PLUG/
AAOI/CVNA (~2–3% each). Direction of the correction is right (universe realism lowers Sharpe, doubles the tail); the
attribution word is loose.

**NEW CAVEAT 1 — look-ahead in the universe definition.** `membership_universe_large_cap.csv` uses `scale_source =
scalemarketcap` (a **static, present-day** Sharadar scale class), and I verified `membership_start_date == first_price_date`
for AXSM (2015-11-19), CVNA (2017-04-28), ENPH (2012-03-30), NVAX, MDGL, AAOI, PLUG. **So every company that is large-cap
*today* is in the tradeable universe from its IPO — including its entire micro/small-cap ascent.** That is look-ahead: you
only know AXSM became a durable large-cap in hindsight, yet momentum gets to ride it from 2015. C proved AXSM alone drives
76% of the top5−top10 edge. When I stress this with a PIT-plausibility age gate (name eligible only after K trading days
listed), the **top5−top10 Sharpe edge swings +0.053 (1yr) → +0.134 (2yr) → +0.081 (3yr) → −0.123 (5yr)** — i.e. it is not
robust to the eligibility boundary. This does not cleanly *inflate* the edge, but it means the specific number is unstable.

**NEW CAVEAT 2 — cost model unfair for the edge-driving names.** A assumes uniform 5 bps (claim: 10 bps) large-cap
half-spreads and notes "not per-name tiered — all names are large-cap." **False at trade time:** AXSM/AAOI/ARWR were
illiquid micro-caps when momentum first bought them (that is *when* the returns were made). Real spreads there run 20–50+ bps.
The concentration edge rests on exactly these names, so realistic entry-era costs would erode it further than the 5 bps model
shows. (Minor/cosmetic: `claim_repro.json` labels `cost_bps_assumption: 25.0`, but the effective applied cost is 10 bps —
`cost_drag 368 / ann_turnover 36.8 = 10.0`. Mislabel only; the 46.02%/1.270 reproduction is unaffected.)

### CLAIM 2 (A — clean PIT sweep numbers) → **CONFIRMED (exactly, independently); peak is FRAGILE**

Reproduced **from a from-scratch harness** (`D_gate.py`, built off the raw `signals_largecap_pit.parquet`, not A's engine):
EW clean, top1 **0.748** / top3 **0.808** / top5 **0.957** / top10 **0.904** / full **0.786** — identical to A. CAGR and
−92%/−69%/−52% MDDs also match. Waterfill matches too (0.748/0.850/0.941/0.935/0.854).

**CAVEAT — the "N≈5 peak" is not a robust feature, only the point estimate is:**
- Under **waterfill (the live-realistic sizing)**, top5 0.941 **≈** top10 0.935 (edge **+0.006**, essentially zero). The peak
  is an artifact of the EW presentation; under live-like sizing there is barely a ridge.
- Under the PIT-age-gate sensitivity above, the edge ranges +0.13 to −0.12.
- (This is exactly what Claim 5's bootstrap says — see below. The numbers are right; the *interpretation* "N≈5 optimal"
  must not be quoted.)

### CLAIM 3 (B — live lag is a window/construction artifact) → **CONFIRMED; mapping-to-live WEAKENED**

Every B number reproduces from `B_daily_shadow_returns.csv`:
- Like-for-like common window: polaris −14.79%, **Polaris_Alpha −18.83% (−4.04pp)**; orion −22.51%, **Orion_Alpha −16.84%
  (+5.67pp)**. Exact.
- Window-mismatch decomposition identity holds: (1+0.2846)(1−0.1479)−1 = +9.46% ≈ reported baseline +9.45%. Consistent.
- Power: t = −1.164 / +1.118, p = 0.265 / 0.282; effect sizes d = 0.311 / 0.300 → **N80 = 81 / 87 days**. Exact. n=14 is
  ~17% of required. Cash tailwind +4.49 / +5.52pp reproduced.

**WEAKENED — the "roughly flat" reframing over-sells the live case.** Stripping the forced cash (the live config is
*fully invested* and *higher-cap*), the Polaris concentrated 4-name basket lagged its baseline by **−8.54pp** gross-of-cash
(Orion basket was flat, +0.15pp). So the construction mismatch cuts **partly against** the live config: a fully-invested,
0.50-cap live book maps toward the −8.5pp end, not "flat." B does state the 8.5pp figure in-text, but its headline
("one behind, one ahead, net flat") understates concentration drag for the *live* config. **This does not change B's verdict**
— at n=14 nothing is significant either way, and "noise-dominated / not meaningful signal / cannot test the live config" is
correct and safe.

### CLAIM 4 (C — no fine-rank selection precision inside the top decile) → **CONFIRMED (robust to the obvious objection)**

Independently recomputed within-top-20 IC vs the actual signal (`momentum_score`) from the ranking parquet + forward returns:
**−0.0116 / −0.0179 / −0.0272** at 1d/5d/21d, t = **−2.47 / −3.89 / −5.64**. Matches C (−0.0115/−0.0177/−0.0262) to the 3rd
decimal. Bucket ordering reproduced: rank 11-20 (3.259% @21d) > rank 6-10 (3.010%) — the perverse gap is real.

**The strongest available attack fails:** "negative IC is just 1d/5d mean-reversion, only 21d is fair." No — the within-top-20
IC is **most negative at 21d**. It is not a microstructure reversal; it is a genuine within-decile reversal/crowding effect at
the horizon momentum is supposed to work on. Claim 4 is **solid.** (Sign note for readers: correlating vs the *rank number*
flips the sign to +0.027; C's convention (vs the signal) is the correct one and its "perverse" reading holds.)

### CLAIM 5 (C — N≈5 is a noise plateau; only top5>top3 robust; post-2019) → **CONFIRMED (robust to block length)**

Re-ran the paired block bootstrap from A's daily series (`D_boot.py`, 5000 draws):
- 21d blocks: P(top5>top10) **0.603**, P(top5>top3) **0.854**, P(top5>top1) 0.750, P(top5>full) 0.679; argmax top5 0.336 /
  top10 0.229 / full 0.220; Sharpe[5−10] CI **[−0.257, +0.328]** (straddles 0). Matches C (0.593 / 0.867 / CI [−0.261,+0.325])
  within Monte-Carlo noise.
- **63d-block sensitivity (mission-requested, to respect multi-month momentum autocorr):** P(top5>top10) **0.599**, CI
  [−0.258, +0.305] — plateau conclusion holds. P(top5>top3) softens to **0.819** (from 0.867) but remains the strongest
  ordering.
- Subperiod reproduced exactly: 2014-2018 argmax = **full** (top5 0.444), 2019-2024 argmax = **top5** (1.310). The premium is
  a post-2019 phenomenon.

Solid. **One note:** the "one robust result — top5>top3 at 87%" weakens to ~82% under 63d blocks; still the only ordering the
data supports, but not as bulletproof as the single 21d figure suggests.

---

## Cross-cutting checks

- **A, C, and my independent harness agree exactly** on the top5/top10 clean series (0.957 / 0.904, gross 1.006 / 0.957,
  turnover 50.3 / 44.2, cost drag 251 / 221 bps). No internal contradiction; C's "reproduced A to the decimal" is true.
- **Costs do not drive top5>top10** (verified independently): gross edge +0.049, net +0.053, and top5 turns *more* (50.3 vs
  44.2). The edge is a gross-return/variance phenomenon. Confirmed.
- **The load-bearing structural weakness both A and C flag: signal divergence.** The whole study measures the alpha_lab
  **momentum** book, not the live combined-allocator conviction. Mapping "N≈5 plateau / no selection skill / live sleeves on
  the wrong side" onto the LIVE config assumes `momentum_score ≈ live target_weight`. Unverified. This is the correct place
  for the memo to hedge hardest.

---

## What the memo can rest on

**SAFE to state without qualification:**
- The 46%/1.27 headline is survivorship/window-biased and collapses on honest data (Claim 1 reproduction, Claim 2 numbers).
- Clean-PIT Sharpes: top1 0.75 / top3 0.81 / top5 0.96 / top10 0.90 / full 0.79 (Claim 2 — reproduced three independent ways).
- Momentum carries **no fine-rank information inside the top decile** (Claim 4 — negative IC, strongest at 21d).
- **The one robust concentration rule is "don't go below ~5 names"** (top5>top3); "5 is uniquely optimal" is NOT supported —
  N∈{5,10,full} is a bootstrap plateau, edge indistinguishable from 0 (Claim 5).
- The live-shadow "22-24% lag" is **not meaningful signal** — window-mismatch + n=14 underpowered (Claim 3).

**SAFE ONLY WITH THESE EXACT CAVEATS:**
- On the clean sweep peak: *"The top-5 Sharpe peak (0.96) is a point estimate on a single price realization; it is not
  statistically distinguishable from top-10 or the full book (block-bootstrap edge CI straddles zero), it vanishes under the
  live-realistic waterfill sizing (top5 0.941 ≈ top10 0.935), and it swings from +0.13 to −0.12 under defensible PIT-eligibility
  choices. Treat N∈{5,10,full} as a plateau, not a ridge."*
- On "survivorship": *"The dominant honesty correction is universe **breadth and composition** (200→1,250 names), of which
  delisted-name survivorship is one part (~13% of holdings); it is not primarily a dead-losers-vanishing effect."*
- On the PIT universe / edge durability: *"The PIT universe is defined by present-day market-cap scale with membership from
  IPO, so eventual large-caps are tradeable during their pre-large-cap ascent — a look-ahead enrichment. Combined with a
  uniform 5 bps cost assumption that understates the true spreads of those names in their small-cap years, the realized
  concentration edge is a soft upper bound."*
- On the live-shadow mapping: *"Gross of forced cash, the Polaris concentrated basket lagged its baseline by ~8.5pp over the
  common window; the live fully-invested, 0.50-cap config maps toward that end, not the cash-flattered −4pp. This remains
  statistically insignificant (n=14) and is offered only to prevent over-reading the shadow as 'flat.'"*
- Everywhere the verdict is mapped to the **live** book: *"All results measure the alpha_lab momentum book, not the live
  combined-allocator conviction; the mapping assumes momentum_score ≈ live conviction and is unverified."*

**Must be DROPPED / not quoted:**
- Any statement that "concentration adds risk-adjusted value up to an interior optimum at N≈5" as if N≈5 were special
  (A §4 phrasing). The data supports only "don't over-concentrate below ~5"; above 5 it is a flat plateau.
- Any naive annualization of the 14-day shadow (−94% / +112% etc.) — noise amplified by extrapolation (B says this too).

## New findings (not in A/B/C)

- **N1.** PIT universe has a static-`scalemarketcap` + membership-from-IPO look-ahead leak (verified against first-price
  dates); top5−top10 edge swings +0.13→−0.12 under an age gate → edge not robust to the eligibility boundary.
- **N2.** Cost model (5 bps flat) is optimistic for the micro-cap-at-entry names that carry the edge; real 20–50 bps would
  erode it further.
- **N3.** Under the live-realistic waterfill sizing the N≈5 peak collapses (top5 0.941 ≈ top10 0.935) — undercuts A's "live
  sleeves on the wrong side of the ridge" as overstated.
- **N4.** Gross-of-cash, the Polaris shadow basket lagged −8.5pp (not −4pp) — construction mismatch partly cuts against live.
- **N5.** `claim_repro.json` cosmetic mislabel `cost_bps_assumption: 25.0` (effective 10 bps) — non-material.

## What I could NOT break
- The 46%/1.27 reproduction, all bias-audit legs, the entire clean sweep grid, C's within-top-20 IC, the bootstrap plateau,
  the subperiod split, and B's window/power arithmetic **all reproduce from source** — several from a harness independent of
  the authors' code. No fabrication, no convenient arithmetic errors, no p-hacking found: C pre-committed the 200dma regime
  before the VIX split and explicitly reports the disagreement as non-robustness (verified in the doc's ordering), and the
  bootstrap/subperiod/eligibility fragilities all point the *same* skeptical direction the authors argue. The methodology is
  honest; my caveats sharpen it, they do not overturn it.
