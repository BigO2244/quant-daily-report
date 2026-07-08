# Known Failures - 2026-07-08 Post-Track2 Baseline

Derived from: `af8238fcb0534683f37018e4e0dd81754865b9bb` (post-1c baseline, 25 nodes)
plus the Track 2 Part B fixture commit on branch `fix-reliability-planned-zero-submitted`.

## What changed vs post-1c

Exactly one node moved from FAIL to PASS:

- `Tests/test_run_precomputed_alpaca_execution.py::test_nonempty_planned_payload_zero_submitted_fails_with_drop_reason`
  moved FAIL -> PASS.

Nothing else moved. The change is test-only: the diff is confined to the body
(date literals) and docstring of that single test function. No production code,
shared fixtures, or `conftest.py` were touched, so no other node's status can
change as a result.

Root cause of the prior failure (Track 2, Part A + B): the fixture was dated
`2026-06-19` (Juneteenth), which the XNYS calendar treats as a
`MARKET_CLOSED_DAY`. On a closed day the market-closed exemption in
`core/operational_invariants.py` (the `expected_market_closed_day` branch,
~line 609) fires BEFORE the planned-payload-drop check and correctly classifies
the run GREEN, because zero submissions on a closed day are expected. The
classifier was verified correct in Part A; the fix is to the fixture, not the
classifier. The scenario was moved to `2026-07-08`, a confirmed OPEN trading day
(proven via `paper.trading_calendar.is_trading_day`: `2026-06-19 -> False`,
`2026-07-08 -> True`; prev-close anchor moved to the prior open day
`2026-07-07`). The assertion intent is unchanged: non-empty planned payload,
zero submitted, must classify `RELIABILITY_RED` with reason
`planned_payload_trades_dropped_before_execution`.

## Expected tallies (canonical Mac/VM environment)

Relative to the post-1c full-suite run (`25 failed, 2494 passed, 1 skipped,
137 warnings, 5 subtests passed` at 2520 collected):

- Collected: 2520
- Passed: 2495  (2494 + the now-passing target test)
- Failed: 24    (25 - the now-passing target test)
- Skipped: 1
- Errors: 0
- Deselected: 0
- Subtests passed: 5

Expected summary line:

```text
24 failed, 2495 passed, 1 skipped, 137 warnings, 5 subtests passed
```

## Pass-proof and verification (Cowork Linux sandbox)

- Target test, run isolated and verbose, on the fixed branch:
  `test_nonempty_planned_payload_zero_submitted_fails_with_drop_reason PASSED`
  (`1 passed`), re-derived twice independently of the edit. The full file passes
  (`8 passed`).
- The full tracked suite (296 tracked test files) was executed in this sandbox
  split into groups to fit the runner's per-call time limit, with the failing
  set aggregated:
  - Group A (148 files, ran to completion): `12 failed, 1321 passed` — the 12
    failures are exactly post-1c baseline nodes #1-#12, with zero additions.
  - Group B baseline files (the 5 files carrying nodes #13-#25): `12 failed,
    65 passed` — exactly nodes #13-#16 and #18-#25; node #17 (the target) is
    absent from the failed list (now passing). All 24 remaining baseline nodes
    reproduced verbatim.
  - Remainder of Group B swept in chunks with an 8s per-test timeout: no new
    logic failures.

## Honest caveat on the sandbox environment

This Cowork Linux sandbox does not perfectly reproduce the canonical Mac/VM
environment where the post-1c baseline was captured:

- Collection differs by a few nodes (2524 collected in-sandbox vs 2520 on the
  VM), driven by env-conditional tests and untracked local Finder-duplicate
  files (`* 2.py`), which are not part of the repo.
- A small number of sandbox-only failures appear that are NOT in the VM baseline
  and are NOT caused by this change, e.g. `Tests/test_shadow_daily_wrapper.py`
  fails with `PermissionError: Operation not permitted` writing under `logs/`
  (a FUSE-mount restriction), plus a couple of network-bound tests that time out.

Because of this drift, the authoritative single-process full-suite tally
(`24 failed, 2495 passed`) should be confirmed on the canonical Mac/VM
environment before it is treated as the official rebaseline. The failing-SET
comparison against the 25-node baseline is, however, complete and clean: all 24
remaining baseline nodes reproduce verbatim and the target node is proven passing.

## Failing Node IDs (24)

1. `Tests/test_argo_phase_a_evidence_framework.py::test_argo_phase_a_marks_phoenix_external_dependency_blocked`
2. `Tests/test_argo_phase_b_research_priority.py::test_argo_phase_b_forces_phoenix_as_top_research_priority`
3. `Tests/test_caerus_daily_health_check.py::test_green_case`
4. `Tests/test_caerus_daily_health_check.py::test_equality_gate_divergence_is_advisory_not_health_degrading`
5. `Tests/test_caerus_daily_health_check.py::test_execution_timeline_missing_is_yellow_operator_visibility`
6. `Tests/test_caerus_daily_health_check.py::test_yellow_not_aligned_case`
7. `Tests/test_caerus_daily_health_check.py::test_yellow_not_comparable_explicit_reasons`
8. `Tests/test_caerus_daily_health_check.py::test_yellow_price_cache_stale_from_shadow_sidecars`
9. `Tests/test_caerus_daily_health_check.py::test_latest_publishing`
10. `Tests/test_differentiation_diagnostic.py::test_true_weak_when_multiple_weak_signals`
11. `Tests/test_feedback_loop_artifacts.py::test_feedback_loop_writes_compact_rolling_index`
12. `Tests/test_governance_calibration.py::test_reclassification_shows_old_and_new_decisions`
13. `Tests/test_promotion_governance.py::test_deterministic_strategy_ordering`
14. `Tests/test_research_registry_mcp_server.py::test_execution_target_attainment_flags_stale_pre_buy_cash_snapshot`
15. `Tests/test_research_review_packet.py::test_tier3_sections_populate_when_artifacts_exist`
16. `Tests/test_research_review_packet.py::test_final_control_summary_surfaces_fixture_active_phoenix_without_overlay`
17. `Tests/test_strategy_differentiation.py::test_high_overlap_high_correlation_is_weak`
18. `Tests/test_strategy_differentiation.py::test_low_overlap_low_correlation_is_stronger_with_missing_factor_graceful`
19. `Tests/test_strategy_differentiation.py::test_strategy_differentiation_uses_date_bounded_shadow_inputs`
20. `Tests/test_strategy_differentiation.py::test_strategy_differentiation_uses_exact_factor_and_position_contributions`
21. `Tests/test_strategy_differentiation.py::test_strategy_differentiation_parses_contribution_report`
22. `Tests/test_strategy_differentiation.py::test_deep_strategy_differentiation_high_overlap_high_corr_is_weak`
23. `Tests/test_strategy_differentiation.py::test_deep_strategy_differentiation_low_overlap_low_corr_can_be_strong`
24. `Tests/test_strategy_differentiation.py::test_deep_strategy_differentiation_insufficient_observations_blocks_strong`

(Prior baselines `Tests/KNOWN_FAILURES_2026-07-08.md` and
`Tests/KNOWN_FAILURES_2026-07-08_post-1c.md` remain in the tree.)
