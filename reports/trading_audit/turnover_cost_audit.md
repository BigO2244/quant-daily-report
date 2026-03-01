# Trading Turnover and Cost Audit Report

**Generated:** 2026-03-01T00:00:00.000000
**Analysis Date:** 2026-02-27

## Executive Summary

| Metric | Value | Status |
|--------|-------|--------|
| Annualized Turnover | 312.5% | OK |
| Avg Holding Period | 4.7 days | OK |
| Max Position | 18.5% | OK |
| HHI Index | 1847.0 | OK |

## Turnover Analysis

**Annualized Turnover:** 312.5%

**Weekly Distribution:**
| Metric | Value |
|--------|-------|
| Mean | 6012.5 |
| Median | 5900.0 |
| P90 | 7100.0 |

## Holding Period Analysis

**Average Holding Period:** 4.7 days
**Period:** 2026-02-13 to 2026-02-27
**Number of Rebalances:** 3

## Slippage Sensitivity Analysis

**Total Notional Traded:** $37,250.87
**Actual Fees:** $0.0000

**Cost Sensitivity (% of notional):**
| Slippage (bps) | Total Cost % |
|---|---|
| 0 | 0.0% |
| 5 | 0.005% |
| 10 | 0.01% |
| 20 | 0.02% |

## Concentration Analysis

**Maximum Position:** 18.5%
**HHI Index:** 1847.0
**Number of Positions:** 24

## Deployment Gates

| Gate | Status | Notes |
|------|--------|-------|
| Turnover < 500% | PASS | Annualized: 312.5% |
| Avg Holding > 3 days | PASS | 4.7 days |
| Max Position < 50% | PASS | 18.5% |
| HHI < 3000 | PASS | 1847.0 |

**Overall Status:** ✓ PASS

## Artifact Sources

- **Trades:** outputs/ledger/trades.csv
- **Holdings:** outputs/ledger/holdings_2026-02-27.csv
- **Ledger:** outputs/ledger/ledger_write_2026-02-27.json
