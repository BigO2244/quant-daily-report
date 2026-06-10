# Polaris PIT Rebaseline — FR-068 Phase 3

Date: 2026-06-10
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Local-only: yes (Mac Studio). VM/execution/model/cron/registry changes: none.
Holdout access: none.

Machine-readable: `outputs/research/pit_rebaseline/polaris_2026-06-10.json`.

## 1. Executive Summary

**A faithful *priced* Polaris PIT rebaseline cannot be completed in this session,
and no performance numbers are fabricated.** Two hard data dependencies are
missing:

1. **Delisted prices.** The local price matrix is current-names-only; Sharadar
   SEP adjusted prices for delisted securities have not been downloaded (needs the
   API key + a bulk fetch). The delisted-loser return channel cannot be priced.
2. **A PIT large-cap membership family.** `Universe(as_of_date)` is the whole
   ~20,618-name market, not a large-cap family mirroring Polaris's selection.
   Swapping it in directly would convert Polaris from large-cap to all-cap — not
   an apples-to-apples universe swap (it would violate "everything else
   identical").

**What is measurable now (deterministic, no prices):** the membership-level
**look-ahead correction** for Polaris's static 200-name universe. On
**2014-01-02, 17 of 200 names (8.5%) were not eligible** — 16 had not yet IPO'd
and 1 is a ticker-format mismatch. The look-ahead names are concentrated in
**high-growth winners a momentum strategy weights heavily** (ABNB, COIN, PLTR,
CRWD, UBER, HOOD, DASH, APP, CEG, GEV…), which systematically inflates early-period
legacy results.

**Classification: MATERIAL (provisional; likely SEVERE once delisted losers are
priced). Historical Polaris results are materially distorted and not
decision-grade for promotion pending the PIT rebaseline.**

## 2. Polaris Backtest — Current Behavior (Task 1, documented, unchanged)

`alpha_stack/research/backtest.py :: AlphaStackBacktest`.

- **Universe source:** `_load_universe()` reads `data/universe.csv` once →
  `tickers = universe["ticker"].tolist()`. **The ticker set is static across all
  dates** — this is the survivorship mechanism. (Config: `alpha_stack.yaml`
  `universe.csv_path`.)
- **Prices:** `PricesDataStore.get_prices_multi()` (yfinance, `auto_adjust=True`).
  Downloaded once for the static tickers; only *prices* are PIT-sliced per date
  (`prices_pit = prices_df[prices_df["date"] <= dt]`), never the ticker set. No
  delisted coverage.
- **Ranking:** `compute_trend_features` + `compute_volatility_features`
  (12-1/6-1/3-1 momentum z-scores + ATR vol adjust), regime/sleeve allocation.
- **Cadence:** `rebalance_frequency` default `daily`.
- **Costs / sizing / risk:** unchanged and out of scope (frozen).
- **Artifacts:** `_persist()` writes a dated backtest output dir (NAV, returns,
  attribution, summary).

The **only** change contemplated by this rebaseline is the universe source:
`data/universe.csv` (static) → `Universe(as_of_date)` (PIT). Everything else is
frozen.

## 3. Membership-Level Comparison (runnable now)

Per rebalance date, of Polaris's 200 legacy names, how many were actually
tradable (PIT-eligible) vs look-ahead/no-match excluded:

| Date | Legacy | PIT-eligible | Excluded | Look-ahead % |
|---|---:|---:|---:|---:|
| 2014-01-02 | 200 | 183 | 17 | 8.5% |
| 2016-01-04 | 200 | 186 | 14 | 7.0% |
| 2018-01-02 | 200 | 188 | 12 | 6.0% |
| 2020-01-02 | 200 | 191 | 9 | 4.5% |
| 2022-01-03 | 200 | 197 | 3 | 1.5% |
| 2024-01-02 | 200 | 198 | 2 | 1.0% |
| 2026-01-02 | 200 | 199 | 1 | 0.5% |

Early-period backtests are the most contaminated; the bias decays as today's
names accumulate trading history.

## 4. Attribution — Look-ahead Inclusions on 2014-01-02 (Task 6, runnable channel)

The full set of names a legacy 2014 Polaris backtest wrongly includes (only 17
differ, so this is the complete list, not a top-25 truncation):

| Ticker | security_id | Reason | IPO / note |
|---|---|---|---|
| ABNB | SHARADAR:… | ipo_after_date | 2020-12-10 |
| ANET | SHARADAR:… | ipo_after_date | 2014-06-06 |
| APP | SHARADAR:… | ipo_after_date | 2021-04-15 |
| CEG | SHARADAR:… | ipo_after_date | 2022-02-02 (spinoff) |
| COIN | SHARADAR:… | ipo_after_date | 2021-04-14 |
| CRWD | SHARADAR:… | ipo_after_date | 2019-06-12 |
| DASH | SHARADAR:… | ipo_after_date | 2020-12-09 |
| DELL | SHARADAR:… | ipo_after_date | 2016-09-07 (relisting) |
| GEV | SHARADAR:… | ipo_after_date | 2024-04-02 (spinoff) |
| GOOG | SHARADAR:… | ipo_after_date | 2014-03-27 (C share) |
| HOOD | SHARADAR:… | ipo_after_date | 2021-07-29 |
| LIN | SHARADAR:… | ipo_after_date | 2018-10-31 (merger) |
| PLTR | SHARADAR:… | ipo_after_date | 2020-09-30 |
| PYPL | SHARADAR:… | ipo_after_date | 2015-07-06 (spinoff) |
| UBER | SHARADAR:… | ipo_after_date | 2019-05-10 |
| VST | SHARADAR:… | ipo_after_date | 2016-10-05 (spinoff) |
| BRK-B | (no match) | no_pit_match | Sharadar uses `BRK.B` — ticker-format/symbol-history issue |

(Exact `security_id`s are in the JSON artifact.) These are predominantly
high-momentum winners; including them before they existed biases early-period
Polaris returns **upward**.

## 5. Channel Analysis (Task 5)

- **A. Delisted securities — UNMEASURED.** Requires SEP delisted prices + a PIT
  large-cap membership family. The current 200-name universe contains ~0 delisted
  names *by construction* (survivor-curated), so this channel only appears once a
  proper PIT large-cap family (including large-caps that later delisted/merged —
  e.g. ATVI, TWTR, CTXS) is built and priced.
- **B. IPO timing — MEASURED.** 16 look-ahead inclusions on 2014, decaying to 1
  by 2026 (Section 3/4).
- **C. Symbol history — PARTIAL.** 1 no-match (`BRK-B` vs `BRK.B`); the resolver
  otherwise handles `relatedtickers` (e.g. FB→META).
- **D. Universe composition — N/A** for a like-for-like swap (not using the
  all-cap Universe directly).

## 6. What is Blocked, and How to Unblock (pre-registered priced plan)

The priced Legacy-vs-PIT comparison and the return-weighted top-25 attribution
require:

1. **SEP delisted-price hydration** (API key; bulk download of adjusted prices
   for the PIT securities, incl. delisted).
2. **A PIT large-cap membership family** (`caerus_large_cap`) that reproduces
   Polaris's selection rule historically (later FR-068 phase).

Then the run is mechanical: `AlphaStackBacktest` with universe selection replaced
by `Universe(as_of_date, "caerus_large_cap")` per rebalance date, identical
ranking/costs/sizing/risk, validation window only (holdout untouched), reporting
CAGR / Sharpe / Sortino / MaxDD / Volatility / Turnover / Hit Rate / Win-Loss /
Avg Hold / Concentration, plus top-25 securities by Legacy−PIT return
contribution tagged `delisted_loser` / `ipo_unavailable` / `survivor_distortion`
/ `symbol_history`. **No numbers are produced until that data exists.**

## 7. Governance Classification & Recommendation (Tasks 7, 9)

- **Quantitative return delta: INCONCLUSIVE** — not computable without delisted
  prices; no metrics fabricated.
- **Foundation-distortion classification: MATERIAL** (provisional; **likely
  SEVERE** once delisted losers are priced). Rationale: the measurable 2014
  look-ahead set is concentrated in high-growth momentum winners that the strategy
  over-weights, inflating early-period returns; combined with Phase 2's SEVERE
  universe distortion (71.7% of the market delisted and invisible), the legacy
  results are materially distorted.
- **Are historical Polaris results decision-grade?** **No — materially distorted
  and not decision-grade for promotion** pending the PIT rebaseline.
- **Recommendation:**
  1. Retain legacy results as `legacy_current_universe` (lineage; mark
     non-decision-grade / survivorship-biased).
  2. Make PIT results canonical once the priced rebaseline runs.
  3. **Require `universe_method = pit_universe` in all promotion evidence.**
  4. Run the full priced rebaseline after SEP delisted-price hydration + a PIT
     large-cap membership family.
- **Confidence:** membership delta **HIGH**; return-delta magnitude
  **UNQUANTIFIED**.

## 8. Constraints honored

Research-only. No production Polaris, execution, cron, model-parameter, ranking,
transaction-cost, sizing, or risk-control changes. No tuning. No holdout access.
The only contemplated change is the universe source. Artifacts deterministic; no
API keys exposed.
