# Workstream A — Backtest Bias Audit + Clean Concentration Sweep

Date: 2026-07-14 · RESEARCH_ONLY / NON_EXECUTIONAL · Branch release/pre-arm-fixes-2026-07-13
Author: Workstream A. Companion: `METHODOLOGY.md`. Artifacts: `artifacts/`, cached data `data/`.

---

## 0. TL;DR

- **The 46%/1.27 claim is real but survivorship- and window-biased.** It is the Alpha Lab v2 single-change variant
  `h6_top5_daily` (daily EW top-5 momentum) vs baseline `baseline_top10_daily` (31.6%/1.11), priced on the
  **static-200 current universe** with **yfinance** prices over **2014→2026-Q2** (which includes the reserved 2025+ holdout).
  I reproduce it to the decimal (46.02% / 1.270).
- **Corrected for survivorship (PIT large-cap universe) + holdout exclusion, top-5 falls from 46%/1.27 to ~38–43% CAGR
  and ~0.87–0.96 Sharpe, with max drawdown deepening from −41% to −69%.** The headline Sharpe collapses ~25–30%.
- **Concentration still helps — but only to a point, and far less than claimed.** On the honest PIT universe, Sharpe by
  level peaks at **N≈5** (0.96) and is WORSE at N=1 (0.75) and N=3 (0.81). The live sleeves sit at N=3–4, i.e. on the
  **wrong side of the optimum**, in the low-Sharpe / high-drawdown regime. This is consistent with the live shadow lag.
- Drawdown worsens monotonically with concentration: full −40% → top10 −52% → top5 −69% → top3 −74% → top1 −92%.

---

## 1. Source of the 46%/1.27 claim (exact provenance)

| Item | Finding |
|---|---|
| Metric object | `outputs/research/alpha_lab_v2_h2_h6_shadow_observation_plan.json` → `baseline_comparisons.alpha_lab_v2_best_single_change.baseline_metrics`: **cagr 0.4616, sharpe 1.273, top_n 5, label `h6_top5_daily`, "H6 only: daily top 5 concentration"**. Top-10 comparator = `alpha_lab_v2_baseline_top10_daily`: **cagr 0.3158, sharpe 1.106**. |
| Engine | `research/alpha_lab_v2/engine.py :: run_backtest` + `research/alpha_lab_v2/hypotheses.py` (spec `h6_top5_daily`, EW top-5, daily, 10 bps). |
| Signal | `research/alpha_lab_v1/signals.py :: build_alpha_lab_signal_frame` (momentum 0.5·r12_1+0.3·r6_1+0.2·r3, close-only). |
| Universe | **`data/universe.csv`** — static 200 **current** tickers (survivorship-biased; confirmed CONFIRMED_BIASED in `research/survivorship_bias_audit_2026-06-10.md`). Loaded via `research/alpha_lab_v2/run.py` → `load_universe`. |
| Prices | **yfinance auto_adjust**, `outputs/research/flow_detection_v1/price_panel.parquet` (2014-01-02 → 2026-06-24). |
| Window | 2014-01-02 → ~2026-04-16 (n_years 12.27). **Includes the reserved 2025+ holdout / live-shadow period.** |
| Costs | 10 bps × turnover. Ranking timing: decision at t-close, return t→t+1 (trade-at-signal-close). |

**Reproduction (my faithful harness, same panel/window):** top-5 = **46.02% / 1.270**, top-10 = **31.41% / 1.101**
(`artifacts/claim_repro.json`). Match confirmed. The engine could be re-run exactly — no reconstruction guesswork needed.

Note: a **priced PIT rebaseline for the top-10 baseline already exists** (`research/polaris_pit_priced_rebaseline_2026-06-10.md`):
Sharpe 1.054→0.851 (−19.3%), MDD −43%→−54%. **The top-5 concentration variant had never been PIT-rebaselined — that gap is this deliverable.**

---

## 2. Bias audit — quantified (EW daily, 10 bps to isolate universe/window/price)

Artifact: `artifacts/bias_audit_table.csv`. Each row corrects one more defect; deltas are cumulative down the table.

### Top-5 (the claim)
| Stage | CAGR | Sharpe | MaxDD | Δ vs prior (Sharpe) | What it corrects |
|---|---:|---:|---:|---:|---|
| R0 biased claim (yfinance, static-200, →2026Q2) | 46.02% | **1.270** | −41.2% | — | (the published number) |
| B1 exclude 2025+ holdout (→2024-12-31) | 34.89% | 1.105 | −40.1% | −0.165 | **windowing bias** (claim window bleeds into reserved holdout, a strong-momentum stretch) |
| B2 price source yfinance→SEP closeadj | 40.05% | 1.193 | −40.1% | +0.088 | data-source reconciliation (not a "bias"; SEP adj differs) |
| B3 survivorship static-200 → **PIT large-cap** | 39.53% | **0.908** | **−70.2%** | **−0.285** | **survivorship / universe curation** (the big one) |
| B4 + execution lag +1 day | 37.57% | 0.869 | −63.6% | −0.039 | **look-ahead / trade-at-signal-close optimism** |

