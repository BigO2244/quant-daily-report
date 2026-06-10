# Survivorship Bias / Current-Universe Lookback Audit

Date: 2026-06-10
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Local-only: yes; VM/deploy actions: none; 2025+ holdout read/run: no

## 1. Executive Summary

**Verdict: CONFIRMED_BIASED. Confidence: HIGH.**

The active research/backtest universe is `data/universe.csv`, a static 200-ticker `ticker,sector` file with no membership start dates, membership end dates, delisting dates, stable security identifiers, or point-in-time eligibility metadata. Polaris/Orion/Lyra shadow and alpha_lab backtests load this file directly; Cygnus Stage 1 maps the same current tickers to CIKs. This means historical tests are run against today's surviving candidate set, not the securities that were eligible on each historical date.

The engine does not appear to select pre-IPO names before local price/signal history exists, which limits one contamination channel. The larger defect remains: delisted losers and historical constituents that disappeared are absent, while current winners enter history as soon as their price history becomes sufficient.

## 2. Universe Sources Inspected

| Source | Role | Findings |
|---|---|---|
| `data/universe.csv` | Primary strategy universe | 200 unique current tickers; columns `ticker, sector`; no PIT fields; no delisted flag. |
| `cik_mapping_results.csv` | Cygnus EDGAR mapping | 200 current tickers mapped to CIK/status; inherits current-universe bias. |
| `data/security_master/manual_aliases.json` | Alias sidecar | Manual aliases only; not a PIT security master. |
| `outputs/research/flow_detection_v1/price_panel.parquet` | Local OHLCV panel | Price history for current universe and SPY from 2014 onward; does not encode universe membership. |
| `alpha_stack_cache/prices/_matrix_prices_2007_2026.parquet` | Older close matrix | Wider lookback close matrix for current ticker set; still no PIT membership/delisted constituents. |

## 3. Time-Machine Test Results

| Date | Current universe count | Had any local price by date | Signal-ready count | Not trading/no local price yet | Current-engine top 10 if rebalanced |
|---|---:|---:|---:|---:|---|
| 2014-01-02 | 200 | 184 | 0 | 16 | none |
| 2016-01-04 | 200 | 186 | 186 | 14 | NFLX, AMZN, NVDA, GOOG, GOOGL, PANW, SBUX, EQIX, NKE, MNST |
| 2020-01-02 | 200 | 191 | 190 | 9 | AMD, LRCX, KLAC, APO, BX, AMAT, AAPL, TDG, HWM, STX |
| 2022-01-03 | 200 | 197 | 195 | 3 | NVDA, BX, F, FTNT, KKR, SPG, INTU, AMD, COP, STX |
| 2024-01-02 | 200 | 198 | 199 | 2 | COIN, APP, PLTR, NVDA, META, CRWD, UBER, DELL, PANW, RCL |

Examples of current tickers unavailable at the historical start:
ABNB, ANET, APP, CEG, COIN, CRWD, DASH, DELL, GEV, HOOD, HWM, MMC, PLTR, PYPL, UBER, VST

## 4. Delisting / Survivorship Findings

- Delisted securities represented in `data/universe.csv`: **0**.
- Current tickers only: **200**.
- Historical constituents missing: **unknown count without PIT vendor/constituent table**, but structurally certain because no delisted or ended memberships exist.
- Complete local price history from 2014-01-02: **184/200 (92.0%)**.
- Likely unavailable at 2014 start by local price inference: **16/200 (8.0%)**.
- The backtest implicitly excludes securities that disappeared before the current universe snapshot, which likely excludes bankruptcies, acquisitions, major decliners removed from the investable family, and stale tickers whose histories would have hurt historical selection.

## 5. Backtest Sensitivity Results

Window used: 2014-01-02 through 2024-12-31 to avoid touching the 2025-forward holdout.

| Variant | Universe size | CAGR | Sharpe | Max drawdown | Excess vs SPY | Avg turnover |
|---|---:|---:|---:|---:|---:|---:|
| A_current_universe_as_is | 200 | 0.2499 | 0.969 | -0.4307 | 7.6907 | 0.144199 |
| B_restricted_valid_price_on_2014_01_02 | 184 | 0.1929 | 0.83 | -0.404 | 3.049939 | 0.149259 |

Interpretation: restricting to names with valid local prices at the 2014 start is not a true PIT universe, but it is a useful fragility test. The official current-universe result should not be considered decision-grade until it is rerun on a PIT universe with delisted names.

Top approximate contributors — A_current_universe_as_is:
NVDA (0.506866), AMD (0.286705), APP (0.24888), TSLA (0.22248), ANET (0.148628), PLTR (0.121891), META (0.099897), AMAT (0.090744), AVGO (0.075339), LRCX (0.073834)

Top approximate contributors — B_restricted_valid_price_on_2014_01_02:
NVDA (0.508645), AMD (0.339163), TSLA (0.196481), META (0.116722), MU (0.098431), LRCX (0.088256), AMAT (0.085959), AVGO (0.080408), FCX (0.077584), AAPL (0.07731)

## 6. Point-in-Time Universe Design

Implement `Universe(date)` as a first-class data contract. It must return only securities eligible as of that date using immutable membership rows, not today's ticker list projected backward.

Required fields: `security_id`, `perm_id`, `ticker`, `company_name`, `membership_start_date`, `membership_end_date`, `listing_date`, `delisting_date`, `exchange`, `asset_type`, `index_or_family`, `source`, `source_asof_date`, `confidence`, `symbol_change_history`, and `data_availability_flags`.

Eligibility rule: include a security only when `membership_start_date <= date` and `membership_end_date` is null or after the date, then apply data-availability flags and governance exclusions known as of that date.

## 7. Vendor / Source Recommendation

- Local reconstruction is sufficient for diagnostics only. It cannot prove historical constituents or delisted returns.
- Minimum data before official rebaseline: survivorship-free prices, delisting dates/returns, stable IDs, symbol changes, and PIT membership or PIT market-cap/index-family reconstruction.
- Sharadar SEP/SFP or equivalent may help if the trial verifies historical delisted small-cap coverage and PIT reconstruction support.
- Norgate is a strong survivorship-free candidate but has an operational Windows/NDU dependency.
- CRSP/WRDS is the gold standard if access exists.
- Polygon/Tiingo/Nasdaq Data Link can help only if verified for delisted coverage, stable IDs, and PIT membership; pricing alone is insufficient.

## 8. Recommended FRs

1. Point-in-time universe build.
2. Vendor verification, starting with the existing Sharadar verifier once a trial key is available.
3. Backtest invalidation / rebaseline: mark current-universe backtests non-decision-grade until rerun on PIT universe.

## 9. Verdict

- Universe bias status: **CONFIRMED_BIASED**.
- Confidence: **HIGH**.
- Reason: static current ticker file is demonstrably used as historical candidate universe and lacks the fields required to answer eligibility as of a past date.

## 10. Validation Commands Run

- `git branch --show-current`
- `git rev-parse --short HEAD`
- `git status --short`
- `rg ... universe ...` to identify universe loaders and consumers
- `.venv/bin/python3` local analysis over `data/universe.csv` and local price panels
- `.venv/bin/python3 -m json.tool outputs/research/survivorship_bias/2026-06-10/survivorship_bias_audit.json`
- `git diff --check` after artifact creation
