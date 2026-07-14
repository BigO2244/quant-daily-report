# Attribution measurement layer — BUILD NOTES

Mission: build the minimal-but-honest, REPEATABLE measurement layer answering
"what actually drives our P&L?" — decomposed by SLEEVE, NAME, REGIME, FACTOR, with
pieces that provably sum to total realized return. Research/infra only; read-only on
all operational artifacts.

Generated 2026-07-14. Engine: `attribution_report.py`. Factor cache builder:
`fetch_factor_prices.py`.

**Revision after adversarial review (`D_adversarial_review.md`):** the verifier
confirmed the arithmetic to machine precision and byte-identical repeatability, but
broke the interpretation layer and found two mechanical gaps. This build now:
(a) labels every headline as the **TARGET-BOOK SHADOW** with the validation gap
(TE = 0.78x the real book's daily vol; real book ran 1.8% behind the shadow over the
25-day overlap) displayed next to the headline; (b) carries the verifier's verbatim
caveat block as report **section 0 (binding)** — in particular the "~95% beta /
near-zero alpha" reading is STRUCK: the placebo test (2000 random 17-name books from
the same universe: median R²=0.738, 96.4% beat this book's 0.593; full-universe EW
R²=0.932) shows R² is breadth-driven and this book is if anything *less*
factor-explained than random, and the intercept CI is ≈±12% annualized so alpha is
unmeasurable at N=109 (market β=1.20, at the placebo p95, is the one factor claim
with content); (c) downgrades the regime split to **event-driven** with a per-bucket
ex-2-worst-days diagnostic column (ELEVATED −6.83% flips positive without its two
worst days); (d) reports the first-listed-sleeve alternative alongside the equal
split (quality +13.33→+14.89, trend −2.66→−4.38; sign-stable); (e) **enforces** the
N<60 factor refusal in the data artifacts — factor CSVs stamped `refused=True` with
no betas/contributions, reconciliation factor row marked `REFUSED`, not PASS;
(f) adds `--signals-panel/--price-panel/--factor-prices` CLI flags (defaults = the
frozen snapshot; see §4).

---

## 1. Data inventory (what exists, windows, quality)

| source | what | window | quality / use |
|---|---|---|---|
| `outputs/research/concentration_live_signal_2026-07-14/A_recorded_signals_panel.csv` | date/ticker/**target_weight**/sleeve/regime/vix, 137 names | 2026-02-03 → 2026-07-14, **101 signal days** | PRIMARY weight source (recorded live-signal book). regime+vix populated for 80 of 101 days. REUSED from the prior concentration study. |
| `outputs/research/concentration_live_signal_2026-07-14/B_price_panel.parquet` | long (date,ticker,close), yfinance auto-adjust total-return proxy | 2026-01-15 → 2026-07-13, 122 days, 137 names (missing MMC only) | PRIMARY name-return source. Cross-validated vs SEP cache in the prior study. REUSED. |
| `outputs/research/attribution_2026-07-14/factor_prices.parquet` | SPY/MTUM/IWM/IVE/IVW/QUAL/USMV + ^VIX, yfinance auto-adjust | 2026-01-15 → 2026-07-13 | BUILT here for factor regression + regime. Same source/method as name panel. |
| `outputs/perf/live_overlay_nav_series.csv` | real daily NAV + `return_1d` (live-overlay lane) | 2026-03-03 → 2026-04-08, **~25 return days** | ONLY real daily-NAV-with-returns series available. Used for VALIDATION (read-only). Includes options overlay + real fills, so it is not identical to the target-weight shadow. |
| `outputs/paper_state/ledger.csv` + `trades.csv` | paper-lane holdings/fills | **2026-02-24 → 2026-02-27 only** (57/28 rows) | STALE. Too short for a daily NAV series. Not usable as the long paper history. |
| `outputs/execution_history.csv` | daily buys/sells/NAV | 7 rows, mostly test rows (`test_run_1`, `2099-01-01`) | Fragmentary. Not a usable daily NAV series. |
| `outputs/portfolio_history/nav.csv` | equity/return_1d | 27 rows, sparse (Mar 3 → Apr 8, gaps) | Same underlying as live_overlay; sparse. |
| `outputs/live_pilot/` | plans only (`live_pilot_plan_2026-03-24`) | — | No `live_trade_ledger.jsonl` present locally; live pilot has no realized-fill history to attribute (armed; 07-09/07-10 rejects only). |
| `outputs/research/concentration_thesis_2026-07-14/artifacts/regime_spy_trend.csv` | SPY>200dma trend flag | 2007 + 2026-03-04→ | Regime fallback source (last resort). |

