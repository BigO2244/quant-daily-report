# CIO Review - Recommended Implementation Plan

Audit date: 2026-06-26

Scope: Plan only. No production behavior changes were made as part of this review.

## Summary

Recommended path for Friday afternoon through Sunday:

1. Protect the successful execution path first.
2. Implement reporting-only CIO visibility from existing artifacts.
3. Keep FR-105 alpha concentration in research/shadow until point-in-time evidence exists.
4. Block or split the dirty-tree live-pilot market-order behavior unless explicitly approved.
5. Do not change optimizer, allocation, sizing, broker submission, or live-pilot order behavior this weekend without a separate operator approval.

## Evidence Reviewed

- Email/reporting modules and dynamic sections
- Portfolio construction and paper execution paths
- Risk controls, paper config, and candidate lifecycle code
- Local artifacts under `outputs/`
- FR-105 2026-06-25 research artifacts
- Stale local morning summary artifact
- Swarm audit outputs for Workstreams 1, 2, and 3

## Files/Modules Inspected

| Area | Files/modules |
| --- | --- |
| Precompute email | `scripts/format_precompute_email.py`, `scripts/send_precompute_email.py`, `core/precompute_contract.py` |
| Execution email | `daily_trade_execution_email.py`, `paper/build_execution_email.py`, `paper/send_execution_email.py` |
| Confirmation email | `scripts/send_trading_confirmation_email.py` |
| Dynamic sections | `core/dynamic_daily_email.py` |
| Morning/status | `scripts/morning_report.py`, `core/trading_day_summary.py`, `core/execution_summary.py`, `scripts/latest_execution_timeline_status.py` |
| Lifecycle/reporting artifacts | `core/candidate_trade_lifecycle.py`, `core/execution_payload.py`, `core/execution_target_attainment.py`, `core/operational_invariants.py` |
| Construction | `daily_quant_report.py`, `regime/regime_config.py`, `core/portfolio_alloc.py`, `paper/signals_io.py` |
| Execution conversion | `paper/paper_broker.py`, `scripts/run_precomputed_alpaca_execution.py` |
| Risk/config | `core/risk_controls.py`, `paper/config_paper.json` |
| Live pilot | `scripts/live_pilot_build_plan_from_precompute.py`, `scripts/live_pilot_execute.py`, `core/live_pilot_guardrails.py` |
| Alpha concentration research | `docs/governance/fr_active/fr_105_global_portfolio_optimizer_and_decision_provenance.md`, `research/fr105_*`, `outputs/research/fr_105/2026-06-25/*` |

## Findings

| Finding | Severity | Plan impact |
| --- | --- | --- |
| CIO emails lack before/after allocation, concentration, target-vs-actual, constraints, and top-not-bought candidates. | High | Friday/Saturday reporting-only implementation. |
| Most missing fields are artifact-to-email gaps, not execution defects. | High | Build read-only summary artifact instead of touching execution. |
| Same-day June 26 artifacts are absent locally. | High | Do not make June 26-specific claims without canonical artifact retrieval. |
| Current construction is sleeve-merge and naturally broad. | High | Do not expect reporting patches to change concentration. |
| FR-105 is research-only and sparse. | Medium | Keep shadow/backtest-only; do not promote. |
| Dirty live-pilot market-order behavior is execution-impacting. | Critical | Block/split or require explicit operator approval. |
| Dynamic and morning sources can be stale/unrelated. | High | Date-fence or prominently label source date/run id. |

## Friday-Sunday Plan

### Friday Afternoon - Freeze Risk And Build Artifact Inventory

Risk classification: Safe reporting-only, plus one execution-impacting blocker.

1. Freeze runtime scope.
   - No optimizer, broker, paper execution, live-pilot, or cron behavior changes.
   - Do not merge/deploy `scripts/live_pilot_execute.py` market-order behavior without explicit operator approval.

2. Retrieve or locate canonical 2026-06-26 artifacts.
   - Precompute payload
   - Daily snapshot/signals
   - Execution payload/results
   - Candidate lifecycle artifact
   - Target-attainment artifact
   - Reliability artifact
   - Broker/account snapshots
   - Live-pilot plan/run artifacts, if relevant

3. Define the CIO summary schema.
   - Source path/date for every field.
   - `unavailable` for missing data.
   - No inferred model claims.
   - No broker calls.

4. Add reporting-only tests first.
   - Fixture with full artifacts.
   - Fixture with missing artifacts.
   - Fixture with stale artifact date.

### Saturday - Implement Minimal Reporting Patches

Risk classification: Safe reporting-only if scoped correctly.

1. Add read-only CIO decision summary builder.
   - Recommended output: `outputs/runs/<run_id>/reports/cio_decision_summary.json`
   - Sections: portfolio before/after, cash before/after, top weights, sectors, HHI/effective-N, target-vs-actual, holdings action, constraint trace, candidate suppression, reliability, target attainment.

2. Wire the summary into emails.
   - Precompute: planned target/constraint/candidate view.
   - Execution: intended/submitted/reliability/target-attainment view.
   - Confirmation: achieved allocation, target-vs-actual, suppressed/missing intended buys, reconciliation.

3. Harden source labels.
   - Prominent stale-source banner.
   - Source artifact path and trade date.
   - Date-fence live-pilot plan/run sections.

4. Improve status wording.
   - Replace misleading `Halt reason: none` on successful confirmation with `Status reason` or omit.
   - Replace inactive research sleeve `UNAVAILABLE` with expected inactive labels where registry supports it.

5. Keep score reporting conservative.
   - Render rank/score/expected-alpha only when directly sourced.
   - Do not call inferred `raw_score` alpha unless provenance says so.

### Sunday - Validate, Review, And Gate

Risk classification: Safe reporting-only; paper/live changes require approval.

