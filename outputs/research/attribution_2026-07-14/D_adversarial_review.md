# D — Adversarial Review of the Caerus Attribution Engine

Reviewer: independent adversarial verifier (did not write the engine). All numbers below
are RE-COMPUTED from source, most with my own pandas rather than the engine's functions.
Scratch: `scratchpad/D_placebo.txt`, `D_validation.txt`, `regen_paper/`, `regen_live/`,
`regen_sub/`.

Verdict legend: **CONFIRMED** / **WEAKENED** / **BROKEN**.

---

## Claim 1 — Internal identity (name=sleeve=regime=factor=total; Carino) — **CONFIRMED**

Re-ran both lanes into fresh dirs; every delivered CSV and the report are
**byte-for-byte identical** to the regenerated outputs (paper + live, 16/16 CSVs match).

Independent reconstruction (my own weight/drift/Carino code, not theirs):

- Geometric total R = `0.11982606258827078` — matches delivered to 1e-17.
- Daily identity max |Σ_i c_i(t) − r_p(t)| = **6.9e-18** (matches the claimed 7e-18).
- Carino-linked name sum − R = 6.9e-16; sleeve − R = 6.8e-16; my name attribution vs
  delivered max diff = 9.9e-17. Delivered reconciliation residuals are 7.2e-12 bps
  (< the claimed 1e-11 bps). PASS.
- Carino robustness: per-day scale k_t/k spans **1.033–1.082** (never blows up); smallest
  non-zero |r_p| = 5.5e-5, well above the 1e-12 guard; degenerate r_p≈0 handled by the
  →1 limit. No reconciliation trick — the identity is real, not a plug.

Double-counting traps — all clean:
- Multi-sleeve names: NAME attribution groups by ticker (one weight per name, no
  duplication); the equal-split only touches the SLEEVE cut. No date-ticker has >1 row and
  no ticker carries >1 sleeve on a given day, so `target_weight.sum()` is identity.
- CASH: weight = max(0, 1−Σw), earns 0, drifts at 0, book renormalised incl. cash each
  day. Never enters name/sleeve sums. Weight sums ∈ [0.31, 1.00], mean 0.98 — no leverage,
  no negative cash.
- Rebalance boundary: on a signal's T+1 effective day the BOD weight resets to the raw
  target (un-drifted); all four cuts consume the SAME `contrib`/`rp`, so the boundary is
  identical across decompositions by construction.

Caveat to carry: the **multi-sleeve equal-split is an arbitrary convention** (8.8% of gross
|contrib| is multi-tagged). Under a "first-listed sleeve" rule instead, sleeve_quality
moves +13.33% → +14.89% and sleeve_trend −2.66% → −4.38%. Sign-stable (quality carries,
trend drags) but the sleeve numbers are convention-dependent at the ~1.5pp level. FTNT and
STX (the two biggest winners, +5.5%/+4.5%) are tagged `sleeve_quality, sleeve_trend` on
some days, so trend is being credited half of their up-days under the equal split.

---

## Claim 3 headline — decomposition arithmetic CONFIRMED, "~95% beta / near-zero alpha"
interpretation **BROKEN**

The reported numbers reproduce exactly: +11.98% total, quality +13.33% carries, trend
−2.66%, market β=1.20, momentum β=0.33 the only significant loads, R²=0.593, unexplained
−0.59% (−4.9%). As arithmetic on this book, all correct.

But the *inference* drawn from R²=0.593 does not survive a placebo. **THE DECISIVE TEST**
(scratchpad/D_placebo.txt), 2000 random equal-weight 17-name books drawn from the SAME
136-name universe, regressed on the SAME 6 factors over the SAME 109 days:

| book | R² | market β |
|---|---|---|
| **actual book** | **0.593** | **1.200** |
| random 17-name EW (median of 2000) | **0.738** | 1.055 |
| random 17-name EW (p05–p95) | 0.604–0.847 | 0.88–1.24 |
| full 136-name EW (max breadth) | 0.932 | 1.055 |

