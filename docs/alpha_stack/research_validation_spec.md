# Alpha Stack Research Validation Specification

## Purpose

This document defines the current validation expectations for Alpha Stack as it moves through staged promotion.

## Promotion Ladder

`research -> backtest -> shadow -> paper -> live`

The legacy model remains frozen while Alpha Stack is validated in parallel.

## Current Validation Reality

- Sleeve 2 is implemented, but its historical backtests are not yet point-in-time safe.
- Sleeve 1 is not complete enough for promotion-grade validation.
- Attribution exists as a target layer, but not yet as a finished validation framework.

## Current Blockers

1. Sleeve 2 point-in-time bias must be removed before its backtests can support promotion.
2. Sleeve 2 needs a full daily equity curve to support drawdown, turnover, and attribution analysis.
3. Sleeve 1 factor pipeline completion is required before meaningful combined-sleeve validation.
4. A transaction cost model is required before trusting reported returns.
5. Benchmark comparison should be included in the report stack.

## Planned Validation Sequence

1. Point-in-time data foundation.
2. Regime state machine definition.
3. Sleeve 1 extension and validation.
4. Sleeve 2 PIT refactor and revalidation.
5. Attribution module with IC/IR.
6. 60+ trading days of shadow mode.
7. Paper-readiness review.
8. Production cutover decision.