### Top-10 (the comparator; also cross-checks the prior PIT rebaseline)
| Stage | CAGR | Sharpe | MaxDD |
|---|---:|---:|---:|
| R0 (yfinance, static-200, →2026Q2) | 31.41% | 1.101 | −43.1% |
| B1 exclude holdout | 24.99% | 0.969 | −43.1% |
| B2 SEP price | 28.83% | 1.054 | −43.2% |
| B3 PIT large-cap | 30.68% | 0.851 | −54.4% |
| B4 + exec lag | 25.72% | 0.754 | −54.9% |

**B3 top-10 (30.68% / 0.851 / −54.4%) reproduces `polaris_pit_priced_rebaseline_2026-06-10.md` (30.68 / 0.851 / −54.4) exactly** — independent confirmation the PIT machinery and my harness agree.

**Bias summary (top-5, MEASURED):**
1. **Survivorship / universe curation (B3): SEVEREST.** Sharpe 1.193→0.908 (−24% relative), MaxDD −40%→−70% (tail nearly doubles). CAGR barely moves (curated-200 ≈ PIT gross) — the damage is entirely risk-adjusted quality and tail risk. Per prior art this delta blends *breadth* (PIT ~1,250 names vs 200) with survivorship; both are legitimate honesty corrections. Dominant channel is **active large-caps the 200 quietly omits** (ENPH, PLUG, CVNA, GME…), not delisted losers.
2. **Windowing / holdout inclusion (B1): MATERIAL.** Sharpe 1.270→1.105, CAGR 46%→35%. The claim's window runs into 2025-2026, a strong-momentum, in-live period; excluding it (the governance convention) removes ~11 CAGR pts and 0.17 Sharpe.
3. **Execution-timing look-ahead (B4): MODEST.** Trade-at-signal-close vs +1-day lag costs ~0.04 Sharpe, ~2 CAGR pts. Present but not the story.
4. **Cost assumption:** the claim's 10 bps is *not* optimistic — realistic large-cap half-spread (5 bps) is lower (see §3). Costs are not a source of upward bias here.

**Net: fully-corrected top-5 ≈ 37.6% / 0.87 (10 bps, PIT, clean window, lagged) — the 1.27 Sharpe headline is ~30% too high and the −41% drawdown was understated by ~30 points.**

---

## 3. Clean concentration sweep (PIT universe, survivorship-free, realistic 5 bps)

Full grid: `artifacts/clean_sweep_table.csv`; settled-cash drag: `artifacts/settled_cash_drag_table.csv`. rf=0, 252/yr.
Benchmark **SPY 2014-2024: CAGR 13.2%, Sharpe 0.808, MDD −33.7%.**

### Primary — clean window 2014-01-02 → 2024-12-31, **EW** sizing, 5 bps, no settlement drag
| Level | CAGR | Sharpe | Sortino | MaxDD | Ann.turnover | Avg #names | Cost drag (bps/yr) | Hit rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| top1 | 33.4% | 0.748 | 1.290 | −92.1% | 54.6 | 1.0 | 273 | 48.7% |
| top3 | 37.1% | 0.808 | 1.338 | −74.3% | 49.5 | 3.0 | 248 | 50.8% |
| **top5** | **43.1%** | **0.957** | **1.506** | −68.8% | 50.3 | 5.0 | 251 | 53.1% |
| top10 | 33.6% | 0.904 | 1.322 | −52.4% | 44.2 | 10.0 | 221 | 54.2% |
| full-book | 14.5% | 0.786 | 0.984 | −40.4% | 0.19 | 1214 | 1 | 54.5% |

### Live-realistic — clean window, **waterfill** (cap 0.50, 5% cash, `core.concentration`), 5 bps
| Level | CAGR | Sharpe | MaxDD | Ann.turn | Avg cash | Cost drag | Settled-cash drag (bps/yr) |
|---|---:|---:|---:|---:|---:|---:|---:|
| top1 | 29.2% | 0.748 | −66.5% | 27.3 | 0.50 | 136 | ~0 (single capped name, no rotation) |
| top3 | 41.3% | 0.850 | −75.3% | 45.5 | 0.10 | 228 | −316 (staging helped) |
| **top5** | 44.1% | 0.941 | −73.9% | 45.8 | 0.09 | 229 | −110 |
| top10 | 37.8% | 0.935 | −56.3% | 41.3 | 0.08 | 207 | +247 |
| full-book | 15.0% | 0.854 | −38.2% | 5.7 | 0.05 | 28 | ~0 |

Settled-cash T+1 drag is **small and mixed-sign (±100–300 bps/yr, noisy)** — for a ~5-name daily-momentum rotation the
one-day settlement lag is a minor effect, not a dominant one. Reported as MODELED.

