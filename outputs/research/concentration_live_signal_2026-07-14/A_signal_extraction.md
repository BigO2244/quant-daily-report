# Workstream A — The TRUE Live Signal: extraction, provenance, reconstruction, validation

Date: 2026-07-14 · Governance: RESEARCH_ONLY / NON_EXECUTIONAL · Read-only over all operational artifacts.
Output dir: `outputs/research/concentration_live_signal_2026-07-14/` (all deliverables prefixed `A_`).

## TL;DR

- **The true live signal is the allocator's COMBINED `target_weight` per name** — each sleeve's normalized weights × its regime-driven allocation share, merged across sleeves — which `core/concentration.py` then ranks and waterfills. Verified against shipped code (citations below).
- **Rung 1 (recorded ground truth) is the win.** We harvested every shipped signal snapshot the pipeline wrote (local + VM). **94 PRE-concentration broad-book days** of the actual combined conviction (2026-02-03 → 2026-07-07), of which **73 are the full 4-sleeve blend**. Median 17 names/day. These are the gold rows for within-decile IC. → `A_recorded_signals_panel.csv`.
- **Concentration went always-on on the VM at 2026-07-08** (empirically; the hardwire commit `15048cd` landed 2026-07-10). Every snapshot ≤ 2026-07-07 is broad-book and therefore PRE-concentration. This is what let Rung 1 recover so much broad-book history.
- **Rung 2 (historical reconstruction) is NOT a valid stand-in — stated loudly.** The only PIT-reconstructable conviction component here is the committed `momentum_score` (the exact signal the prior study used). Validated against recorded truth over 64 overlap dates: **mean Spearman −0.16 (median −0.17); 77% of days negative; 0% reach ≥0.8; momentum top-5 overlap with the live book ≈ 0.00/5.** Momentum-only reconstruction does not reproduce the live combined conviction, in either selection or ordering.
- **Consequence for Workstream B: use Rung 1 (recorded panel). Do NOT treat the momentum-only reconstruction as the live signal.** The prior study's momentum-only concentration sweep does not transfer.

---

## 1. What the true live signal is (verified, with citations)

The live conviction that `core/concentration.py` ranks and concentrates on is the allocator's **combined `target_weight`**:

- `core/concentration.py:14-17` (module docstring): *"Conviction proxy: the combined `target_weight` from the allocator. That weight already encodes each name's composite score times its regime-driven sleeve budget, so ranking by it selects the names the engine is most confident in for the current regime."*
- `core/concentration.py:104` — the ranking/selection: `df.sort_values(["target_weight","ticker"], ascending=[False,True]).head(effective_n)`, then `_capped_waterfill` (line 106) sizes by that same `target_weight` as conviction. So **`target_weight` is literally the ranking key.**
- The combined book is produced by `core/portfolio_alloc.py::PortfolioAllocator`. `_combine_sleeve_weights(sleeve_outputs, realized_sleeve_allocations)` (`core/portfolio_alloc.py:234`) merges each sleeve's normalized `target_weight` × its allocation share; a name held by multiple sleeves sums their contributions (observed in snapshots, e.g. `NFLX` = `sleeve_2, sleeve_trend`).
- Sleeve allocation share = `strength_i / Σ strength` over active sleeves (`core/portfolio_alloc.py:336-370`), where each sleeve's `strength` is set dynamically. Regime→strength mapping: `resolve_regime_strengths` (`daily_quant_report.py:3643-3709`) maps RegimeAllocator weights `{trend,value,quality,mean_reversion,cash}` onto `{sleeve_trend, sleeve_2, sleeve_quality, sleeve_mean_reversion, sleeve_defensive_etf}` and renormalizes. Strengths are then blended with the prior day's strength by a **broker-drift** factor (`_drift_blend`, `daily_quant_report.py:3847-3859`) and, for quality, scaled by a rolling **IC throttle** (`core/ic_throttle.py`, applied `daily_quant_report.py:3271-3290`).
- The shipped pipeline path (`daily_quant_report.py::build_daily_snapshot`, ~4461-4511): it takes `alloc_result.combined_weights`, drops CASH, then **ALWAYS applies `concentrate_targets`** (`daily_quant_report.py:4480-4501`, comment line 4466: *"Concentrated-alpha (ALWAYS ON — concentration is the model)"*). The resulting post-concentration `weights_df` is what gets cached and written to the signal snapshot (`_cache_signal_snapshot_payload(..., df_targets=weights_df)`, `daily_quant_report.py:4607-4614`).

