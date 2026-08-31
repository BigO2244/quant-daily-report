# HYP-2026-003 Terminal-Settlement Certification Result

As of: 2026-08-03 UTC
Classification: `RESEARCH_ONLY_NON_EXECUTIONAL`
Result: `NOT_CERTIFIED`

## Decision

The exact historical terminal-settlement contract is now explicit and
machine-auditable, but the current repository evidence cannot pass it.

## Evidence inspected

- The PIT security master identifies terminated/delisted security histories.
- Sharadar SEP supplies observed trading histories, including delisted names.
- The v3 price panel deliberately retains the final provider daily return as
  `last_observed_total_return` and leaves `delisting_return` and
  `terminal_return` null.
- The SEC/Sharadar delisting index links action candidates to nearby 8-K/8-K/A
  filings and explicitly labels them as candidates, not settlement proof.
- The terminal-return sensitivity bundle contains a further-total-loss and a
  zero-incremental scenario, neither of which is a verified point estimate.

## Missing certification evidence

No finalized source-hashed bundle in repository evidence establishes, for
every terminated name in the 2012-2024 HYP-2026-003 evaluation interval:

- a separately materialized, immutable price extract ending no later than
  2024-12-31;
- exact final cash paid per pre-action share;
- exact shares received and their receipt-date valuation;
- final resolution and value of contingent-value rights;
- all bankruptcy or liquidation distributions, including authoritative proof
  of zero recovery where applicable;
- effective and evidence-availability timestamps; and
- non-overlap with the provider's final observed return.

Missing cases remain missing. They are not censored, treated as zero, replaced
with the last trade, or filled from the sensitivity envelope.

## Consequence for HYP-2026-003

`pit_prices_liquidity_v1` remains blocked on independently verified terminal
settlements. The discovery/calibration return evaluator must not claim the
frozen price contract is certified from current evidence. The untouched
2025-2026 challenge period was not accessed.

This result changes no production, trading, broker, allocation, scheduler,
paper, pilot, live, deployment, or capital behavior.