**Bottom line on holdings:** there is NO clean, complete daily NAV/holdings series for
a "paper lane" over the full history. The reconstructable weight series is the recorded
**target book** (101 signal days). The engine therefore attributes the *strategy book as
recorded* — target weights, rebalanced to target on each signal day and drifted
buy-and-hold between signals — which is the honest, reproducible foundation. It is
validated against the one real NAV series that exists (live-overlay, 25 days).

---

## 2. Methodology (how the numbers are built)

**Position-day panel.** For each trading day the beginning-of-day (BOD) weight of each
name is: the most recent recorded target weight (applied **T+1** — a signal on day D is
effective from D+1, so D's own close never sets the weights that earn D's return),
carried forward and **drifted** by realised returns between rebalances (buy-and-hold),
renormalised each day including cash. Cash weight = 1 − Σ(name weights), earns 0%.
Validated: Σ(name BOD weights) ∈ [0.31, 1.00], mean 0.98 (remainder = cash);
Σ_i c_i(t) = r_p(t) to 7e-18; only 1 of 1694 name-days lacks a price (treated flat).

**Daily portfolio return** r_p(t) = Σ_i w_i(BOD,t)·r_i(t), r_i = close/close_prev − 1.

**Multi-period linking = Carino (1999)** logarithmic smoothing. With
R = Π(1+r_p)−1, k = ln(1+R)/R, k_t = ln(1+r_p_t)/r_p_t, the scaled contribution
C_i = Σ_t (k_t/k)·c_i(t) satisfies Σ_i C_i = R **exactly**. Plain daily sums do not
compound; Carino is the single linking method used for every cut.