- **96.4% of random books beat the actual book's R²=0.593.** Pure noise + breadth produces
  a HIGHER R² than this book. The actual book is if anything *less* factor-explained than a
  coin-flip basket — it carries MORE idiosyncratic variance, not less.
- So R²=0.593 is a **mechanical breadth artifact** (the holdings are constituents of SPY/
  MTUM/QUAL), not evidence of "95% beta." The "~95% beta, near-zero alpha" characterization
  of the strategy is unsupported: the same test on random names says the same or "more beta."
- Market β=1.20 sits at the placebo p95 — mildly, genuinely elevated vs a random basket
  (that part is real: the book does run a bit hot on market). Momentum β=0.33 significant is
  plausible. But R² carries none of the claimed skill message.
- **"Near-zero alpha" is a no-power non-result.** Intercept = −0.15 bps/day, HAC se ≈ 7.7
  bps/day → annualized alpha ≈ −0.4% ± ~12%. The test cannot distinguish −12% from +12%
  alpha. It is not a finding that alpha is ~0; it is that alpha is unmeasurable at N=109.

Regime split — **partly CONFIRMED, conclusion WEAKENED**:
- Thresholds 20/30/40 are genuinely pre-registered: `sleeves/sleeve_trend/config.py`
  lines 159–162 (`VIX_LOW/ELEVATED/HIGH_THRESHOLD`). Not chosen after seeing results. The
  engine classifies ALL 109 days off the cached ^VIX close (one taxonomy), using the
  recorded tag only as fallback — so this is more consistent than the "21 backfilled days"
  worry implies. Good.
- BUT ELEVATED −6.83% is **event-driven, not structural**. It rests on 2–3 days out of 34:
  worst days −4.22% (02-05), −3.06% (02-23), −2.50% (06-05). Simple-sum ELEVATED = −5.95%;
  **removing the two worst days flips it to +1.35%.** April ELEVATED was +2.2%. The
  LOW +20.35% / ELEVATED −6.83% story is "we had a few bad tape days that happened to be
  ELEVATED," not a robust regime edge. HIGH is only 2 days (ignore).

---

## Claim 2 — Validation vs NAV — numbers CONFIRMED, honesty **WEAKENED**

Recomputed on the 25-day overlap (scratchpad/D_validation.txt): corr 0.799, RMS TE 58.1
bps/day, mean diff +7.6 bps, cumulative shadow −1.03% vs real NAV −2.85%. All match.

The 58 bps figure is doing far less reassuring work than the corr 0.80 suggests:
- **TE / NAV-daily-vol = 0.78.** The daily tracking error is ~78% of the real book's own
  daily volatility (75 bps). The error is nearly as large as the thing being tracked.
- **corr² = 0.64** — the shadow explains only 64% of the real book's daily-return variance;
  36% is unexplained by the reconstruction.
- The shadow's overlap-window daily vol is **97.7 bps vs the real book's 74.9 bps** — the
  shadow is ~30% more volatile, i.e. a materially different risk profile (real book damped
  by the options overlay / exposure scaling the shadow ignores).
- The one time we can check level, the real book was **1.8% worse over 25 days** (+7.6
  bps/day drift). If that drift is representative, the +11.98% shadow headline overstates
  realized P&L by a non-trivial amount over the full window.

State plainly what this is attribution OF: **the recorded target-weight book (a shadow),
rebalanced to target on signal days and drifted between them — NOT realized cash P&L.**
Slippage, partial fills, rejects, and the options overlay are outside the decomposition.
The validation is a loose directional/shape check, not a magnitude validation.

---

## Claim 4 — Live lane refuses factor regression at N=11 — **CONFIRMED, but cosmetic**

