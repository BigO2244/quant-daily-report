# Trading Turnover and Cost Audit Report

**Generated:** 2026-03-01T09:04:41.238633
**Analysis Date:** 2026-02-27

## Executive Summary

| Metric | Value | Status |
|--------|-------|--------|
| Annualized Turnover | None% | N/A |
| Avg Holding Period | None days | N/A |
| Max Position | 41.18% | OK |
| HHI Index | 3287.0 | OK |

## Turnover Analysis

**Status:** N/A
**Reason:** No trades data available

## Holding Period Analysis

**Status:** N/A
**Reason:** No trades data

## Slippage Sensitivity Analysis

**Total Notional Traded:** $121,891.40
**Actual Fees:** $0.0000

**Cost Sensitivity (% of notional):**
| Slippage (bps) | Total Cost % |
|---|---|
| 0 | 0.0% |
| 5 | 0.05% |
| 10 | 0.1% |
| 20 | 0.2% |

## Concentration Analysis

**Maximum Position:** 41.18%
**HHI Index:** 3287.0
**Number of Positions:** 4

## Deployment Gates

| Gate | Status | Notes |
|------|--------|-------|
| Turnover < 500% | FAIL | Annualized: None% |
| Avg Holding > 3 days | FAIL | None days |
| Max Position < 50% | PASS | 41.18% |
| HHI < 3000 | FAIL | 3287.0 |

**Overall Status:** ✗ FAIL

## Artifact Sources

- **Holdings:** /Users/brettolson/Documents/Caerus/quant-daily-report-main/outputs/runs/2026-02-27T110016-0500_f2a529c/snapshots/holdings_mtm_2026-02-27.csv
- **Ledger:** /Users/brettolson/Documents/Caerus/quant-daily-report-main/outputs/ledger/ledger_write_2026-02-27.json
- **Positions:** /Users/brettolson/Documents/Caerus/quant-daily-report-main/outputs/_archive/day0_reset_2026-02-25/ledger/positions_2026-02-24.csv
- **Trades:** /Users/brettolson/Documents/Caerus/quant-daily-report-main/outputs/audit/verify_full_main/trades.csv
