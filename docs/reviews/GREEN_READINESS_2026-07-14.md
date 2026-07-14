# GREEN Readiness Review — 2026-07-14

## Executive verdict

**READY_TO_ARM.** All GREEN criteria passed at the runtime-tested source SHA `d88c0eabfbab100a824a47ea0a26214891f3c875`. The final review-document merge is documentation-only; its resulting main SHA is recorded in the PR/handoff and must be fast-forwarded to the VM before owner re-arm. Live remains disarmed and no order was placed or cancelled.

## Current source and runtime state

| Item | SHA / state |
|---|---|
| Pre-integration `origin/main` | `d3fb068c67c3dd4a3a16388b5717826d6e72e602` |
| Safety release | `85a6fe26ef5a5aba95c9f4329552340e465db318` |
| Ledger/TCA integration base | `7c027c11c5bb5e5c3e96f1e243aea5688b7b112a` |
| Runtime-tested `origin/main` | `d88c0eabfbab100a824a47ea0a26214891f3c875` |
| VM HEAD at evidence capture | `d88c0eabfbab100a824a47ea0a26214891f3c875` (`main`) |
| VM deploy-state SHA at evidence capture | `d88c0eabfbab100a824a47ea0a26214891f3c875` |

Effective VM gates after the authorized atomic safety action:

```text
schedule=1
cron-approved=1
kill-switch=1
submit-approved=0
sells-enabled=1
ALPACA_PAPER=0
```

The environment backup is `/home/brettolson/.caerus/live_pilot.env.backup-disarm-20260714T204529Z`, mode `0600`. Before/backup SHA-256 is `98bb1077b235cde757341ee9b51539b469a17385d0c5d963c0e292fad81238f1`; after SHA-256 is `e726029613bfd4f7bb6f16c85c9466e36c2bb737290909491572ebce0b1dc421`. Independent comparison proved that only `CAERUS_LIVE_PILOT_KILL_SWITCH` and `CAERUS_LIVE_PILOT_SUBMIT_APPROVED` changed. A GET-only live Alpaca query returned zero open orders.

## Integrated commit inventory

The candidate is a linear fast-forward from the then-current `origin/main`, preserving the reviewed topology:

| Commit | Scope |
|---|---|
| `f3494fc` | Settled-cash/GFV buy clamp |
| `7b816c9` | Lane-truthful confirmations and race fix |
| `f9602fb` | Confirmation cron test reconciliation |
| `85c3d00` | Non-fatal completion-confirm hook |
| `6c5dbcf` | Hermetic settled-cash tests |
| `eebcac9` | Per-order validation partitioning |
| `bc0b177` | Submission partitioning and reconciliation semantics |
| `62e87aa` | `SUBMITTED_UNFILLED` reporting |
| `852e490` | Per-order and reconciliation tests |
| `8d27302` | Bulk-history freshness/date-bounded settlement verification |
| `d873bec` | Settled-cash branch merge |
| `5970cd2` | Per-order branch merge |
| `5277cea` | Reporting branch merge |
| `b65dcf9` | Merged-truth transition test reconciliation |
| `ce47022` | Deploy-SHA guard |
| `85a6fe2` | Scoreboard-email assertion reconciliation |
| `618a80d` | GET-only broker-truth ledger/report/cron |
| `b7a30e9` | Additive TCA decomposition |
| `b5fd0e3` | Nightly intended NAV/TCA refresh |
| `7c027c1` | Ledger/TCA hardening and reconciliation |

The new readiness commit adds only the authorized `N=[5,7]` and 30% ceiling, last-mile gate enforcement, classification-only live fail-closed behavior, canonical ledger cron source, tests, and this review.

Remote durability:

- `origin/backup/broker-truth-ledger-2026-07-14` → `7c027c11c5bb5e5c3e96f1e243aea5688b7b112a`
- `origin/research/concentration-thesis-2026-07-14` → `a89b207f751254f553b92948f40263c8eb0bc507`

The research branch was preserved but not merged. The independent per-layer sector-cap branch was reviewed and deferred: it bundles broader sector and cash-routing semantics not required for this mission. Only the minimum classification map needed to keep unknown live metadata fail-closed was ported.

## Claude concern-by-concern closure