The markdown prints the "N=11 < 60: factor regression REFUSED" banner. However the engine
**still computes and writes** `factor_betas_live.csv` and `factor_attribution_live.csv`
(R²=0.907, momentum t=−4.51, lowvol t=−12.67) and **still emits a passing "factor" row in
`reconciliation_live.csv`** — with no refusal flag anywhere in the CSVs. A downstream
consumer reading the CSVs sees fully-formed, reconciling factor numbers and no signal that
they were disavowed. The refusal is text-only, not enforced in the data artifacts. Fix:
either suppress the factor CSVs / null the betas when N<MIN_FACTOR_OBS, or stamp a
`refused=True` column.

---

## Claim 5 — Repeatable / cron-safe — **CONFIRMED with one real caveat**

- Regen byte-identical on both lanes (above). Subwindow `--start 2026-05-01 --end
  2026-07-13`: runs clean, N=49<60 correctly REFUSES factor, reconciles to 4e-12 bps, and
  daily r_p is **byte-identical to the full run on overlapping dates (max diff 0.0)** — the
  drift state entering the window is carried correctly, no boundary artifact.
- No `yfinance`/`requests`/`urllib`/`http` imports in `attribution_report.py`: **report time
  is offline-safe**, reads only the cached parquet/CSV. (Only `fetch_factor_prices.py`
  touches the network, and it is a separate manual step.)
- Caveat: "no hardcoded dates/paths" is not literally true. Input paths hardcode the DATED
  study dirs `concentration_live_signal_2026-07-14/` and `attribution_2026-07-14/`
  (lines 60–63), and paths are repo-root-relative. A cron re-run tomorrow would deterministic­
  ally reprocess the SAME frozen snapshot, not fresh data, unless those dirs are updated or
  re-pointed. Deterministic and re-runnable: yes. Self-updating: no.

---

## Caveat wording the final report MUST carry (verbatim)

> This engine attributes the **recorded target-weight book (a shadow)**, not realized cash
> P&L; slippage, fills, rejects, and the options overlay are excluded. The one available
> reality check (25 days vs live-overlay NAV) shows daily tracking error of 58 bps — about
> 78% of the real book's own daily volatility — and the real book running ~1.8% behind the
> shadow over those 25 days, so the +11.98% headline is a target-book figure that likely
> overstates realized return.
>
> The factor R²=0.593 is a **breadth artifact, not a skill measurement**: a random 17-name
> equal-weight book from the same universe scores a higher median R² (0.738), and 96% of
> such random books exceed 0.593. R² therefore does NOT support "~95% beta." Alpha is
> **unmeasurable at N=109** (intercept CI ≈ ±12% annualized), so "near-zero alpha" is a
> no-power non-result, not a finding. Market β≈1.2 is mildly elevated vs a random basket
> and is the one factor claim with real content.
>
> Regime results are event-driven: ELEVATED −6.83% flips positive if the two worst tape
> days are removed. Sleeve splits for multi-tagged names are an equal-split convention;
> the quality/trend numbers shift ~1.5pp under an alternative rule (sign-stable).

---

## Bottom line

**Is the engine's math trustworthy as the measurement layer? YES — the arithmetic is
sound.** The additive identity, Carino linking, reconciliation, no-double-counting, and
determinism/repeatability all verify independently to machine precision, byte-for-byte.

**What must be fixed before the OUTPUT is used for decisions (the interpretation, not the
math):**
1. Strike/rewrite the "~95% beta, near-zero alpha" conclusion — the placebo shows R² is
   breadth, and alpha has no statistical power at N=109. This is the load-bearing fix.
2. Relabel every headline as the **target-book shadow**, and surface the validation gap
   (58 bps TE ≈ 0.78× the real book's vol; real book −2.85% vs shadow −1.03%) next to the
   +11.98%, not buried in notes.
3. Downgrade the regime conclusions to "event-driven" and show the 2-day sensitivity.
4. Make the live-lane factor refusal real in the CSVs (null/flag the betas, don't emit a
   clean reconciling factor row at N=11).
5. Note the sleeve equal-split convention dependence and the hardcoded dated input paths.

The measurement layer is a trustworthy calculator. Several of the *claims written on top of
it* overstate what the numbers can support and must be corrected before circulation.
