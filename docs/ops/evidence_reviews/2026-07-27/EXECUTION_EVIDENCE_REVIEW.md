# 2026-07-27 Execution Evidence Review

Generated as a superseding forensic review. This addendum does not rewrite the
original 2026-07-27 artifacts or confirmation receipts.

## Executive verdict

| Scope | Verdict | Evidence |
| --- | --- | --- |
| LIVE_PILOT broker submission | GREEN | 3 submitted, 3 accepted, 3 filled, 0 rejected |
| LIVE_PILOT broker reconciliation | GREEN | Reconciliation `CLEAN`; no open orders after refresh |
| LIVE_PILOT account pin | GREEN, artifact persistence defect found | Pin was enforced; original preflight failed to persist the successful match |
| Original precompute bundle integrity | RED | KLAC contained non-finite execution-critical values |
| Paper target attainment | AMBER | Three buys were suppressed after the bounded sell-settlement wait exhausted |
| Original confirmation package coherence | RED | Header said 3/3 filled while account metrics said 0 fills / 0% |
| Patched evidence pipeline | GREEN under local regression validation | Run-scoped reporting, strict numeric validation, refreshed metrics, final-state receipt, and suppression retention are covered |
| Overall production pipeline | AMBER pending deployment and a clean native run | Historical source evidence cannot be retroactively made valid |

## Immutable source evidence

- Deployed and validated source SHA: `ff5aa480d3d59e2acbf6a56dc793bdeea3f7bcb5`
- Original planned execution payload SHA-256:
  `f6fed0b8958a965ba915441080933e887e2b7cea161f796a260d49f397ecd44d`
- LIVE_PILOT submit run:
  `2026-07-27T20260727T093609-0400_live_pilot_cron_submit`
- Paper submit run:
  `2026-07-27T20260727T093503-0400_paper_cron_submit`

The original planned payload contained KLAC with non-finite `entry_price`,
`stop_loss`, `take_profit`, and `notional`. The daily snapshot also contained
non-finite KLAC and UNP values. The bundle was incorrectly marked complete and
execution-validated.

No 2026-07-27 broker submission inherited a non-finite value. The plan builder
independently obtained a finite KLAC open price of `$211.15`; KLAC was not
submitted in either lane. The live broker orders were LRCX SELL, HLT BUY, and
CSX BUY, all filled.

## Root causes corrected

1. Python's permissive JSON encoding emitted `NaN`, and validators did not
   require finite positive quantity, planning price, and notional.
2. Final live submission guardrails could accept a `NaN` notional because
   comparisons with `NaN` are false.
3. Confirmation rendering selected the globally newest run instead of the
   explicitly confirmed run.
4. Confirm-time broker refresh updated reconciliation and execution results but
   left evidence metrics and capital usage stale.
5. The cron wrapper mixed stderr into captured JSON, so pointer extraction could
   silently fail and leave the workflow pointing at DRY_RUN.
6. The confirmation ledger recorded the discovery-time status rather than the
   post-refresh status.
7. Paper execution replaced the approved intent with submitted sells, erasing
   the three buys suppressed by `live_pilot_sell_settlement_timeout`.
8. Repeated retries from one trading day were counted as separate sessions,
   allowing the buy-entry escalation counter to advance too quickly.

## Local repair validation

- Focused precompute, broker-boundary, live-pilot, confirmation, cron, and
  evidence regression suite: `164 passed`.
- Integrity, lifecycle, reconciliation, dashboard-status, and workflow-status
  suite: `67 passed`.
- Operational validation: `6 passed`, `0 warned`, `0 failed`.
- Python compilation, shell syntax, patch whitespace, and correction-manifest
  JSON validation: passed.
- An expanded repository run reached `1754 passed`, `26 failed`, `10 skipped`
  before it was stopped in slow network-style tests. The observed failures were
  existing environment/baseline issues (including missing optional Alpaca/PIT
  data, pandas frequency compatibility, and parity golden drift), so this review
  does not represent the entire repository as clean.

## Green acceptance gate

Production may be called fully Green only after all of the following are true:

- The patch is deployed under an attested SHA.
- A corrupt-bundle fixture fails precompute validation, readiness certification,
  exact-plan validation, live plan validation, and final broker submission
  guardrails without invoking the broker SDK.
- A native scheduled precompute run contains no non-finite values and its
  contract is execution-valid.
- The live pointer, explicit run ID, execution results, refreshed evidence
  metrics, capital usage, reconciliation, confirmation body, and sent receipt
  agree on counts and final status.
- Approved but suppressed buys remain visible with their reason and operator
  action.
- The next native paper/live cycle completes without repair or backfill.

Until that deployment and native clean run occur, the honest classification is:
broker mechanics Green, historical evidence integrity Red, overall pipeline
Amber.
