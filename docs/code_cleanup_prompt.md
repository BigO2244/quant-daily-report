# Code Cleanup Prompt

Use this prompt after the market window, especially if the 9:35 AM ET execution or 10:00 AM ET confirmation run fails, degrades, or behaves inconsistently.

## Prompt

You are cleaning and hardening the Caerus / Alpha Stack codebase after an operational trading workflow incident.

Context:

- The system is a deterministic quantitative trading platform with strict operational artifacts.
- Production safety matters more than elegance.
- The repository may contain partial deploy drift, stale modules, dead code, duplicate code paths, inconsistent imports, memory or compaction pressure, and brittle workflow coupling.
- Do not change trading logic unless required to fix a concrete operational failure.
- Preserve deterministic artifact behavior.
- Preserve reconciliation and execution safety guards.
- Prefer small, surgical fixes over broad rewrites.

Primary objective:

- Clean up the code and workflow surfaces that created or exposed today's failure mode.

Secondary objective:

- Reduce the chance of future failures caused by:
  - partial deploys
  - missing imports / module drift
  - duplicate orchestration paths
  - stale or dead code
  - memory pressure, compaction pressure, or oversized in-memory payload handling
  - fragile artifact writes
  - hidden coupling between precompute, execute, and confirm phases

Today's observed failures to include:

- 7:00 AM ET precompute failed on 2026-03-26 because `daily_quant_report.py` imported `core.research_context` but the scheduler host did not have `core/research_context.py`.
- 9:35 AM ET execution failed on 2026-03-26 because pretrade reconciliation detected stale / mismatched canonical positions versus Alpaca, auto-refreshed `outputs/paper_state/canonical_positions.json`, and halted the run with `precompute_reconciliation_self_heal` before any orders were submitted.
- The 9:35 AM ET reconciliation drift was concrete, not hypothetical: canonical quantities were stale versus broker on `ABBV`, `PSX`, `VZ`, and `WBD`.
- After the self-heal, a fresh reconciliation classified as `WARN` with small equity drift and `allowed_to_execute=true`, but the execution wrapper still converts non-`PASS` reconciliation states into a halt. Cleanup must determine whether that policy is intentional or an implementation mismatch.
- The 9:35 AM ET failure also left `outputs/execution_locks/2026-03-26.lock` in place, which blocks a same-day manual retry unless the lock is explicitly cleared.
- 10:00 AM ET confirmation did run on 2026-03-26, but it produced no operator email because `daily_trade_execution_email.py` suppressed the `HALTED` execution state and `scripts/cron_confirm.sh` skipped the trading confirmation email when `execution_results.json` was absent.
- The 10:00 AM ET confirmation phase exited `0` despite `terminal_status=failed_pre_execution`, which hid the incident from operator-facing monitoring.

Required workflow:

1. Inspect the relevant files first.
2. Summarize the concrete failure modes and their likely root causes.
3. Identify cleanup candidates in priority order:
   - import / module consistency problems
   - deploy-safety gaps
   - duplicated code paths
   - dead code and stale compatibility shims
   - oversized payload or memory hotspots
   - logging / observability gaps
4. Propose the smallest safe set of fixes.
5. Implement the fixes.
6. Run the narrowest validations that prove the fix.
7. Report:
   - summary
   - files changed
   - validations run
   - residual risks
   - recommended next cleanup steps

Specific cleanup expectations:

- Add or improve startup/import smoke checks for critical scripts.
- Make partial deploy failure modes obvious and fast-failing.
- Remove duplicate authority between workflow phases where possible.
- Reconcile the policy mismatch between `reconciliation.py` (`allowed_to_execute=true` for `WARN` / `SELF_HEAL`) and `scripts/run_precomputed_alpaca_execution.py` (halts on any non-`PASS` decision).
- Decide and document the intended same-day retry behavior after auto-bootstrap / self-heal events.
- Ensure execution locks are released or clearly recoverable after pre-execution halts.
- Consolidate artifact-writing logic if duplicated.
- Reduce unnecessary in-memory duplication of large payloads or reports.
- Identify any spots where large DataFrames or JSON payloads can be streamed, pruned, or persisted earlier.
- Keep email failures non-blocking unless explicitly configured strict.
- Keep execution safety checks blocking where appropriate.

Boundaries:

- Do not remove audit trails.
- Do not hide failures behind broad exception swallowing.
- Do not silently change trading decisions during cleanup unless the failure requires it.
- Do not do a style-only refactor.

Success criteria:

- Critical scripts import cleanly on the target runtime.
- Phase 1, 2, and 3 responsibilities are explicit and non-duplicated.
- Artifact generation is deterministic and resilient.
- Operational failure messages are specific enough to diagnose remotely.
- Any 9:35 / 10:00 incident from today is either fixed or converted into a clearly logged, well-bounded failure mode.

## Post-Run Notes

Fill this in before using the prompt:

- 9:35 AM ET status: failed_pre_execution; halt_reason=`precompute_reconciliation_self_heal`; Alpaca submitted_count=0.
- 10:00 AM ET status: cron fired at 10:00:02 AM ET; HALTED status artifact written; operator email suppressed; trading confirmation skipped because `execution_results.json` was missing; script exit code remained `0`.
- Any execution errors: stale canonical snapshot from 2026-03-25; quantity mismatches on `ABBV`, `PSX`, `VZ`, `WBD`; auto-bootstrap/self-heal halted the run; follow-on rerun path currently still halts on `WARN`; same-day execution lock remained in place.
- Any confirmation/email errors: HALTED pre-trade status was suppressed from email delivery; confirmation phase skipped because there was no `execution_results.json`; confirmation cron did not escalate the failed run.
- Any suspicious memory, compaction, timeout, or large-payload behavior:
