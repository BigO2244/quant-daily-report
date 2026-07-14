# Workstream B — Live Shadow Evidence & Statistical Power

**Question:** Is the live-shadow evidence against concentration (Polaris_Alpha / Orion_Alpha
"lagging their diversified baselines by ~22-24%, negative absolute") signal, regime effect,
construction artifact, or short-sample noise?

**Bottom line:** The headline "22-24% lag" is a **window-mismatch measurement artifact**, not a
real performance gap. On a like-for-like window the differential is **-4.0 pp (Polaris_Alpha) and
+5.7 pp (Orion_Alpha)** — i.e. roughly flat, one slightly behind, one ahead — and **neither is
statistically distinguishable from zero** (p = 0.27 and 0.28; n = 14). The sample is ~17% of the
size needed for 80% power. The shadow books are also **not the live concentration construction**
(equal-weight 20/25% caps with 20-25% forced cash vs. live conviction waterfill at 0.50 cap, fully
invested), so even a clean result would not test the live config. **Classification: noise-dominated,
with the specific 22-24% figure being a window-mismatch + construction-mismatch artifact. High
confidence it is NOT meaningful signal.**

Data provenance: daily returns read read-only from the VM
(`caerus-vm:~/quant-daily-report/outputs/shadow_candidates/<date>/shadow_performance.json`,
`.../shadow_evaluation.json`, `.../performance/shadow_nav_series.csv`). No live/paper/config state
touched. Assembled series in `B_daily_shadow_returns.csv`, `B_daily_relative_series.csv`.

---

## 1. Where the "22-24%" comes from, and what it actually measures

The number is reproduced **exactly** from the pipeline's own `shadow_evaluation.json`
(2026-07-13), field `cumulative_return`:

| Strategy | Pipeline `cumulative_return` | `nav_observation_count` | Window start |
|---|---:|---:|---|
| caerus_polaris (baseline) | **+9.45%** | 42 | ~2026-05-12 |
| Polaris_Alpha | **-13.13%** | 14 | 2026-06-23 |
| caerus_orion (baseline) | **+10.23%** | 42 | ~2026-05-12 |
| Orion_Alpha | **-10.90%** | 14 | 2026-06-23 |

Gap = -13.13 - 9.45 = **-22.58 pp** (Polaris); -10.90 - 10.23 = **-21.12 pp** (Orion). That is the
"~22-24%". (Using the `alpha_per_dollar_deployed_proxy` fields instead gives ~-25 to -26 pp, which
is where the upper "24" comes from.)

**It is an apples-to-oranges cumulative-return difference over two different windows.** The baseline
cumret is measured over **42 NAV observations back to the 2026-05-12 shadow observation_start**; the
alpha cumret over only **14 observations since the 2026-06-23 activation**. Decomposing the
baseline's +9.45%:

| Baseline | 05-12 → 06-22 (PRE-activation) | 06-22 → 07-13 (common window) | Full (reported) |
|---|---:|---:|---:|
| caerus_polaris | **+28.46%** | **-14.79%** | +9.45% |
| caerus_orion | **+42.25%** | **-22.51%** | +10.23% |

The entire "lag" is the **+28%/+42% rally the baselines booked before the alpha books existed.**
Over the common window the baselines were *also* deeply negative (-14.8% / -22.5%).

### The correct, like-for-like comparison (both indexed to 1.0 at 2026-06-22 close, 14 days)

| Book | Cumulative return, common window |
|---|---:|
| caerus_polaris | -14.79% |
| **Polaris_Alpha** | **-18.83%** → **-4.04 pp vs baseline** |
| caerus_orion | -22.51% |
| **Orion_Alpha** | **-16.84%** → **+5.67 pp vs baseline (outperforms)** |
| caerus_lyra | -16.91% |
| SPY | +0.64% |

