# PIT Universe Impact Assessment

Date: 2026-06-10
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Local-only: yes (Mac Studio). VM/execution/model/cron/registry changes: none.

Inputs: `data/pit_universe/` (FR-068 Phase 1 canonical ingest, mode=live,
source=sharadar_tickers) vs the legacy `data/universe.csv` (201 current names).
Machine-readable: `outputs/research/pit_universe/2026-06-10/pit_impact_assessment.json`.

## 1. Executive Summary

The canonical PIT universe contains **20,618 securities — 5,828 active and
14,790 delisted (71.7% delisted)**. The legacy research foundation
(`data/universe.csv`, the current-only price matrix, and current-only
fundamentals) sees **none** of the 14,790 delisted securities and applies today's
201 curated survivors retroactively. The distortion is classified **SEVERE** and
confirms, with full quantification, the prior CONFIRMED_BIASED / HIGH audit.

## 2. Universe Composition by Date

`Universe(as_of_date)` = securities trading on the date (firstpricedate ≤ date ≤
lastpricedate, common stock). "In current universe" = the date-eligible PIT
security's ticker is among the 201 `data/universe.csv` names. "Current-universe
unavailable" = of the 201 current names, how many were **not yet tradable** (or
already gone) on the date — the look-ahead channel.

| Date | PIT count | Active | Delisted | In current 201 | Absent from current | Current-201 unavailable | % unavailable |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2014-01-02 | 5,302 | 2,473 | 2,829 | 181 | 5,121 | 17 | 8.5% |
| 2016-01-04 | 5,370 | 2,781 | 2,589 | 184 | 5,186 | 14 | 7.0% |
| 2018-01-02 | 5,185 | 3,053 | 2,132 | 186 | 4,999 | 12 | 6.0% |
| 2020-01-02 | 5,366 | 3,445 | 1,921 | 189 | 5,177 | 9 | 4.5% |
| 2022-01-03 | 7,638 | 4,313 | 3,325 | 195 | 7,443 | 3 | 1.5% |
| 2024-01-02 | 6,333 | 4,667 | 1,666 | 196 | 6,137 | 2 | 1.0% |
| 2026-01-02 | 5,714 | 5,509 | 205 | 197 | 5,517 | 1 | 0.5% |

Each historical date had **2,000–3,300 delisted-but-then-trading securities** that
the current-only foundation cannot represent. On 2014-01-02, **17 of today's 201
names (8.5%) were not yet tradable** — a naive current-universe backtest includes
them anyway (look-ahead). One current ticker has no PIT match (minor; recent
listing / Sharadar gap).

## 3. Known Examples (inclusion/exclusion by date)

| Ticker | PIT identity | Window | Eligible dates |
|---|---|---|---|
| TWTR | SHARADAR:187959 (direct) | 2013-11-07 → 2022-10-27 (delisted) | 2014, 2016, 2018, 2020, 2022 — **excluded 2024, 2026** |
| ATVI | SHARADAR:198850 (direct) | 1993-10-25 → 2023-10-12 (delisted) | 2014, 2016, 2018, 2020, 2022 — **excluded 2024, 2026** |
| META | SHARADAR:194817 (direct) | 2012-05-18 → active | **all 7 dates** |
| FB | → SHARADAR:194817 via `relatedtickers` (→ META) | same as META | resolves to META on all dates |
| GYMB | SHARADAR:198521 (direct) | 1993-04-01 → 2010-11-22 (delisted) | **none** of the assessment dates (delisted before 2014); absent from current universe |

**FB illustrates the core PIT identity point.** `FB` is not a standalone security:
the Facebook→Meta lineage is preserved under one stable identity (permaticker
194817), with `FB` carried in META's `relatedtickers`. A *separate* recycled `FB`
ticker (Sharadar `FB1`, 1999–2003) also exists — proof that **tickers are reused
and must never be used as identity**; backtests join on `security_id`, not ticker.

TWTR and ATVI demonstrate the survivorship core: both were tradable for years,
delisted (Twitter taken private 2022; Activision acquired by Microsoft 2023), and
are **invisible to any current-universe backtest** despite being live, liquid
large-caps during the 2014–2022 window. GYMB is the small-cap loser channel —
gone in 2010, never in any current view.

## 4. Survivorship Distortion Metrics

- **Market delisted fraction: 71.7%** (14,790 / 20,618). The legacy foundation
  represents 0% of delisted securities. This is the dominant distortion signal.
- **Current-universe look-ahead** (today's 201 names not yet tradable):
  **8.5% on 2014-01-02**, decaying to 0.5% by 2026 — early-period backtests are
  the most contaminated by names that had not yet IPO'd.
- **Fraction of PIT names absent from the current universe: ~96.6% on 2014**
  (5,121 / 5,302). **Caveat:** this is *breadth-dominated*, not pure
  survivorship — the PIT universe is the full common-stock market (~5k tradable
  per date) while `data/universe.csv` is a 201-name curated large-cap list. It is
  reported for completeness but is **not** the decision-grade survivorship metric;
  the 71.7% delisted fraction and the look-ahead series above are.

## 5. Governance Conclusion

**Classification: SEVERE.**

Rationale:

1. **71.7% of the investable common-stock universe is delisted** and entirely
   absent from the legacy research foundation — historical backtests are computed
   exclusively on survivors.
2. The 201-name current universe is itself a survivor-curated set of today's
   winners, **plus** up to 8.5% early-period look-ahead inclusion of not-yet-listed
   names.
3. This is consistent with, and stronger than, the prior CONFIRMED_BIASED / HIGH
   audit and its 24.99% → 19.29% CAGR fragility gap (which only restricted to
   names priced in 2014 and did **not** add back delisted losers, so it understates
   true bias).

Implication (no action taken here): legacy current-universe backtests for
Polaris/Orion/Lyra remain **non-decision-grade**. The FR-068 PIT rebaseline
(Phase 2+) must precede any promotion decision. This assessment changes no
execution, model, cron, VM, or registry behavior.

## 6. Caveats

- Phase 1 is **security-existence** PIT, not historical *index* membership. "In
  current universe" matches by ticker (with a `relatedtickers` fallback); a small
  number of ticker-change/no-match cases exist and are counted explicitly.
- The PIT universe is the full Sharadar common-stock set; strategy universes
  (large-cap for Polaris; small-cap band for Vela) are membership families to be
  built in later FR-068 phases. Distortion for a *specific* strategy will be
  re-measured against its own PIT membership family at rebaseline.