**Sleeves (5):** `sleeve_trend` (momentum/EMA-ADX), `sleeve_2` (value, EDGAR XBRL P/E), `sleeve_quality` (Sharadar/DataStore fundamentals), `sleeve_mean_reversion` (RSI/z-score reversal), `sleeve_defensive_etf` (regime-gated cash route). Confirmed present in the recorded snapshots.

### The critical timing fact
Because the current pipeline writes the **post-concentration** book to the snapshot, a snapshot written *today* would be a 3–7-name concentrated book. But concentration only became always-on recently:
- `c0adf2b` (2026-07-08): "Concentrated-alpha construction (flag-gated, **off by default**)".
- `15048cd` (2026-07-10): "**Hardwire** concentration with regime-adaptive top-N; fail loud".
- Empirically (from the snapshots themselves): every VM bundle **≤ 2026-07-07 is broad-book** (16–33 names, weights sum ≈ 1.0); **2026-07-08 onward is concentrated** (5–7 names, 5% cash floor). So the flag was switched ON on the VM at 07-08, two days before the hardwire.

**Therefore every recorded snapshot dated 2026-02-03 … 2026-07-07 is the PRE-concentration combined-allocator book — the exact broad-book conviction that concentration ranks, before it was concentrated.** This is the gold data the prior study lacked.

---

## 2. Rung 1 — Recorded ground truth (MANDATORY, delivered)

Builder: `A_build_recorded_panel.py`. Output: `A_recorded_signals_panel.csv` (1,652 rows, 101 dates) + `A_recorded_provenance.csv`.

### Sources harvested (all shipped artifacts the pipeline wrote)
| Source | What | Coverage |
|---|---|---|
| `outputs/precompute/<date>/signals.json` (local) | authoritative bundle, has `meta.asof_date` | 2026-03-24 only (local) |
| VM `/home/brettolson/quant-daily-report/outputs/precompute/<date>/signals.json` | **daily series** | 2026-03-22 → 2026-07-14 (83 dates) |
| VM `signals/<date>.json` | daily snapshots incl. Feb | 2026-02-03 → 2026-07-14 (100 dates) |
| local `signals/*.json` | subset of VM | 24 dates |
| `signals_store/<date>.parquet` (local+VM) | per-name `final_target_weight`, `sleeve_source`; **per-sleeve component scores are all NULL** | 28 dates, not needed (weights identical to JSON) |

VM artifacts pulled read-only via `tar cf - … | tar xf -` streaming (no writes on VM) into `_vm_pull/`. Source priority when a date exists in multiple: local precompute > VM precompute > VM signals > local signals. Verified precompute vs `signals/` agree exactly on 2026-03-24 (same 31 names, max weight diff 0.0).

### The panel
Each row: `date, ticker, target_weight, raw_score, sleeve, n_names_day, n_sleeves_day, cash_target_weight, concentration_status, source, asof_date, generated_at, breaker_mode, exposure_multiplier, vix, regime`.

`target_weight` is the combined-allocator conviction **after** the (identity-when-breaker-off) exposure overlay and with CASH removed. The exposure overlay is a uniform scalar, so it **preserves the conviction ranking** — the field is a faithful ranking key for rank-IC and top-N overlap.

### Provenance summary (`concentration_status`)
| status | dates | window | meaning |
|---|---|---|---|
| **pre_concentration_broad** | **94** | 2026-02-03 → 2026-07-07 | broad-book combined conviction — **GOLD** |
| pre_conc_riskoff_narrow | 3 | 2026-06-05, 06-10, 06-17 | pre-concentration but cash-heavy/narrow (risk-off routed most to cash; 4–8 names, 48–69% cash). Usable but small books. |
| post_concentration | 5 | 2026-07-08 → 2026-07-14 | already concentrated (3–7 names). NOT usable for within-decile IC. |