**NAME:** C_i as above. **SLEEVE:** name contributions grouped by that day's sleeve
label; multi-sleeve names (comma-listed tag, e.g. `sleeve_quality, sleeve_trend`) split
their daily contribution **equally** across the listed sleeves. **REGIME:** days
partitioned by a single consistent VIX taxonomy (LOW<20, ELEVATED 20–30, HIGH 30–40,
CRISIS≥40 — the strategy's own thresholds from `sleeves/sleeve_trend/config.py`),
classified from the cached ^VIX level; regime return = Carino-scaled sum of daily r_p in
each bucket (no overlap by construction). **FACTOR:** OLS of daily r_p on the
pre-registered proxy set with intercept; per-day contribution β_f·f(t); intercept =
"unexplained" (not alpha); residual reported. HAC (Newey-West) t-stats. Carino-linked
factor + intercept + residual sum to R exactly. If N<60 the refusal is ENFORCED in the
artifacts (refused=True CSVs with no betas; reconciliation row REFUSED), not just noted
in the text. The equal-split SLEEVE convention's alternative (first-listed rule) is
emitted as a sensitivity column; the REGIME cut carries an ex-2-worst-days diagnostic.

**Factor proxies (documented limits):** market=SPY, momentum=MTUM−SPY, size=IWM−SPY,
value=IVE−IVW, quality=QUAL−SPY, lowvol=USMV−SPY. These are liquid-ETF long/short
spreads, NOT academic (Fama-French/AQR) factors: MTUM/QUAL/USMV are cap-weighted
single-ETF proxies with their own construction rules and overlap, IWM−SPY conflates size
with small-cap sector tilt, IVE−IVW is large-cap value/growth only. Betas are descriptive
of co-movement with tradable proxies, not clean factor loadings. Pre-registered set of 6;
no stepwise selection.

**Reconciliation.** name/sleeve/regime reconcile to the full-window R; factor reconciles
to the geometric total over its own regression days (identical here, all 109 days had
factor data after dropping the phantom Memorial-Day ^VIX row). All four residuals =
0.0000 bps.

---

## 3. Gaps & what they blind (mandatory)

1. **No realized-fill P&L series to attribute.** The paper ledger is stale (Feb 24–27)
   and the live pilot has no fill ledger locally. Attribution is of the recorded
   **target book** (shadow), not booked cash P&L. Blinds: real slippage, partial fills,
   rejects, and the options overlay are NOT in the decomposition. The 25-day validation
   vs the live-overlay NAV quantifies the gap: corr 0.80, RMS tracking error ~58 bps/day,
   cumulative shadow −1.03% vs real NAV −2.85% over the overlap — i.e. the real book
   underperformed the target-weight shadow (costs + overlay drag), same direction.
2. **Per-sleeve component scores never persisted.** Only the final sleeve *label* per
   name-day exists, not the per-sleeve conviction scores. Blinds: within-sleeve
   score→return IC and the *why* behind a name's sleeve contribution.
3. **Conviction-persistence gap (from the concentration studies).** Pre-concentration
   conviction recording stopped 2026-07-08; the last days (07-08→07-14) are the 5–7 name
   concentrated book. Blinds: clean pre/post conviction comparison inside this window.
4. **Live lane has no meaningful history.** 11 trading days from the live-pilot start.
   Factor regression is correctly REFUSED (N<60). Name/sleeve/regime reconcile but N is
   too small for inference — reported for completeness only.
5. **regime/vix missing for ~21 early days** in the recorded panel; the engine
   backfills regime from the cached ^VIX level (canonical thresholds) so the regime cut
   spans all 109 days on one taxonomy. VIX level is the daily close (^VIX), a
   point-in-time proxy for the regime the strategy would have seen.
6. **Timing / intraday-vs-close convention.** Returns are close-to-close; signals are
   applied T+1. The real lane executes intraday near the open/rebalance, so a fraction
   of the first-day move is a timing artifact, not captured here.
7. **Prices are yfinance auto-adjust total-return proxy** (splits+divs), cross-checked
   vs the SEP cache in the prior study (daily-return corr >0.99 for most names). MMC has
   no price and is dropped from any day it appears.

---

## 4. Script usage (repeatable; cron-safe)

```
# 1. refresh factor + VIX cache (yfinance; widen window in the file if history grows)
.venv/bin/python outputs/research/attribution_2026-07-14/fetch_factor_prices.py

# 2. full-history paper (recorded-book) attribution -> ATTRIBUTION_REPORT.md + CSVs
.venv/bin/python outputs/research/attribution_2026-07-14/attribution_report.py --lane paper

# windowed / live-lane variants
.venv/bin/python outputs/research/attribution_2026-07-14/attribution_report.py \
    --lane paper --start 2026-05-01 --end 2026-07-13
.venv/bin/python outputs/research/attribution_2026-07-14/attribution_report.py --lane live
```

Defaults: `--start/--end` = full price-panel window; `--lane paper`;
`--out outputs/research/attribution_2026-07-14`. No hardcoded dates in the logic — a
future cron can call it unchanged. Outputs per run (tagged `<lane>_<start>_<end>`):
`name_attribution`, `sleeve_attribution` (equal-split + first-listed columns),
`regime_attribution` (with ex-2-worst diagnostic), `factor_betas` / `factor_attribution`
(with `refused`/`refusal_reason` columns), `reconciliation` (with `status`
PASS/INVESTIGATE/REFUSED), `daily_portfolio_return`, `contrib_panel` CSVs, plus
`ATTRIBUTION_REPORT.md`.

**Input paths (frozen-snapshot defaults):** the defaults of `--signals-panel`,
`--price-panel`, and `--factor-prices` point at the dated 2026-07-14 research dirs —
a FROZEN snapshot. A re-run tomorrow with defaults deterministically reprocesses the
SAME data (byte-identical), it does not self-update. To wire into the live pipeline,
pass the flags pointing at the operational recorded-signals + price artifacts (same
schemas: signals CSV with date/ticker/target_weight/sleeve/regime/vix; long
(date,ticker,close) parquets) — no code edits needed. This pass does NOT deploy.

Verified behavior (adversarial review + post-fix re-run): byte-identical regeneration
on both lanes; subwindow runs carry drift state correctly (daily r_p byte-identical to
the full run on overlapping dates); report generation is offline-safe (only
`fetch_factor_prices.py` touches the network); explicit-flag run byte-identical to the
default run.