| Concern | Closure / evidence |
|---|---|
| Live gates were ambiguously armed | Closed: atomic backup; kill switch `1`; submit approval `0`; exact changed-key proof |
| Open live orders could exist | Closed: canonical GET-only live query returned `0` |
| VM safety release was detached and ahead of main | Closed: all 16 commits integrated; VM fast-forwarded to canonical main |
| Ledger/TCA work was local and VM-only | Closed: backup pushed; five files tracked; byte-identical VM copies preserved outside repo; VM source canonical |
| Research work was not durable | Closed: existing July 14 research branch pushed unchanged |
| Three-to-five-name ranking lacked evidence | Closed in policy: no regime or override can request fewer than five; range is `[5,7]` |
| Single-name pilot exposure was too high | Closed in shared controls: defaults/overrides are bounded at 30% |
| Paper/live configuration could diverge | Closed: both consume the same signals loader and risk controls; parity test passes |
| Unknown layer classification could trade live | Closed: live plan blocks with `live_pilot_layer_unresolved` |
| Gates could change after planning | Closed: kill switch and submit approval are re-read immediately before every broker submit |
| Dry run or rerun could mutate broker state | Closed: two official dry runs had identical pre/post state and zero broker submissions/cancellations |
| Ledger reruns could duplicate or overwrite history | Closed in tests: append dedupe and revisioned prior-row preservation pass |
| NAV/TCA could disagree with Alpaca truth | Closed: live and paper ledger reconciliation passed; TCA report `reconciles=True` |

## Strategy guardrails and rationale

- Regime-adaptive requested concentration is clamped to `N ∈ [5,7]`, including environment overrides; fallback is five.
- Temporary shared maximum single-name target is `0.30`; a stricter explicit cap remains allowed, while a looser value is clamped.
- Cash remains explicit. The concentration transform retains at least the existing 5% target and routes cap residuals to cash.
- Paper and live retain a common signals/risk-control implementation; there is no live-only model or scoring fork.
- Unknown/missing sleeve classification blocks live planning. The broader per-layer sector-cap change is deferred.
- Settled-cash/GFV, per-order reconciliation, sell-first sequencing, model signals, sleeve scores, credentials, order types, and promotion rules were not weakened or changed.

The rationale is the July 14 research conclusion: evidence did not support reliable fine ranking for a three-to-five-name book, while `N >= 5` was supportable. The 30% temporary cap limits pilot single-name exposure without changing signals.

## Validation evidence

Static/operational checks:

- `git diff --check`: pass
- `py_compile` for changed runtime and ledger/TCA modules: pass
- `bash -n` for broker-ledger and live cron scripts: pass
- cron command validation: `28/28` pass
- operational validation: `6 pass, 0 warn, 0 fail`

Targeted execution-adjacent matrix: **297 passed**. An additional focused guardrail matrix passed **96 tests**, and affected regression nodes passed **43 tests** after fixture hardening.

Coverage includes shared transition, paper execution parity/lifecycle, live execution, full targets, settled cash/GFV, per-order partitioning, sell-first behavior, cron/confirmation, SHA guard, trading confirmation, concentration/risk, broker ledger, realized performance, TCA, portfolio history, idempotency, and NAV reconciliation.

## Full-suite exact failure-set comparison

Baseline at `7c027c1`: **27 failed, 2670 passed, 11 skipped, 5 subtests passed**.

Final candidate: **26 failed, 2676 passed, 11 skipped, 5 subtests passed**.

Set comparison:

```text
new failed node IDs:     []
removed failed node IDs: [
  Tests/test_live_pilot_build_plan_from_precompute.py::test_full_target_all_names_emitted_and_priced
]
```

The removed node was a stale assertion for the former 50% concentration ceiling. It now asserts the authorized 30% shared ceiling and passes. Every remaining failed node ID is identical to baseline:

