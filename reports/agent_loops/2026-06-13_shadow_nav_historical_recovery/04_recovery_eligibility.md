# 04 Recovery Eligibility

## Decision

Status: `NEEDS_OPERATOR`

Active production artifact replacement was not eligible in this pass.

## Passed Gates

- VM tracked working tree was clean.
- VM HEAD matched `origin/main` at `75b51c6223921c69b4625b0e31a3abe2a32733f5`.
- Incident backup directory existed.
- Incident backup manifest SHA-256 matched `fe69dddbb3845066ba65fe118a1a9eaf7622974a763a1f0b4d4f98f377b1805c`.
- Original preserved active artifact hashes matched expected values.
- Price panel covered `2014-01-02` through `2026-06-12`.
- Dated target weights and dated `shadow_performance.json` existed for `2026-06-08` through `2026-06-12`.
- Dated daily returns for all affected strategies and SPY were independently reconstructed exactly.
- No broker, execution, allocation, model, promotion, retirement, or cron path was invoked.

## Failed Gates

1. The declared `2026-06-05` anchor could not be proven against a single deterministic return convention.
2. The active CSV has no `2026-06-08` row despite `2026-06-08` being a trading day with complete dated artifacts.
3. The active CSV switched from a historical forward-return/backtest-like scale to the local same-day dated-performance scale on `2026-06-09`.
4. The codebase uses the label `weights_as_of_t` for both the dated same-day chain and the full historical forward-return writer, which makes a production restatement ambiguous without an owner-approved canonical convention decision.
5. A temporary full-history rebuild check was attempted in `/tmp`, but it exceeded the practical gate window and was stopped without producing replacement artifacts.

## Required Owner Decision

Before active replacement, the owner must choose the canonical `shadow_nav_series.csv` convention:

- Option A: historical/backtest convention, where row `t` represents target weights at `t` applied to return `t -> next_trading_day`, with transaction-cost treatment consistent with `run_backtest_prepared()`;
- Option B: dated-performance convention, where row `t` represents target weights at `t` applied to return `previous_trading_day -> t`, matching `shadow_performance.json`;
- Option C: rebuild and restate the entire series under a newly documented convention and treat prior CSV history as superseded.

Until that decision, the system should continue to fail closed: current Shadow cumulative performance is non-decision-grade, rankings and promotion signals should remain suppressed, and no model decision should use the corrupted CSV.
