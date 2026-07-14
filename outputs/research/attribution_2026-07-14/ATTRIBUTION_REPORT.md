# Return Attribution Report — lane=paper — TARGET-BOOK SHADOW

Window: 2026-01-15 -> 2026-07-13  |  109 trading days  |  generated 2026-07-14

**Total return of the TARGET-BOOK SHADOW (geometric, Carino-linked base): +11.983%**  (mean daily +0.113%, daily vol 1.337%)

**This is NOT realized cash P&L.** It is the return of the recorded target-weight book — rebalanced to target on signal days, drifted buy-and-hold between them, T+1 application, cash at 0% — with slippage, partial fills, rejects, and the options overlay all EXCLUDED.
Reality check over the only available overlap (25 days vs the live-overlay NAV): tracking error 58 bps/day = **0.78x the real book's own daily vol**, and the real book ran -1.814% behind the shadow over those days — the shadow headline above likely OVERSTATES realized return.

Contributions are Carino(1999)-linked so every decomposition sums EXACTLY to the total shadow return.

## 0. Interpretation constraints (verbatim from the 2026-07-14 adversarial review; binding on any reading of the tables below)
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

## 1. Validation: reconstruction vs recorded NAV
Overlap with `outputs/perf/live_overlay_nav_series.csv` (2026-03-04 -> 2026-04-08, n=25):
- correlation of daily returns: **0.799** (corr^2 = 0.64: the shadow explains only ~64% of the real book's daily-return variance)
- mean daily diff: **+7.59 bps**
- RMS tracking error: **58.14 bps/day** = **0.78x** the real book's own daily vol (74.9 bps) — the error is nearly as large as the thing being tracked
- shadow daily vol 97.7 bps vs real book 74.9 bps: materially different risk profile (the real book is damped by the options overlay / exposure scaling the shadow ignores)
- cumulative over overlap: shadow -1.031% vs recorded NAV -2.845%

This is a loose directional/shape check, NOT a magnitude validation. Every headline number in this report is a target-book shadow figure.

## 2. Per-NAME attribution (Carino-linked contribution to the shadow total)

Top 10 contributors:

| ticker | contribution |
|---|---|
| FTNT | +5.533% |
| STX | +4.524% |
| QCOM | +3.981% |
| DELL | +2.079% |
| ELV | +1.899% |
| PWR | +1.149% |
| EQIX | +0.985% |
| BK | +0.916% |
| UNH | +0.902% |
| AMD | +0.821% |

Bottom 10 contributors:

| ticker | contribution |
|---|---|
| GLW | -1.731% |
| CRWD | -1.503% |
| ABBV | -1.298% |
| CMCSA | -1.274% |
| NFLX | -1.189% |
| FCX | -1.025% |
| FDX | -0.971% |
| LMT | -0.860% |
| MAR | -0.845% |
| GS | -0.785% |

## 3. Per-SLEEVE attribution (convention-dependent for multi-tagged names)
The reconciling cut splits comma-tagged multi-sleeve names EQUALLY across their listed sleeves — an arbitrary convention. The first-listed-rule column shows the sensitivity: sleeve numbers move at the ~1.5pp level between rules (e.g. quality +13.33% -> +14.89%, trend -2.66% -> -4.38% on the full window) while signs are stable.

| sleeve | contribution (equal split, reconciling) | contribution (first-listed rule, sensitivity) |
|---|---|---|
| sleeve_quality | +13.333% | +14.886% |
| sleeve_mean_reversion | +1.891% | +1.499% |
| sleeve_2 | +1.128% | +1.692% |
| charlie_munger | -0.578% | -0.578% |
| core | -1.132% | -1.132% |
| sleeve_trend | -2.659% | -4.385% |

## 4. Per-REGIME attribution (days partitioned; no overlap) — EVENT-DRIVEN, not structural
Regime = canonical VIX thresholds (LOW<20, ELEVATED 20-30, HIGH 30-40, CRISIS>=40; the strategy's own config) classified from the cached ^VIX close. The ex-2-worst-days column (diagnostic, non-reconciling) shows how much each bucket rests on its two worst tape days: read bucket differences as event outcomes, NOT a robust regime edge — on the full window ELEVATED flips from -6.83% to positive without its two worst days.

| regime | n_days | contribution | ex 2 worst days (diagnostic) |
|---|---|---|---|
| LOW | 73 | +20.349% | +24.869% |
| HIGH | 2 | -1.535% | +0.000% |
| ELEVATED | 34 | -6.831% | +1.020% |

## 5. Per-FACTOR attribution (time-series OLS, liquid-ETF proxies)
Proxies: market=SPY, momentum=MTUM-SPY, size=IWM-SPY, value=IVE-IVW, quality=QUAL-SPY, lowvol=USMV-SPY.

N=109 days, R^2=0.593, HAC(Newey-West) lag=4. Intercept is labeled *unexplained*, not alpha.

| term | beta | HAC se | HAC t |
|---|---|---|---|
| unexplained(intercept) | -0.0000 | 0.0008 | -0.02 |
| market | +1.2002 | 0.1649 | +7.28 |
| momentum | +0.3285 | 0.1029 | +3.19 |
| size | +0.1615 | 0.1647 | +0.98 |
| value | +0.3248 | 0.2063 | +1.57 |
| quality | -0.3971 | 0.3801 | -1.04 |
| lowvol | +0.3512 | 0.2714 | +1.29 |

Carino-linked factor contributions to the shadow total:

| term | contribution |
|---|---|
| unexplained(intercept) | -0.177% |
| market | +11.204% |
| momentum | +4.542% |
| size | +0.497% |
| value | -1.881% |
| quality | +0.085% |
| lowvol | -1.873% |
| residual | -0.414% |

Unexplained (intercept + residual) linked contribution: -0.591%  (= -4.9% of the shadow total).

**Do NOT read R^2 as 'share of return explained by beta' or the small unexplained share as 'near-zero alpha'.** The adversarial placebo test showed random same-universe 17-name books score a HIGHER median R^2 (0.738; 96% exceed this book's 0.593) — R^2 here is breadth-mechanical, and this book carries MORE idiosyncratic variance than a random basket, not less. The intercept has no statistical power at this N (CI ~ +/-12% annualized on the full window): alpha is UNMEASURABLE, not near zero. The one factor claim with real content is the mildly elevated market beta (~1.2, placebo p95); the momentum load (~0.33, HAC t>3) is plausible.

## 6. Reconciliation table (hard requirement)
Name/sleeve/regime residuals must be ~0 by construction; a non-refused factor row sums to its regression-day total because intercept+residual are included. REFUSED = regression not run (N<MIN_FACTOR_OBS); a refused row is not a pass.

| decomposition | sum | total | residual (bps) | status |
|---|---|---|---|---|
| name | +11.983% | +11.983% | +0.0000 | PASS |
| sleeve | +11.983% | +11.983% | +0.0000 | PASS |
| regime | +11.983% | +11.983% | +0.0000 | PASS |
| factor | +11.983% | +11.983% | +0.0000 | PASS |

Largest non-refused reconciliation residual: 0.0000 bps (PASS, <1bp).

## 7. Gaps & caveats
See BUILD_NOTES.md for the full data-inventory and gaps section, and section 0 above for the binding interpretation constraints. Input-panel defaults are a frozen 2026-07-14 snapshot; re-point --signals-panel/--price-panel/--factor-prices for fresh data.