```text
Tests/test_argo_phase_a_evidence_framework.py::test_argo_phase_a_marks_phoenix_external_dependency_blocked
Tests/test_argo_phase_b_research_priority.py::test_argo_phase_b_forces_phoenix_as_top_research_priority
Tests/test_caerus_daily_health_check.py::test_equality_gate_divergence_is_advisory_not_health_degrading
Tests/test_caerus_daily_health_check.py::test_execution_timeline_missing_is_yellow_operator_visibility
Tests/test_caerus_daily_health_check.py::test_green_case
Tests/test_caerus_daily_health_check.py::test_latest_publishing
Tests/test_caerus_daily_health_check.py::test_yellow_not_aligned_case
Tests/test_caerus_daily_health_check.py::test_yellow_not_comparable_explicit_reasons
Tests/test_caerus_daily_health_check.py::test_yellow_price_cache_stale_from_shadow_sidecars
Tests/test_differentiation_diagnostic.py::test_true_weak_when_multiple_weak_signals
Tests/test_feedback_loop_artifacts.py::test_feedback_loop_writes_compact_rolling_index
Tests/test_flow_detection_v2.py::test_cli_smoke_v2
Tests/test_governance_calibration.py::test_reclassification_shows_old_and_new_decisions
Tests/test_pit_universe.py::test_real_caerus_large_cap_family_certification_counts
Tests/test_promotion_governance.py::test_deterministic_strategy_ordering
Tests/test_research_registry_mcp_server.py::test_execution_target_attainment_flags_stale_pre_buy_cash_snapshot
Tests/test_research_review_packet.py::test_final_control_summary_surfaces_fixture_active_phoenix_without_overlay
Tests/test_research_review_packet.py::test_tier3_sections_populate_when_artifacts_exist
Tests/test_strategy_differentiation.py::test_deep_strategy_differentiation_high_overlap_high_corr_is_weak
Tests/test_strategy_differentiation.py::test_deep_strategy_differentiation_insufficient_observations_blocks_strong
Tests/test_strategy_differentiation.py::test_deep_strategy_differentiation_low_overlap_low_corr_can_be_strong
Tests/test_strategy_differentiation.py::test_high_overlap_high_correlation_is_weak
Tests/test_strategy_differentiation.py::test_low_overlap_low_correlation_is_stronger_with_missing_factor_graceful
Tests/test_strategy_differentiation.py::test_strategy_differentiation_parses_contribution_report
Tests/test_strategy_differentiation.py::test_strategy_differentiation_uses_date_bounded_shadow_inputs
Tests/test_strategy_differentiation.py::test_strategy_differentiation_uses_exact_factor_and_position_contributions
```

These are pre-existing research/fixture/environment expectations and do not overlap the approved runtime scope. They remain residual technical debt; no criterion was weakened to hide them.

## Dry-run reconciliation evidence

The official scheduled live-pilot path ran twice after the reporting fix, under `kill-switch=1` and `submit-approved=0`:

- Run IDs: `2026-07-14T20260714T173952-0400_live_pilot_cron_dry` and `2026-07-14T20260714T174015-0400_live_pilot_cron_dry`.
- Plan: `READY_FOR_MANUAL_APPROVAL`; 7 targets; maximum target `0.1965081857`; cash `0.05`; target plus cash `1.0`; every target priced.
- Current holdings `ALL` and `C` each appeared exactly once in keep/reduce/sell partition (both sell); no dropped orders.
- Each run intended 6 orders and recorded 6 `DRY_RUN_NOT_SUBMITTED` rows; broker-submitted count was zero.
- Reconciliation was `DRY_RUN_NO_SUBMISSION` / state `DRY_RUN`; settled cash was `$293.20`; `settled_cash_fail_closed=false` with an explicit verdict.
- Pre/post account, positions, and open-order snapshots were identical within each run.
- Client order IDs were unique within each run and disjoint across runs.
- Operator summary status was authoritative `DRY_RUN`; confirmation email was accepted and marked sent; post-reconciliation was `DRY_RUN_NO_SUBMISSION`, not `UNKNOWN`.
- Running SHA, `origin/main`, and deploy-state were identical at `d88c0eab...`; SHA drift and working-tree dirty flags were false.
- Live open orders remained count `0` with stable canonical hash `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` before/after validation.
- No cancellation path was called; source contains no dry-run cancellation action.

## Ledger/TCA durability and cron

- All five formerly VM-only source/test files are tracked in the candidate.
- Installed VM cron already contains one GET-only broker-ledger entry at `45 19 * * 1-5`; source `scripts/crontab.txt` now contains the identical entry.
- Ledger append/revision idempotency, prior-row preservation, realized performance, TCA decomposition, and portfolio-history/NAV tests pass.
- The GET-only ledger/report/TCA job completed `rc=0`: paper and live NAV reconciliations passed, no new orders/fills were added, TCA wrote its report with `reconciles=True`, and the live open-order hash was unchanged.

## Source-control and VM cleanliness

Local candidate worktree was created fresh from `origin/main`; unrelated user worktrees were not modified. Source review found no credentials, runtime outputs, generated broker account data, or research artifacts in the candidate diff. The five VM-only files matched the reviewed tracked files byte-for-byte and were preserved at `/home/brettolson/.caerus/green-readiness-untracked-20260714T211848Z` before deployment. VM tracked source is clean; no untracked executable Python/shell file exists under `scripts/`, `core/`, `paper/`, or `transition/`. Installed and tracked cron active-command sets are identical, with exactly one broker-ledger entry.

