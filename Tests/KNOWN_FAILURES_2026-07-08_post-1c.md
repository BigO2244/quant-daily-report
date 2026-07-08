# Known Failures - 2026-07-08 Post-1c Baseline

Derived SHA: `878de510e7dca27bdf0b79cd900639c4a780e9c9`

Collection and summary from the Step 2 full-suite run on the post-1c merge tree:

- Collected: 2520
- Passed: 2494
- Failed: 25
- Skipped: 1
- Errors: 0
- Deselected: 0
- Xfailed: 0
- Xpassed: 0
- Subtests passed: 5
- Warnings: 137

Summary line:

```text
25 failed, 2494 passed, 1 skipped, 137 warnings, 5 subtests passed in 451.24s (0:07:31)
```

Provenance:

- The prior pre-1c baseline remains in `Tests/KNOWN_FAILURES_2026-07-08.md`.
- These two tests moved from fail to pass through the `paper-on-core-1c` migration replacing the paper-native post-sell path:
  - `Tests/test_post_submit_snapshot_failure.py::test_postsell_snapshot_failure_preserves_submissions_and_halts_buys`
  - `Tests/test_post_submit_snapshot_failure.py::test_run_paper_day_handles_uuid_in_postsell_snapshot`
- R1 pass-proof was recorded on `878de510e7dca27bdf0b79cd900639c4a780e9c9`; both nodes reported `PASSED`, with `2 passed in 60.42s`.
- The reliability-classifier red remains in this 25-node baseline and is intentionally handled on the separate Track 2 branch.

## Failing Node IDs

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
17. `Tests/test_run_precomputed_alpaca_execution.py::test_nonempty_planned_payload_zero_submitted_fails_with_drop_reason`
18. `Tests/test_strategy_differentiation.py::test_high_overlap_high_correlation_is_weak`
19. `Tests/test_strategy_differentiation.py::test_low_overlap_low_correlation_is_stronger_with_missing_factor_graceful`
20. `Tests/test_strategy_differentiation.py::test_strategy_differentiation_uses_date_bounded_shadow_inputs`
21. `Tests/test_strategy_differentiation.py::test_strategy_differentiation_uses_exact_factor_and_position_contributions`
22. `Tests/test_strategy_differentiation.py::test_strategy_differentiation_parses_contribution_report`
23. `Tests/test_strategy_differentiation.py::test_deep_strategy_differentiation_high_overlap_high_corr_is_weak`
24. `Tests/test_strategy_differentiation.py::test_deep_strategy_differentiation_low_overlap_low_corr_can_be_strong`
25. `Tests/test_strategy_differentiation.py::test_deep_strategy_differentiation_insufficient_observations_blocks_strong`
