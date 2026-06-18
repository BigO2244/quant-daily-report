# FR-069 Cassiopeia Phase B 13D Event Tape

Status: PHASE_B_EVIDENCE_GENERATED
Owner: Caerus Research Program
Last Updated: 2026-06-18
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Classification: CASSIOPEIA_PHASE_B_NEEDS_DEEPER_EVIDENCE

RESEARCH_ONLY
NO_RUNTIME_CHANGE

## 1. Executive Summary

Cassiopeia Phase B built the first PIT-safe activist 13D event tape from SEC
EDGAR quarterly full-index rows and filing headers. The artifact proves that a
deterministic 13D tape can be built with availability timestamps, tradable-date
mapping, forward-return measurement, and PIT liquidity/capacity checks.

The evidence is not yet strong enough to classify Cassiopeia as promising for
Shadow-readiness review. The deduped event sample has positive 60D
SPY-relative return, but 1D, 5D, and 20D SPY-relative means are flat to
negative, and the tape still requires deeper event segmentation.

## 2. Data Sources

Primary event source:

- SEC EDGAR quarterly full-index files for 2022-01-01 through 2024-09-30.
- SEC SC 13D / SC 13D-A filing headers.
- Header field `<ACCEPTANCE-DATETIME>` as the PIT availability timestamp.

Mapping and market data:

- `data/alpha_stack_cache/edgar/sec_ticker_map.json`
- `cik_mapping_results.csv`
- `data/pit_universe/security_master.csv`
- `outputs/research/pit_liquidity/pit_liquidity_panel.csv`
- `alpha_stack_cache/prices/_matrix_prices_2007_2026.parquet` for SPY.

Generated artifacts:

- `outputs/research/cassiopeia/cassiopeia_phase_b_13d_event_tape_2026-06-18.json`
- `outputs/research/cassiopeia/cassiopeia_phase_b_13d_event_tape_2026-06-18.md`

## 3. PIT Methodology

The builder uses SEC filing-header acceptance timestamps, not filing dates or
campaign outcomes. Header timestamps are parsed as America/New_York local time.
Before-open filings are eligible on the same trading date; during-market and
after-close filings are eligible on the next trading date.

The study excludes 2025+ holdout evidence by ending event intake on
2024-09-30, leaving enough room for 60 trading-day forward returns before
2025-01-01.

Events fail closed if they lack acceptance timestamps, cannot map to a ticker,
are not active in the PIT security master on the tradable date, or cannot join
price/liquidity evidence.

## 4. Event Tape Coverage

| Metric | Value |
|---|---:|
| Raw mapped 13D/13D-A rows | 430 |
| Usable filing rows | 142 |
| Deduped ticker-date event rows | 132 |
| Excluded rows | 288 |
| Unique usable tickers | 27 |
| Timestamp missing count | 0 |
| PIT safe | true |

Forward-return and liquidity summaries use one event per `(ticker,
tradable_date)` to avoid overcounting multi-filer same-issuer campaigns. Raw
filings remain retained in the JSON event tape for auditability.

## 5. Exclusion Reasons

| Reason | Count |
|---|---:|
| `subject_cik_mismatch` | 282 |
| `security_not_active_on_tradable_date` | 10 |
| `price_or_liquidity_series_missing` | 10 |

The dominant exclusion is intentional: some master-index CIK rows resolve to
the filer/reporting owner rather than the subject issuer. Those rows are not
eligible unless the parsed subject CIK matches the mapped ticker lineage.

## 6. Forward Return Evidence

SPY-relative returns, deduped by ticker/tradable date:

| Horizon | Count | Mean | Median | Hit Rate |
|---|---:|---:|---:|---:|
| 1D | 132 | -0.0006200747 | 0.0009651264 | 0.5227272727 |
| 5D | 132 | -0.0015732632 | 0.0010691137 | 0.5227272727 |
| 20D | 132 | -0.0046447812 | -0.0013383334 | 0.4848484848 |
| 60D | 132 | 0.0176470355 | 0.0133060696 | 0.553030303 |

Interpretation: the first 13D tape shows a potentially interesting 60D drift,
but no robust short-horizon alpha. This is preliminary event evidence, not a
decision-grade alpha claim.

## 7. Liquidity/Capacity Evidence

Liquidity classification: `LIQUIDITY_OK`

| Metric | Value |
|---|---:|
| Measured event count | 132 |
| Measurement coverage | 1.0 |
| Reference capital | 1,000,000 |
| Target event weight | 0.02 |
| Minimum 5% ADV capacity | 253,688,295.75 |
| Minimum 10% ADV capacity | 507,376,591.50 |
| Maximum dollar ADV participation | 0.0001970923 |

Liquidity is not the current blocker. The blocker is evidence depth and
segmentation, not tradeability.

## 8. Differentiation vs Existing Sleeves

Cassiopeia is structurally differentiated from the current momentum family
because the trigger is an external corporate/event filing, not a price-rank or
trend score.

| Comparator | Differentiation Assessment |
|---|---|
| Polaris | Different event trigger; overlap may emerge only after price confirmation is added. |
| Orion | Different trigger source and holding thesis; empirical overlap still unmeasured. |
| Lyra | Different from holding-period momentum; correlation still unmeasured. |
| Phoenix | Different from crisis-reversal; both are event-like but 13D is issuer-specific. |
| Cygnus | Both use EDGAR timing, but Cygnus owns earnings 8-K drift and Cassiopeia owns activist/event catalysts. |

## 9. Classification

Classification: `CASSIOPEIA_PHASE_B_NEEDS_DEEPER_EVIDENCE`

Reason codes:

- `initial_tape_built`
- `deeper_sample_and_signal_segmentation_required`

The event source is usable and liquidity is viable, but preliminary return
evidence is mixed and needs segmentation by original vs amendment, filer type,
campaign freshness, sector, liquidity bucket, and crowded duplicate events.

## 10. Next Evidence Task

The next highest-value evidence task is a Phase B2 segmentation pass:

1. Separate original `SC 13D` filings from `SC 13D/A` amendments.
2. Deduplicate multi-filer same-issuer campaign clusters more formally.
3. Add filer/campaign recurrence features.
4. Measure event cohorts by sector, liquidity bucket, and filing type.
5. Add transaction-cost sensitivity and overlap/correlation versus existing
   sleeves.

## 11. Explicit Statement

RESEARCH_ONLY

NO_RUNTIME_CHANGE

This evidence does not activate Cassiopeia, generate live signals, change
allocations, change execution, change broker behavior, change risk controls,
change promotion thresholds, or modify cron.