## Explicit non-changes

- No broker orders were submitted or cancelled.
- No credentials or account identifiers were changed or recorded.
- No model signals, rank scores, sleeve budgets, promotion rules, or order types changed.
- No trading cron timing changed.
- No 5% cash target, settled-cash rule, GFV rule, per-order validation, sell-first lifecycle, or SHA guard was weakened.
- No research-only artifacts were merged into deployable main.
- No per-layer sector-cap enforcement was merged.

## Known residual risks

- The unchanged 26-node full-suite baseline debt remains and should be remediated separately.
- Runtime target count can be below five only if fewer than five eligible upstream candidates exist; the selector requests at least five and the validated live plan produced seven.
- Dry-run validation proves construction, gating, reconciliation, and broker non-mutation, but not future live fill quality or slippage.
- The live account remains intentionally disarmed until the owner explicitly says `ARM LIVE`.

## Rollback

1. Keep kill switch `1` and submit approval `0`.
2. Record main, VM, deploy-state, gate, cron, and broker read-only evidence.
3. Revert the readiness commit(s) on main with `git revert`; never force-push.
4. Push the revert through the normal PR/non-force workflow.
5. Fast-forward the VM to reverted `origin/main` and rewrite `outputs/deploy_state.json` via the established deploy method.
6. Re-run operational, targeted, SHA, cron, ledger/NAV, and non-submitting dry-run validation.
7. Preserve all ledger, TCA, report, and recovery artifacts.

## Final owner checklist

- [x] Verdict is `READY_TO_ARM`; all evidence sections are complete.
- [x] Runtime-tested `origin/main`, VM HEAD, and deploy-state SHA are identical.
- [x] VM source is clean and canonical; no untracked executable source remains.
- [x] Kill switch is `1`; submit approval is `0`.
- [x] Live open orders are zero and unchanged across both dry runs.
- [x] Dry-run target count is at least five; max target is at most 30%; cash reconciles.
- [x] Holdings, prices, orders, settled cash/GFV, confirmation, and NAV reconcile.
- [x] Ledger cron is sourced from git and one ledger/TCA run passed without broker mutation.
- [x] No unresolved P0/P1 defect remains.
- [ ] Owner has reviewed the GREEN packet and explicitly says `ARM LIVE` before any re-arm.

## Proposed owner re-arm commands — do not run before `ARM LIVE`

The owner should first re-verify `origin/main == VM HEAD == deploy-state`, zero open live orders, and this document. After saying exactly `ARM LIVE`, atomically back up and edit only the two gate keys:

```bash
ssh caerus-vm 'python3 - <<"PY"
import os, tempfile
from datetime import datetime, timezone
from pathlib import Path

path = Path.home() / ".caerus" / "live_pilot.env"
backup = path.with_name(f"live_pilot.env.backup-arm-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
backup.write_bytes(path.read_bytes())
os.chmod(backup, 0o600)
updates = {
    "CAERUS_LIVE_PILOT_KILL_SWITCH": "0",
    "CAERUS_LIVE_PILOT_SUBMIT_APPROVED": "1",
}
lines = path.read_text().splitlines()
seen = set()
for i, line in enumerate(lines):
    key = line.split("=", 1)[0].removeprefix("export ").strip()
    if key in updates:
        prefix = "export " if line.startswith("export ") else ""
        lines[i] = f"{prefix}{key}={updates[key]}"
        seen.add(key)
if seen != set(updates):
    raise SystemExit(f"missing required gate keys: {sorted(set(updates)-seen)}")
fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", text=True)
try:
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")
        fh.flush(); os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
finally:
    if os.path.exists(tmp): os.unlink(tmp)
print(f"backup={backup}")
print("changed=CAERUS_LIVE_PILOT_KILL_SWITCH,CAERUS_LIVE_PILOT_SUBMIT_APPROVED")
PY
set -a; source ~/.caerus/live_pilot.env; set +a
printf "kill-switch=%s submit-approved=%s\n" "$CAERUS_LIVE_PILOT_KILL_SWITCH" "$CAERUS_LIVE_PILOT_SUBMIT_APPROVED"'
```

Expected verification output ends with:

```text
changed=CAERUS_LIVE_PILOT_KILL_SWITCH,CAERUS_LIVE_PILOT_SUBMIT_APPROVED
kill-switch=0 submit-approved=1
```