### Comparability window 2014 → 2026-06-09 (INCLUDES reserved 2025+ holdout — not decision-grade)
EW: top1 39.5%/0.740, top3 70.9%/1.065, **top5 68.6%/1.183**, top10 47.9%/1.084, full 15.5%/0.834. The 2025+ tail lifts
everything (strong momentum regime), which is exactly why the original claim looked like 1.27; on the clean window it doesn't.

---

## 4. Provisional read: does concentration add or destroy alpha? (hedged)

On survivorship-free data with realistic costs, **concentration adds risk-adjusted value only up to an interior optimum
around N≈5, and destroys it beyond that point in either direction.** Sharpe rises from the full book (0.79) to a peak at
top-5 (0.94–0.96), then *falls* as you tighten further — top-3 (0.81–0.85) and top-1 (0.75) are both worse than top-5 and
barely distinguishable from (or below) the diversified full book and SPY (0.81). So the claim's *direction* survives
(top-5 > top-10 > full on Sharpe) but its *magnitude* is roughly halved (corrected top-5-vs-top-10 Sharpe edge ≈ +0.05,
not the claimed +0.16) and it is bought with a near-doubling of drawdown (−69% vs −52%). Critically, **the live sleeves
(Polaris top-4, Orion top-3) sit at N=3–4 — on the losing side of the N≈5 ridge**, in the low-Sharpe / catastrophic-drawdown
regime (top1 −92%, top3 −74%), which is consistent with their observed ~22–24% live lag and negative absolute returns.
My honest reading: concentration is **not** free alpha; it is a Sharpe-neutral-to-slightly-positive bet around a narrow
N≈5 sweet spot that massively amplifies tail risk, and the current live configuration is more concentrated than that sweet
spot. This is provisional — see limitations; Workstream C should treat "top-5 is optimal" as an unstable single-point
estimate, not a law.

---

## 5. Artifact inventory (paths absolute under this dir)

`outputs/research/concentration_thesis_2026-07-14/`
- `A_bias_audit_and_clean_sweep.md` (this), `METHODOLOGY.md`
- `engine_ws_a.py` — the research engine (validated against committed engine)
- `artifacts/bias_audit_table.csv` — §2 full table
- `artifacts/clean_sweep_table.csv` — §3 full grid (both windows, both sizings, settled off + on)
- `artifacts/settled_cash_drag_table.csv` — settled-cash drag per level
- `artifacts/claim_repro.json` — exact reproduction of the 46%/1.27 claim
- `artifacts/daily_<window>_<sizing>_<settle>_<level>.csv` — **25 per-level DAILY return series** (date, gross/net return, turnover, cost, n_names, cash_weight)
- `artifacts/ranking_tables_top20_clean.parquet` — **per-day top-20 momentum ranking** (date, rank, ticker, momentum_score, spy_above_200dma), clean window, 55,360 rows
- `artifacts/regime_spy_trend.csv` — per-day SPY-above-EMA200 regime state
- `data/panel_legacy200_sep.parquet`, `data/panel_largecap_sep.parquet`, `data/signals_largecap_pit.parquet`, `data/membership_largecap.parquet` — cached inputs

---

## 6. Limitations (loud)

1. **Signal divergence from live.** The sweep uses the committed **alpha_lab momentum** signal (the one that produced the
   claim), NOT the full `daily_quant_report` combined-allocator conviction the live engine ranks on. The waterfill uses
   `momentum_score` as the conviction proxy. So this measures concentration of the *momentum* book; the live book differs.
   A true live-signal sweep requires mirroring the allocator pipeline (out of scope here; flagged for a follow-up).
2. **PIT family = current-scale large-cap.** `caerus_large_cap` uses `scalemarketcap` (today's scale, PIT-approximate),
   per `polaris_pit_priced_rebaseline` caveat. A daily-market-cap PIT family would refine membership. The PIT delta blends
   breadth (1,250 vs 200) with survivorship — both are honest corrections but not separable here.
3. **Holdout / 2025+.** The clean window stops at 2024-12-31 by governance convention. The compare window's 2025+ tail is
   shown only to bound the effect and is explicitly NOT decision-grade. No parameter was tuned on it.
4. **Costs are MODELED, not measured.** 5 bps half-spread is an estimate; no live fill data. Not per-name tiered (all
   large-cap). Market impact assumed 0. Settled-cash staging is a weight-space model, not a share-level simulation.
5. **Execution timing.** The committed trade-at-signal-close convention is mildly optimistic; B4 bounds it at ~0.04 Sharpe.
   The true live T+1-open fill is not simulated tick-accurate.
6. **Single realization.** No block-bootstrap / randomized-window robustness on the concentration ranking was run here
   (the alpha_lab_v2 study has `robustness.py` for that); "N≈5 optimal" is one path and should be stress-tested by C.