1. Run targeted validation.
2. Render dry-run emails from fixtures and one real canonical artifact set if available.
3. Compare generated CIO summary with source artifacts.
4. Produce a short operator approval memo for any non-reporting proposal.
5. Leave FR-105 as research/shadow unless promoted by explicit governance.

## Proposed Fixes By Priority

| Priority | Fix | Risk classification | Validation |
| --- | --- | --- | --- |
| P0 | Block/split dirty live-pilot market-order change from reporting work. | Execution-impacting, requires approval | `Tests/test_live_pilot_execution_path.py`, guard test for order-type approval. |
| P1 | Add CIO summary artifact with source paths and unavailable states. | Safe reporting-only | Py compile, unit fixtures, no broker imports/calls. |
| P1 | Render before/after allocation, cash, concentration, target-vs-actual, constraints, suppressed candidates. | Safe reporting-only | `Tests/test_execution_email.py`, confirmation/precompute fixtures. |
| P1 | Add stale-source/date fencing for dynamic and morning reports. | Safe reporting-only | `Tests/test_dynamic_daily_email.py`, morning summary stale fixture. |
| P2 | Add reliability and target-attainment blocks to execution/confirmation emails. | Safe reporting-only | Existing reliability/target-attainment fixture tests. |
| P2 | Add construction trace from score/sleeve/target/trade/order lifecycle. | Safe reporting-only if read-only | `Tests/test_candidate_trade_lifecycle.py`, timeline/status tests. |
| P3 | Fill FR-105 sparse artifacts and score dispersion diagnostics. | Backtest/shadow-only | FR-105 phase tests, PIT provenance checks. |
| P4 | Propose config changes for breadth after evidence review. | Paper-only or execution-impacting | Backtest/shadow, dry-run, operator approval. |
| P5 | Design new alpha-concentration mode. | Backtest/shadow first; paper/live requires approval | New governance doc, shadow run, paper gate, rollback. |

## Severity

- Critical: live-pilot order-type mutation in dirty tree.
- High: missing CIO allocation data, missing target-vs-actual, missing constraints/suppression, stale source labeling, absent June 26 local canonical artifacts.
- Medium: score provenance, FR-105 sparse inputs, schema compatibility.
- Low: inactive sleeve label wording.

## Risk Classification

This weekend's recommended implementation scope:

- Safe reporting-only: allowed after tests pass.
- Backtest/shadow-only: allowed for FR-105 diagnostics; no paper/live influence.
- Paper-only: defer until CIO reviews reporting evidence.
- Live-pilot-impacting: blocked unless explicitly approved.
- Execution-impacting: blocked unless explicitly approved.
- Requires explicit operator approval: any paper/live promotion, optimizer/config change that affects orders, live-pilot order policy, or broker behavior.

## Validation Required

Run the user's suggested suite:

```bash
git status --short
.venv/bin/python -m py_compile core/dynamic_daily_email.py core/execution_summary.py core/execution_target_attainment.py core/operational_invariants.py core/candidate_trade_lifecycle.py paper/build_execution_email.py scripts/format_precompute_email.py scripts/send_trading_confirmation_email.py scripts/latest_execution_timeline_status.py daily_trade_execution_email.py
.venv/bin/pytest Tests/test_execution_email.py -q
.venv/bin/pytest Tests/test_candidate_trade_lifecycle.py -q
.venv/bin/pytest Tests/test_latest_execution_timeline_status.py -q
.venv/bin/pytest Tests/test_dashboard_ui_status.py -q
```

Add these before promoting any weekend reporting patch:

```bash
git diff --check
.venv/bin/pytest Tests/test_format_precompute_email.py Tests/test_dynamic_daily_email.py Tests/test_confirmation_email_reconciled.py Tests/test_execution_summary.py -q
```

Add these before any alpha-concentration research claim:

```bash
.venv/bin/pytest Tests/test_fr105_replay_contract.py Tests/test_fr105_phase1_baseline.py Tests/test_fr105_phase2_topn_frontier.py Tests/test_fr105_phase3_holding_count.py -q
```

Add these before any live-pilot change:

```bash
.venv/bin/pytest Tests/test_live_pilot_guardrails.py Tests/test_live_pilot_execution_path.py Tests/test_live_pilot_build_plan_from_precompute.py -q
```

## Rollback Plan

1. Reporting rollback
   - Disable new email sections with a feature flag or revert email renderers.
   - Ignore/delete generated `cio_decision_summary` artifacts.
   - Existing execution artifacts remain valid.

2. Candidate lifecycle/reporting rollback
   - Remove lifecycle/reporting artifact generation from execution path if non-mutation validation fails.
   - Keep generated audit files as non-canonical.

3. FR-105 rollback
   - Keep FR-105 outputs under `outputs/research/fr_105/` ignored by runtime.
   - Revert research/doc/test changes if they confuse governance.

4. Live-pilot rollback
   - Revert/split live-pilot market-order behavior.
   - Set `CAERUS_LIVE_PILOT_KILL_SWITCH=1`.
   - Keep dry-run on.
   - Reconcile any already-submitted order by broker truth before operator action.

5. Config/optimizer rollback
   - Do not deploy without explicit approval.
   - If later approved and rolled back, restore prior config/allocator objective and rerun dry-run equality checks.

## Open Questions

1. Can the canonical June 26 artifact bundle be copied into this checkout for exact CIO review?
2. Should the CIO summary be generated at precompute time, execution time, confirmation time, or all three with stage-specific fields?
3. Should stale dynamic sections be suppressed, or shown with a warning banner?
4. What target concentration should define alpha chase: top 5, top 10, max single-name 20-25%, effective-N floor, or a score-derived frontier?
5. Who must approve any transition from FR-105 research to paper-only and then live-pilot influence?
