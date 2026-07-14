# Workstream A — METHODOLOGY

Date: 2026-07-14 · Governance: RESEARCH_ONLY / NON_EXECUTIONAL · Branch: release/pre-arm-fixes-2026-07-13
Local-only (Mac Studio). No VM/execution/cron/registry/model changes. Holdout (2025+) treated as reserved; see windows.

## Engine
- `outputs/research/concentration_thesis_2026-07-14/engine_ws_a.py`. A research backtester that **reuses**:
  - `research.alpha_lab_v1.signals.build_alpha_lab_signal_frame` — the committed Polaris momentum signal
    (`momentum_score = 0.5·r12_1 + 0.3·r6_1 + 0.2·r3`, close-only; r12_1 = close.shift(21)/close.shift(252)−1, etc.).
  - `alpha_stack.research.metrics.summarise_performance` — the committed metric set.
  - `core.concentration._capped_waterfill` — the **LIVE** top-N waterfill (imported read-only, never modified).
- **Validation:** on the committed flow panel, unconstrained EW, 10 bps, `engine_ws_a` reproduces
  `research.alpha_lab_v2.engine` exactly (top5 CAGR 0.3489 / Sharpe 1.105; top10 0.2499 / 0.969 — the latter also
  equals the 2026-06-10 survivorship audit "A_current_universe" figure). So the harness is faithful.

## Rebalance timing / return convention
- `returns_matrix = close.pivot().pct_change().shift(-1)`. At date *t*: rank on `momentum_score` computed through
  close[t]; hold weights; earn `close[t+1]/close[t]−1`. i.e. **decision at t's close, return t→t+1** (trade-at-signal-close).
  This is the committed convention. The realistic live pipeline trades T+1 open, so this convention is mildly optimistic;
  quantified via `exec_lag_days=1` (B4).
- Daily rebalance for all sweep levels (matches the claim). rf = **0.0** (committed `sharpe_ratio` default), 252 periods/yr.
- Tie-break in ranking: `momentum_score` desc, then `ticker` asc (deterministic).

## Universes (the only thing that changes across bias legs)
- **Legacy / static-200:** `data/universe.csv` (200 current tickers; leading blank line stripped as prior art does).
  Priced two ways: (a) **yfinance** `outputs/research/flow_detection_v1/price_panel.parquet` (the panel that produced the
  original claim); (b) **SEP** `closeadj` from `data/research_cache/sharadar_sep/`. 198/200 priced on SEP (BK, GOOG, MMC absent).
- **PIT / survivorship-free:** `caerus_large_cap` membership (`data/pit_universe/membership_universe_large_cap.csv`,
  1,600 securities incl. 354 delisted/removed). Per-date eligibility mask: a ticker is eligible on date *d* iff
  `membership_start ≤ d ≤ membership_end` (end open ⇒ active). ~1,150–1,260 signal-ready names per date. Priced from SEP
  `closeadj` (`data/research_cache/sharadar_sep/`, includes delisted names e.g. ATVI, TWTR). This reuses the FR-068
  Phase-3 machinery (`research/run_polaris_pit_priced_rebaseline.py`). Cached panels in `data/`.
- SPY comes from `alpha_stack_cache/prices/_matrix_prices_2007_2026.parquet` for the regime input (identical in all legs).

## Windows
- **claim window** 2014-01-02 → 2026-04-15 (reproduces the published claim; n_years≈12.25). **Includes** the 2025+ period.
- **clean window** 2014-01-02 → 2024-12-31 (holdout-excluded; the FR-068 decision-grade convention). **Primary for the thesis.**
- **compare window** 2014-01-02 → 2026-06-09 (SEP cache extent). Reported for comparability but its 2025+ tail is the
  **reserved holdout / live-shadow period** — NOT decision-grade, shown only to bound the effect. No tuning was done on it.
- Warm-up: signals need ≥252 trading days; SEP history extends back to the 1990s so 2014-01-02 is fully warmed.

## Cost model (MODELED / ASSUMED — not measured)
- **Commissions:** 0 bps (Alpaca fractional). MEASURED-policy (Alpaca fee schedule).
- **Slippage:** half-spread per side, **5 bps** central estimate applied to `Σ|Δw|` (turnover already sums both sides, so
  cost = turnover × half_spread). MODELED: liquid large-cap Alpaca effective half-spreads run ~1–5 bps; 5 bps is a
  conservative central value. (Bias-audit legs use the committed **10 bps** to isolate universe/window/price effects from
  cost changes; the clean sweep uses 5 bps.) Not per-name tiered — all names are large-cap; documented limitation.
- **Market impact:** ≈0 (fractional market orders ~$30–300). ASSUMED.
- **cost_drag_bps_yr** reported per level = mean daily cost × 252 × 1e4.

## Live constraints (clean sweep, "waterfill" sizing)
- **Long-only**, top-N by `momentum_score`. **Conviction-weighted waterfill** via `core.concentration._capped_waterfill`
  with **MAX_WEIGHT = 0.50** cap, **5% cash floor** (invested budget = 0.95), residual → cash. Mirrors
  `core/concentration.py` exactly for sizing. **Divergence (documented):** live conviction = the combined-allocator
  `target_weight` (composite score × regime sleeve budget); here conviction = `momentum_score` (shifted positive),
  because we mirror the alpha_lab pipeline that produced the 46%/1.27 claim, not the full `daily_quant_report` allocator.
  The "ew" sizing variant (1/N, no cap, no cash) is also reported for direct comparability to the claim's construction.
- **T+1 settled-cash staging** (`settled_cash=True`): cash account; buys on day *t* funded only by cash settled from
  sales on day *t−1* (initial deposit seeded as settled). Sells execute immediately; unfunded buys defer one day.
  Isolates the staged-deployment drag on daily rotation.

## Pre-registered sweep grid (all reported, no selection)
- Levels N ∈ {1, 3, 5, 10, full-book}. Sizings {ew, waterfill}. Settled-cash {off, on}. Windows {clean, compare}.

## Measured / Modeled / Assumed
- MEASURED: all CAGR/Sharpe/MDD/turnover/hit numbers (real priced runs on real price data), universe membership deltas.
- MODELED: slippage (5 bps), settled-cash staging mechanics, `momentum_score`-as-conviction proxy.
- ASSUMED: market impact ≈ 0; Alpaca commission = 0; SEP `closeadj` as the true total-return price.