Format evolution within the 94 broad days (all recoverable, tag via `n_sleeves_day`/`sleeve`):
- 2026-02-03…05: single `core` sleeve, 7 names equal-weight (legacy format).
- 2026-02-09…19: 3-sleeve (`sleeve_trend`, `sleeve_2`, `charlie_munger`), conviction-weighted.
- 2026-02-21…03-22: `sleeve_trend`-only, 10 names (often equal-weight).
- **2026-03-25 … 07-07: full 4-sleeve blend (`sleeve_trend`, `sleeve_2`, `sleeve_quality`, `sleeve_mean_reversion`) — 73 dates. This is the truest picture of the live combined conviction.**

**Usable PRE-concentration (broad-book) conviction history: 94 days; 73 of them the full 4-sleeve blend.** `raw_score` (== per-name pre-normalization contribution) is present only from 2026-04-10 onward; before that only `target_weight` is recorded. Per-sleeve component scores (momentum/value/quality sub-scores) were **never persisted** (the `signals_store` parquet has them as columns but all-NULL).

---

## 3. Rung 2 — Historical reconstruction (ATTEMPTED; honest limits)

Builder: `A_reconstruct_and_validate.py`. Output: `A_reconstructed_conviction_panel.parquet`.

### What is and isn't reconstructable (confronting the mission's stated realities)
Reproducing the live combined conviction historically requires running all 5 shipped sleeve builders + the RegimeAllocator + strength-blending on PIT data. Blockers, in order of severity:

1. **(a) Value & quality sleeves need PIT fundamentals.** `sleeve_2` selects on EDGAR XBRL trailing P/E (`sleeves/sleeve_2/selection.py`, `compute_pe_ratios`); `sleeve_quality` reads `DataStore`/`data/fundamental/` (`sleeves/sleeve_quality/selection.py:33,71`). Neither is available as a survivorship-free PIT panel in the repo's research caches (the 2026-06-10 Sharadar value assessment flags fundamentals coverage as the gap). **These two sleeves carry ~48% of the recorded book by weight (25.6% value + 22.2% quality in the 4-sleeve era) — see §4 — so omitting them omits about half the signal.**
2. **Even the "price-only" sleeves need OHLCV, not close-only.** `sleeve_trend` uses EMA-20/50/200 + ADX + volume filters; `sleeve_mean_reversion` uses RSI-14 + z-scores. The prior study's PIT panel (`…/data/panel_largecap_sep.parquet`, `signals_largecap_pit.parquet`) is **close-only** (`momentum_score = 0.5·r12_1+0.3·r6_1+0.2·r3`). So even `sleeve_trend`'s conviction cannot be reproduced from it — the trend sleeve's composite ≠ the prior study's `momentum_score`.
3. **(b) Strengths depend on non-reconstructable state.** Sleeve strength = regime target (reconstructable from VIX) **blended with prior-day strength via broker drift** (`_drift_blend`) **and IC throttle** for quality (`core/ic_throttle.py`, rolling live IC). Broker-drift and IC-throttle are functions of live account/monitor history and are **not PIT-reconstructable — frozen at neutral** per the mission. Their divergence is folded into the validation gap measured below (they are second-order relative to blockers 1–2).

### What we actually delivered as the reconstruction
Given the above, the only conviction component honestly reconstructable PIT is the committed **`momentum_score`** (close-only, survivorship-free, the exact signal the prior study ran). `A_reconstructed_conviction_panel.parquet` = per-day per-name `momentum_score` as conviction over the PIT-eligible, signal-ready large-cap universe, **1998-12-31 → 2026-06-09** (6,901 dates, 7.4M rows), columns `date, ticker, conviction_momentum_only, sleeve_trend`. **It is explicitly a momentum-only proxy, NOT the combined conviction**, and its fidelity is measured next. (We did not fabricate value/quality/mean-reversion or regime-strength blending we could not run faithfully.)

---

## 4. VALIDATION (the crux) — reconstruction vs recorded truth

Output: `A_validation_recon_vs_recorded.csv`. Overlap = 64 dates where a recorded broad-book snapshot and the PIT momentum panel both exist (2026-02-09 → 2026-06-09; ticker coverage of recorded names in the PIT panel is 100%).

