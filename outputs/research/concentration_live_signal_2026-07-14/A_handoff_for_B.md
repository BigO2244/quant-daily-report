# A → B Handoff: which panel to use for which analysis

TL;DR: **Use the RECORDED panel. Ignore the reconstruction as a live-signal stand-in — it failed validation (Spearman −0.16, top-5 overlap ≈ 0).**

## Panels delivered
1. `A_recorded_signals_panel.csv` — **THE signal to analyze.** Recorded live combined-allocator conviction (`target_weight`), 1,652 rows, 101 dates. Filter `concentration_status == "pre_concentration_broad"` → **94 broad-book days** (2026-02-03 → 2026-07-07); add `n_sleeves_day >= 4` → **73 full 4-sleeve days** (2026-03-25 → 2026-07-07).
2. `A_reconstructed_conviction_panel.parquet` — momentum-only PIT proxy (1998–2026). **Not the live signal** (see validation). Use only as a labeled negative control / the prior study's signal.
3. `A_recorded_provenance.csv` — per-date source, sleeves, cash, status.
4. `A_validation_recon_vs_recorded.csv` — per-date reconstruction-vs-recorded stats.

## Use this for which analysis
| Analysis B wants | Panel + filter | Caveats |
|---|---|---|
| **Within-book (within-"decile") rank-IC of the live conviction** | `A_recorded_signals_panel.csv`, `pre_concentration_broad` (ideally `n_sleeves_day>=4`) | conviction = `target_weight`; ~16–18 names/day; join forward returns from a price panel. This is the direct replacement for the prior study's momentum-only within-top-20 IC. |
| **Concentration/top-N & MAX_WEIGHT sweeps on the TRUE signal** | same | rank names by `target_weight`, then apply `core.concentration.concentrate_targets` (import read-only) at each N / cap. This is exactly what the live pilot does. |
| **Selection overlap / turnover of the live book** | same, all broad days | note format eras (`core` / 3-sleeve / trend-only / 4-sleeve) via `sleeve` & `n_sleeves_day`. |
| **Long momentum-history context only** | `A_reconstructed_conviction_panel.parquet` | label it "momentum-only, NOT live conviction (Spearman −0.16 vs recorded)". Never present as the live signal. |

## Hard caveats (do not skip)
1. **Forward returns not included.** Join a price panel keyed `(date, ticker)`. SEP research cache (`…/concentration_thesis_2026-07-14/data/panel_largecap_sep.parquet`) covers ≤ 2026-06-09; Jun 10 – Jul 7 needs a price source you supply. Recorded `target_weight` is decided at `asof_date` (T-1 close) for trade date `date` → align returns as `date → date+1` to match live T+1 execution.
2. **94 days ≈ 4.5 months.** Fine for descriptive within-book IC and top-N-vs-rest; **underpowered** for regime-conditional or significance claims (matches prior power analysis: ~81–87 obs for 80% power).
3. **Per-sleeve component scores were never persisted** (only combined `target_weight`, and `sleeve` membership). You can decompose by `sleeve` (primary sleeve = first token of the `sleeve` field) but cannot recover each sleeve's internal score.
4. **`target_weight` is post-exposure-overlay, CASH-removed.** Overlay is a uniform scalar → ranking preserved. On the 3 `pre_conc_riskoff_narrow` days weights are scaled down hard (48–69% cash); use ranks not absolute weights there, or exclude them.
5. **The prior study's momentum-only concentration verdict does NOT transfer.** Confirmed: momentum ≠ live conviction (selection overlap ≈ 0, ordering anti-correlated). Any live-pilot concentration conclusion must be re-derived on the recorded panel.

## Reproduce
`A_build_recorded_panel.py` (panel + provenance), `A_reconstruct_and_validate.py` (reconstruction + validation). VM artifacts staged read-only under `_vm_pull/`.
