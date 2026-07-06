# CIO Review - Governance / Safety Review

Audit date: 2026-06-26

Scope: Review only. No broker orders were submitted. No production behavior was changed. Existing dirty worktree changes were treated as evidence/risk, not as changes made by this review.

## Summary

The safe path is to limit Friday-Sunday implementation to reporting-only and research/shadow improvements unless the operator explicitly approves otherwise. The system reportedly traded successfully today, and local June 26 canonical artifacts are absent, so execution behavior should not be changed blindly.

One hard blocker exists in the current dirty tree: `scripts/live_pilot_execute.py` contains logic that can convert approved BUY limit orders to market orders and submit them as market orders. That is live-pilot-impacting and execution-impacting. It must be split, blocked, or explicitly approved under FR-104 before merge/deploy. This review did not change it.

## Evidence Reviewed

- Current dirty worktree status and relevant modified/untracked modules
- Live-pilot order type and submission paths
- Live-pilot guardrail code and FR-104 governance doc
- Dynamic email live-pilot/account source selection
- Candidate lifecycle execution-path wiring
- Execution summary schema expansion
- FR-105 governance, research modules, and sparse artifacts
- Email/reporting paths that could make unsupported claims

## Files/Modules Inspected

| Module | Governance relevance |
| --- | --- |
| `scripts/live_pilot_execute.py` | Live-pilot order type, preflight, submission, evidence, reconciliation. |
| `core/live_pilot_guardrails.py` | FR-104 guardrails and approval inputs. |
| `docs/governance/fr_active/fr_104_live_pilot_unlock_program.md` | Live-pilot status/reconciliation expectations. |
| `core/dynamic_daily_email.py` | Dynamic live-pilot/account section source selection by latest artifact/mtime. |
| `scripts/send_trading_confirmation_email.py` | Confirmation email claims and dynamic section inclusion. |
| `scripts/run_precomputed_alpaca_execution.py` | Candidate lifecycle artifact generation, reliability, target-attainment. |
| `core/candidate_trade_lifecycle.py` | Audit-only lifecycle reconstruction. |
| `core/execution_summary.py` | Execution history schema and operator summaries. |
| `docs/governance/fr_active/fr_105_global_portfolio_optimizer_and_decision_provenance.md` | FR-105 research-only status. |
| `research/fr105_*` | Research-only concentration replay/frontier/holding count. |

## Findings

| Finding | Severity | Evidence | Risk classification | Proposed fix |
| --- | --- | --- | --- | --- |
| Live-pilot limit-to-market override exists in dirty tree | Critical | `scripts/live_pilot_execute.py` can mark approved limit buys as submitted market orders. | Live-pilot-impacting, execution-impacting, requires explicit operator approval | Split/block from reporting work. Require separate FR-104 approval for market order policy, slippage, and reconciliation semantics. |
| Live-pilot `CLEAN` status semantics may be unsupported | High | FR-104 docs expect terminal filled orders for `CLEAN`, while code/tests may accept open/accepted orders as clean. | Live-pilot-impacting if behavior changes; safe reporting-only if label-only | Align labels to artifact truth. Do not claim clean completion unless reconciliation supports it. |
| Dynamic live-pilot/account email source can be stale or unrelated | High | `core/dynamic_daily_email.py` selects latest live-pilot plan/run by mtime; confirmation includes dynamic sections. | Safe reporting-only if source-labeled | Date-fence by email trade date or render source date/run id prominently. |
| Morning summary artifact is stale locally | High | `outputs/trading_day_summary.json` has `trade_date=2024-01-15`. | Safe reporting-only | Add stale-artifact check/warning before morning report displays it. |
| Candidate lifecycle is execution-adjacent | Medium | Lifecycle module is audit-only, but wiring in execution path must not mutate trades/status/orders. | Safe reporting-only only if proven non-mutating | Add tests asserting lifecycle generation does not change payload, trade plan, submissions, or final status. |
| Execution summary schema expansion could affect consumers | Medium | `core/execution_summary.py` adds history fields consumed by dashboards/MCP/export. | Safe reporting-only with compatibility tests | Keep additive fields optional; run dashboard/status tests. |
| FR-105 artifacts are sparse | Medium | 2026-06-25 FR-105 artifacts have no candidate pool, selected variant, current positions, or execution payload. | Backtest/shadow-only | Do not promote. Add sparse-input and ex-ante provenance tests. |
| Score/expected alpha inputs require look-ahead controls | Medium | FR-105 frontier can rank by `expected_alpha`, `score`, or `conviction_score`. | Backtest/shadow-only until proven PIT-safe | Require point-in-time provenance before any backtest/shadow conclusion. |
| Email claims may outrun canonical artifacts | Medium | Emails can fall back to broker snapshots or latest artifacts. | Safe reporting-only if source-labeled | Every CIO claim must include source artifact and unavailable behavior. |

## Severity

- Critical: any dirty-tree live-pilot order-type mutation that can affect submitted orders.
- High: stale/unrelated live-pilot or morning artifacts in operator-facing emails; unsupported live-pilot completion labels.
- Medium: execution-adjacent audit wiring, additive schema changes, sparse FR-105 artifacts, score provenance/look-ahead.

## Governance Checks