| Metric | Result | Threshold | Verdict |
|---|---|---|---|
| **Spearman(recorded `target_weight`, `momentum_score`) within the recorded book** | mean **−0.162**, median −0.168, std 0.26, range [−0.67, +0.60] | ≥0.8 to be a valid stand-in | **FAIL** |
| frac of days Spearman ≥0.8 / ≥0.5 / ≤0 | 0.00 / 0.02 / **0.77** | — | anti-correlated most days |
| **Momentum top-5 vs recorded top-5** (from full momentum universe) | **0.00 / 5** | — | live book never holds momentum's top-5 |
| Momentum top-10 vs recorded top-10 | 0.05 / 10 | — | essentially disjoint selection |
| Within-book momentum re-rank top-5 | 1.25 / 5 | — | poor ordering agreement |
| Weight MAE (recorded vs momentum-share, common names) | 0.057 | — | large vs ~0.06 mean weight |

**The reconstruction is NOT a valid stand-in for the live signal — loudly.** It fails on both axes: it selects a different book (top-5 overlap ≈ 0) and orders that book differently (negative rank correlation).

### Why (mechanism, so B trusts the negative result)
- **Sleeve composition of the recorded book (4-sleeve era, share of total weight):** `sleeve_trend` 40.6%, `sleeve_2` (value) 25.6%, `sleeve_quality` 22.2%, `sleeve_mean_reversion` 11.7%. **Only ~41% is momentum-driven; ~48% is value+quality (which momentum can't see) and the mean-reversion sleeve is by construction anti-momentum.**
- Concrete (2026-06-09): the two top-weight names ELV & FTNT sit at momentum percentiles 0.43/0.57, while the highest-momentum names MU (0.994) and STX (0.99) get the *smallest* weights (0.026, 0.059). The equal-cap waterfill + multi-sleeve blend flatten and invert momentum ordering.
- The live book *does* skew above-median momentum (median momentum percentile ≈ 0.73), consistent with the prior study's "top-decile-vs-rest works" — but the **fine ordering and exact selection**, which concentration depends on, are uncorrelated-to-negative with momentum.
- Even on `sleeve_trend`-only days, Spearman vs `momentum_score` averages −0.14: the shipped trend sleeve (EMA/ADX/inverse-vol) ≠ the prior study's `momentum_score`.

---

## 5. What Workstream B can rely on (honest bottom line)

- **Rely on Rung 1: `A_recorded_signals_panel.csv`, 94 pre-concentration broad-book days (73 full 4-sleeve), 2026-02-03 → 2026-07-07.** This is the actual live combined conviction, point-in-time, survivorship-free by construction (it's what the live system recorded). Median 17 names/day → adequate for within-book (within-"decile") rank-IC and top-N-vs-rest tests over ~3–4 months of real signals.
- **Do NOT use the momentum-only reconstruction as the live signal.** `A_reconstructed_conviction_panel.parquet` gives long PIT history (1998–2026) but validation proves it does not represent the live combined conviction (Spearman −0.16; selection overlap ≈ 0). It is retained only as (i) the prior study's signal, now with its fidelity quantified, and (ii) a labeled negative control.
- **The prior study's momentum-only concentration verdicts do not automatically transfer to the live signal.** The memo's caveat #6 is now confirmed with numbers: momentum ≠ live conviction. Any concentration/IC conclusion B draws for the live pilot must come from the 94 recorded days, not from the long momentum backtest.
- Length limits to state plainly: 94 days is ~4.5 months — enough for descriptive within-book IC and top-N overlap, **underpowered** for regime-conditional or forward-return-significance claims (consistent with the prior study's power analysis). Forward returns for these names must be joined from a price panel (SEP/yfinance) covering Feb–Jul 2026; the SEP research cache extends to 2026-06-09, so the last ~1 month (Jun 10 – Jul 7) needs a price source B must supply.

## Measured / Modeled / Assumed
- **MEASURED:** all recorded `target_weight` values (verbatim from shipped snapshots); the concentration flip date (from the snapshots + git); all validation statistics (real joins on real recorded data); sleeve composition.
- **MODELED:** the momentum-only reconstruction (`momentum_score` as conviction) — and it is shown to be a poor model of the live signal.
- **ASSUMED:** exposure overlay preserves ranking (true — uniform scalar); broker-drift & IC-throttle frozen at neutral for the (undelivered) full reconstruction — moot, since the reconstruction failed validation on the first-order sleeves alone.