"Negative absolute" is literally true — but **every momentum sleeve is negative** over this window
(baselines -14.8%/-22.5%, Lyra -16.9%). This is a **momentum/semiconductor drawdown window**
(holdings are WDC/MU/STX/INTC), not alpha-specific destruction. Only SPY was ~flat.

**What the 22-24% actually is:** a cumulative-return figure computed over mismatched windows that
credits the baseline with a pre-inception rally. It is **not** an annualized alpha, and it is **not**
the common-window performance gap (which is -4pp / +5.7pp).

---

## 2. Sample size and gaps

- **14 valid trading days** with alpha data: 2026-06-23 → 2026-07-13 (matches pipeline
  `nav_observation_count = 14`).
- **Gaps:**
  - **2026-07-03** — `data_status: NO_DATA / PRICE_CACHE_STALE`; daily return forced to 0.0
    (carried flat, excluded from the 14). A genuine artifact gap of the kind flagged in the June
    scorecard incident.
  - **2026-07-14** (today) — `NO_DATA / PRICE_CACHE_STALE`, no returns yet.
  - **2026-06-23** — missing from `vix_regime/regime_history.csv` (regime coverage starts 06-24).
  - First local artifact (2026-06-24) carried `status: BROKEN_CHAIN / SHADOW_PRIOR_ARTIFACT_MISSING`;
    the VM chain is intact from 06-23.

---

## 3. Shape of the (non-)underperformance

Daily relative series `r = alpha − baseline` (`B_daily_relative_series.csv`):

- **Not catastrophic single names.** The 3 worst relative days are 2026-06-29, 06-30, 06-24 for
  Polaris (≈ -1.9 to -2.3% each) — these are **up-market rebound days on which the concentrated
  4-name / cash-heavy book captured less of the baseline's bounce**, not idiosyncratic single-name
  blowups. No day exceeds ~2.3% relative.
- **Not a steady bleed.** Mean daily relative: Polaris -0.37%/day, Orion **+0.44%/day**. The two
  sleeves have *opposite* signs — inconsistent with any systematic "concentration destroys alpha"
  bleed.
- **No regime signal available.** VIX is **LOW on 13 of 14 days** (one ELEVATED day, 06-26, VIX
  20.19); everything else 15.8-19.1. There is essentially no regime variation to split on — the
  drawdown is a sector/momentum event inside a calm-VIX tape, not a VIX-regime shift. Regime
  attribution is not estimable on this window.
- **Cash drag is a TAILWIND here, not the cause.** Polaris_Alpha holds 20% cash, Orion_Alpha 25%
  (forced: equal-weight top-4×20% = 80% invested; top-3×25% = 75% invested). In a **down** window
  the uninvested cash *cushioned* losses:

  | Book | As-reported (with cash) | Fully-invested same names | Cash effect |
  |---|---:|---:|---:|
  | Polaris_Alpha | -18.83% | -23.33% | **+4.49 pp** |
  | Orion_Alpha | -16.84% | -22.36% | **+5.52 pp** |

  So cash did not cause the apparent lag; if anything it flattered the alpha books. Polaris_Alpha's
  -4pp common-window gap is *despite* a +4.5pp cash tailwind — i.e. the concentrated 4-name basket
  itself underperformed the 10-name baseline by ~8.5pp gross of cash, while Orion_Alpha's 3-name
  basket roughly matched its 5-name baseline gross.

### Construction mismatch — the shadows do not test the LIVE concentration config

| Dimension | Shadow Polaris_Alpha | Shadow Orion_Alpha | **LIVE concentration** (`core/concentration.py`) |
|---|---|---|---|
| Weighting | equal | equal | **conviction `_capped_waterfill`** |
| Per-name cap | 0.20 | 0.25 | **0.50** (`DEFAULT_MAX_POSITION_WEIGHT`) |
| Names | top-4 | top-3 (rank-decay) | waterfill over ranked convictions |
| Forced cash | **20%** | **25%** | **0%** (full budget deployed) |
| HHI / eff-N | 0.25 / 4.0 | 0.33 / 3.0 | up to ~0.35-0.5+ / ~2-3 |