| Check | Status | Required control |
| --- | --- | --- |
| No look-ahead bias | Not fully proven for FR-105 score inputs | Require ex-ante source artifact, as-of date, and no forward returns before score/concentration claims. |
| No silent execution behavior change | At risk in dirty tree | Block/split live-pilot order-type mutation unless explicitly approved. Reporting changes must not touch sizing/submission. |
| No untested promotion from shadow to trading | Current FR-105 appears research-only | Keep FR-105 non-executional until governance promotion. |
| No artifact schema breakage | Must be validated | Add optional fields only; keep existing keys stable; run email/dashboard/status tests. |
| No email claims unsupported by canonical artifacts | At risk | Add source path/date for every CIO claim; render unavailable instead of inference. |
| Preserve deterministic artifacts | Required | Reporting artifact builder must be read-only and deterministic from existing inputs. |
| Preserve successful execution path | Required | Do not modify `paper_broker`, allocator, risk controls, broker submit, live-pilot submit, or cron behavior in reporting patch. |

## Proposed Fix

1. Block live-pilot order-type mutation from this reporting/concentration workstream.
   - If market orders are desired for FR-104, create a separate operator-approved change with slippage policy, approval env var, tests, and rollback.

2. Limit near-term implementation to safe reporting-only patches.
   - Add CIO summary artifact.
   - Render source-labeled email fields.
   - Add stale artifact warnings.
   - Add inactive sleeve labels.
   - Add reliability and target-attainment blocks.

3. Keep FR-105 in research/shadow.
   - Fill sparse artifacts.
   - Add point-in-time checks.
   - Require promotion memo before paper/live influence.

4. Add non-mutation tests.
   - Lifecycle/reporting artifact generation must not alter trade plan, order list, broker calls, final execution status, or idempotency.

## Risk Classification

| Classification | Examples from this audit |
| --- | --- |
| Safe reporting-only | Email rendering, CIO summary artifact, source labels, stale warnings, inactive sleeve labels, reliability/target-attainment blocks. |
| Backtest/shadow-only | FR-105 top-N frontier, score dispersion, global optimizer research, portfolio score definition. |
| Paper-only | Config changes affecting paper targets/orders, paper-only alpha-concentration flag. |
| Live-pilot-impacting | Selected live-pilot order, order type, cap, approval, preflight, reconciliation status semantics. |
| Execution-impacting | Allocator objective, target weights, risk controls, broker submit, post-sell rebudget, order filters. |
| Requires explicit operator approval | Any live-pilot-impacting or execution-impacting change. |

## Validation Required

Minimum safe validation:

```bash
git status --short
git diff --check
.venv/bin/python -m py_compile core/candidate_trade_lifecycle.py core/execution_summary.py core/dynamic_daily_email.py core/trading_day_summary.py paper/build_execution_email.py scripts/send_trading_confirmation_email.py scripts/run_precomputed_alpaca_execution.py scripts/live_pilot_execute.py research/fr105_replay_contract.py research/fr105_phase1_baseline.py research/fr105_phase2_topn_frontier.py research/fr105_phase3_holding_count.py
.venv/bin/pytest Tests/test_execution_email.py -q
.venv/bin/pytest Tests/test_candidate_trade_lifecycle.py -q
.venv/bin/pytest Tests/test_latest_execution_timeline_status.py -q
.venv/bin/pytest Tests/test_dashboard_ui_status.py -q
```

Additional governance validation:

```bash
.venv/bin/pytest Tests/test_dynamic_daily_email.py Tests/test_confirmation_email_reconciled.py Tests/test_execution_summary.py Tests/test_live_pilot_execution_path.py Tests/test_live_pilot_guardrails.py Tests/test_fr105_replay_contract.py Tests/test_fr105_phase1_baseline.py Tests/test_fr105_phase2_topn_frontier.py Tests/test_fr105_phase3_holding_count.py -q
```

`Tests/test_live_pilot_execution_path.py` must be dry-run only unless the operator explicitly approves broker submission testing.

## Rollback Plan

Reporting rollback:

- Disable or revert new email sections in `paper/build_execution_email.py`, `scripts/format_precompute_email.py`, `scripts/send_trading_confirmation_email.py`, and `core/dynamic_daily_email.py`.
- Ignore/delete generated CIO summary artifacts under `outputs/runs/<run_id>/reports/`.

Candidate lifecycle rollback:

- Remove execution-path wiring from `scripts/run_precomputed_alpaca_execution.py`.
- Keep any generated lifecycle artifacts as historical audit outputs only.

FR-105 rollback:

- Ignore/delete `outputs/research/fr_105/<date>/`.
- Revert research modules/docs/tests if needed.
- Runtime behavior should remain unaffected if FR-105 stays research-only.

Live-pilot rollback:

- Revert or split `scripts/live_pilot_execute.py` live-pilot order-type changes.
- Set `CAERUS_LIVE_PILOT_KILL_SWITCH=1`.
- Keep `CAERUS_LIVE_PILOT_DRY_RUN=1`.
- Reconcile any already submitted live-pilot order by broker order id or client order id before operator action.

## Open Questions

1. Was the live-pilot market-order behavior intentionally approved outside this workstream?
2. Should FR-104 define `CLEAN` as accepted/open order, filled order, or fully reconciled terminal state?
3. Should dynamic live-pilot sections be suppressed entirely unless the source run matches the email trade date?
4. Which consumers require a stable `core/execution_summary.py` CSV schema?
5. Should broker-fallback emails be allowed to make CIO allocation claims, or only execution-status claims?
