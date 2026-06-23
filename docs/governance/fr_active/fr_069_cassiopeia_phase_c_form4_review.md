# FR-069 Cassiopeia Phase C Form 4 Review

Status: PHASE_C_EVIDENCE_GENERATED
Owner: Caerus Research Program
Last Updated: 2026-06-19-pilot-v3
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Classification: CASSIOPEIA_PHASE_C_NEEDS_DEEPER_EVIDENCE

RESEARCH_ONLY
NO_RUNTIME_CHANGE

## 1. Executive Summary

Cassiopeia Phase C built a PIT-safe Form 4 insider activity event tape from SEC EDGAR submissions and Form 4 XML. The artifact tests whether insider buying can support a differentiated event sleeve after the activist 13D path was demoted to secondary research.

Forced conclusion: insider activity should remain Cassiopeia's primary research thesis, but the current evidence classification is `CASSIOPEIA_PHASE_C_NEEDS_DEEPER_EVIDENCE`. Relative to activist 13D, Form 4 is structurally more attractive because it has direct issuer/insider transaction labels and EDGAR availability timestamps; it is `not yet decision-grade` on measured return evidence in this first artifact.

This packet is research-only. It does not activate Cassiopeia, generate live signals, change allocations, change execution, change broker behavior, change risk controls, change promotion thresholds, or modify cron.

## 2. PIT Methodology

- Source: SEC submissions API plus Form 4 XML documents.
- Availability: `acceptanceDateTime` is parsed as UTC, converted to America/New_York, and mapped to tradable date using the existing 09:00/16:00 ET availability rules.
- Before-open filings are eligible on the same trading date; during-market and after-close filings are eligible on the next trading date.
- Events without clear acceptance timing, parseable Form 4 XML, active PIT security membership, or price/liquidity joins fail closed.
- The 2025+ period is excluded as holdout.

## 3. Event Coverage

| Metric | Value |
|---|---:|
| Raw Form 4 filings | 50 |
| PIT-valid filings | 16 |
| Summary events | 16 |
| Excluded filings | 34 |
| Unique tickers | 5 |
| Missing timestamps | 0 |
| PIT safe | True |

Forward-return and liquidity summaries dedupe by `(ticker, tradable_date, transaction_type)` to reduce same-day filing-cluster inflation while preserving raw filings in the JSON artifact.

## 4. Exclusion Reasons

```json
{
  "form4_xml_parse_failed": 28,
  "no_open_market_purchase_or_sale": 34,
  "price_or_liquidity_series_missing": 5,
  "security_not_active_on_tradable_date": 5
}
```

## 5. Forward Return Evidence

SPY-relative summary events:

| Horizon | Count | Mean | Median | Hit Rate | T-stat |
|---|---:|---:|---:|---:|---:|
| 1D | 16 | -0.001498848 | -0.0036453521 | 0.375 | -0.4844205385 |
| 5D | 16 | 0.0085909432 | 0.0080352093 | 0.625 | 1.1574672296 |
| 20D | 16 | 0.056310716 | 0.020357235 | 0.6875 | 1.879881029 |
| 60D | 16 | 0.201810836 | 0.1420288411 | 0.8125 | 4.1268837296 |

Purchase cohort:

- 20D SPY-relative: count `None`, mean `None`, hit rate `None`, t-stat `None`.
- 60D SPY-relative: count `None`, mean `None`, hit rate `None`, t-stat `None`.

## 6. Liquidity/Capacity Review

Liquidity classification: `LIQUIDITY_OK`

| Metric | Value |
|---|---:|
| Measured event count | 16 |
| Measurement coverage | 1.0 |
| Reference capital | 1000000.0 |
| Target event weight | 0.02 |
| Minimum 5% ADV capacity | 12605712329.25 |
| Minimum 10% ADV capacity | 25211424658.5 |
| Median ADV participation | 1.9526e-06 |
| Median implementation shortfall proxy bps | 10.069837 |

## 7. Differentiation vs Existing Sleeves

Form 4 insider activity is differentiated from Polaris, Orion, and Lyra because the trigger is a public insider transaction filing rather than price momentum. It is differentiated from Phoenix because it is not a crisis-reversal signal. It is differentiated from Cygnus because it does not depend on earnings 8-K drift. It is stronger as a Cassiopeia primary direction than activist 13D if purchase-cohort evidence remains positive after clustering, costs, role filters, and longer holdout validation.

## 8. Reviewer Findings

- PIT validity depends on filing acceptance time, not transaction date; filing delays are a real limitation.
- Role classification quality depends on Form 4 XML relationship fields and officer-title strings.
- Same-day filings can overstate sample size; this artifact uses deduped summary events for return and liquidity conclusions.
- CIK/ticker mapping remains repo-local and can miss historical ticker changes.
- Liquidity is measured from the existing PIT liquidity panel and should be rechecked before any Shadow request.

## 9. Classification

Classification: `CASSIOPEIA_PHASE_C_NEEDS_DEEPER_EVIDENCE`

Reason codes:

```json
[
  "no_usable_purchase_events",
  "form4_tape_built"
]
```

Decision answers:

- Is insider activity more promising than activist 13D? `not yet decision-grade`.
- Should insider activity become Cassiopeia's primary thesis? `yes`, as a research thesis only.
- Is the signal strong enough to continue? `yes`; continuation remains research-only and non-executing.

## 10. Next Evidence Task

Build Phase C2 with cluster-aware purchase cohorts, role-quality filters, transaction-value thresholds, explicit filing-delay diagnostics, cost sensitivity, sector cohorts, and matched overlap/correlation versus Polaris, Orion, Lyra, Phoenix, Cygnus, and SPY. Preserve 2025+ as holdout until a separate validation task consumes it.

## 11. Explicit Statement

RESEARCH_ONLY
NO_RUNTIME_CHANGE

This evidence does not activate Cassiopeia, generate live signals, change allocations, change execution, change broker behavior, change risk controls, change promotion thresholds, modify cron, or create trades.