The live book waterfills conviction to a 0.50 cap and stays fully invested; the shadows are
equal-weight, low-cap (0.20/0.25), and structurally hold 20-25% cash. Different cap, different
weighting, different cash — **the shadow "_Alpha" books are a materially milder, cash-diluted
proxy and are not a faithful test of the live 0.50-cap conviction waterfill.** Any verdict about
"live concentration" drawn from these shadows inherits this mismatch.

---

## 4. Statistical power (daily relative series, n = 14)

Parametric one-sample t-test on daily `r = alpha − baseline`, plus stationary block bootstrap
(block length 3, B = 20,000) on the cumulative sum:

| | Polaris_Alpha − caerus_polaris | Orion_Alpha − caerus_orion |
|---|---:|---:|
| Mean daily rel | -0.3725% | +0.4447% |
| SD daily rel | 1.1975% | 1.4834% |
| **t-stat** | **-1.16** | **+1.12** |
| **p-value (2-sided)** | **0.265** | **0.282** |
| Bootstrap cum (obs) | -5.22% | +6.23% |
| Bootstrap 95% CI | [-16.20%, +5.26%] | [-5.46%, +14.65%] |
| Bootstrap p | 0.31 | 0.40 |
| **N for 80% power** (detect this effect vs 0, α=0.05) | **≈81 days (~3.9 mo)** | **≈87 days (~4.2 mo)** |
| **MDE at n=14** (80% power) | daily 0.90% → **~12.6% cum / ~226% ann** | daily 1.11% → **~15.5% cum / ~280% ann** |

Both the parametric and bootstrap CIs **straddle zero comfortably**. Neither sleeve's relative
performance is distinguishable from noise.

**How underpowered:** we have **14** trading days; ~**81-87** are needed for 80% power to confirm an
effect of the *observed* size. That is **~17% of the required sample**. At n=14, only a monster
effect — **~12.6-15.5% cumulative over the window, i.e. ~230-280% annualized** — would be
detectable; anything realistic (single-digit annual alpha) is statistically invisible. Any
annualized-alpha claim from this window (e.g. the naive annualizations of the daily means are
-94%/+112%) is **noise amplified by extrapolation** and should be disregarded.

---

## 5. Verdict input

**Classification: NOISE-DOMINATED, with the "22-24% lag" being a WINDOW-MISMATCH ARTIFACT
compounded by a CONSTRUCTION-MISMATCH artifact. Not statistically meaningful signal. Confidence:
HIGH.**

Evidence:
1. **The 22-24% is a measurement artifact.** It compares baseline cumret over 42 obs (incl. a
   +28%/+42% pre-activation rally) against alpha cumret over 14 obs. Like-for-like, the gap is
   **-4.0 pp (Polaris) / +5.7 pp (Orion)** — one slightly behind, one ahead, net ~flat.
2. **Not signal.** t = -1.16 / +1.12, p = 0.27 / 0.28; bootstrap CIs both straddle 0. The two
   sleeves disagree in sign.
3. **Not a regime artifact in the VIX sense** (no regime variation to attribute to — 13/14 days
   LOW-VIX), though it *is* confined to one short momentum/semiconductor drawdown window.
4. **Cash drag is a tailwind, not the cause** (+4.5 / +5.5 pp in this down window).
5. **Construction mismatch** means even a clean shadow result would not test live concentration
   (equal-weight 0.20/0.25 cap + 20-25% cash ≠ conviction waterfill 0.50 cap, 0% cash).
6. **Severely underpowered** — 14 of ~81-87 days needed; MDE ~230-280% annualized.

The live-shadow evidence **does not support** a claim that concentration destroys alpha. It neither
confirms nor refutes the concentration thesis — it is simply too short, mis-windowed, and
mis-constructed to carry weight. The 20/60-day review checkpoints in the governance doc are the
right gate; at 14 days the book is pre-checkpoint and underpowered by design.
