# FR-070 — Cash Gating and Post-Sell Buy Budget Reconciliation

## Status

DEPLOYED_OBSERVING

## Priority

HIGH

## Category

Execution Integrity / Capital Deployment

## Executive Summary

Historical targeted execution validation reported seven failing tests concentrated in post-submit artifact handling, cash gating, buying-power budgeting, pending-sell handling, and buy eligibility logic.

These failures were verified to exist before deployment of FR-069 (posttrade telemetry sequencing) and therefore are not regressions introduced by commit `0aab2e1`.

While recent live execution evidence suggests that sell execution, buy execution, fractional shares, and post-sell rebudgeting are functioning, the failing tests cover the exact execution-control surfaces responsible for capital deployment and therefore require formal review. The June 12 live-run investigation classified the cash discrepancy as `ARTIFACT_TIMING_FAILURE`, not failed rebudgeting; remediation is deployed and the workstream now monitors post-buy snapshot validation through the next live run.

New implementation work should reopen FR-070 only if diagnostics show a stale/pre-buy posttrade snapshot, buy timeout/failure, unclassified cash drift, reconciliation/target-attainment contradiction, or achieved cash materially outside tolerance without a classified reason. Because any remediation may alter trading behavior, any future implementation changes must be conducted as research first and deployed only during a scheduled maintenance window outside active market hours.

---

## Background

Recent execution workstreams have included:

- FR-031 Fractional Share Preservation
- Post-Sell Rebudgeting
- Capital Budget Basis Corrections
- Target Attainment Monitoring
- FR-069 Posttrade Telemetry Sequencing

On 2026-06-11:

- Sell orders executed successfully.
- Buy orders executed successfully.
- Fractional share orders filled successfully.
- Posttrade telemetry sequencing was corrected.

However, targeted validation still reports failures involving:

- Buy authorization logic
- Pending-sell handling
- Buying-power budgeting
- Cash reserve enforcement
- Operator reason-code generation
- Post-submit artifact failure paths

These failures require reconciliation between intended behavior, implemented behavior, and test expectations.

---

## Observed Failing Tests

### Artifact / Snapshot Failure Path Tests

#### test_run_paper_day_handles_uuid_in_postsell_snapshot

Observed:

- execution_outcome = post_submit_artifact_failure

Expected:

- execution_outcome = None

Notes:

- Failure path involves mocked broker objects lacking get_order support.
- May represent test-fixture drift rather than production behavior.

---

#### test_postsell_snapshot_failure_preserves_submissions_and_halts_buys

Observed:

- execution_reason = post_sell_rebudget_failed

Expected:

- execution_reason = post_sell_account_snapshot_write_failed

Notes:

- Appears related to artifact-failure classification behavior.

---

### Cash Gating / Capital Deployment Tests

#### test_pending_sell_does_not_block_affordable_buy

Expected:

- SELL submitted
- BUY submitted

Observed:

- SELL submitted only

Research Question:

- Is affordable-buy authorization behaving correctly after sell submission?

---

#### test_pending_sell_blocks_only_unaffordable_buy

Expected:

- SELL AAA
- BUY SML

Observed:

- SELL AAA only

Research Question:

- Is pending-sell logic overly restrictive?

---

#### test_postsell_buy_budget_uses_buying_power_when_paper_and_clean

Expected:

- B1 submitted
- B2 submitted
- B3 submitted

Observed:

- No buy submissions

Research Question:

- Is buying_power being ignored in some execution paths?

---

#### test_postsell_buy_budget_falls_back_to_cash_when_buying_power_zero

Expected:

- Buy skipped
- Skip reason recorded

Observed:

- No skipped order recorded

Research Question:

- Is fallback-to-cash behavior functioning correctly?

---

#### test_buying_power_covers_planned_buys_no_pending_sells_reason

Expected:

- BUY1 submitted

Observed:

- BUY blocked

Research Question:

- Is buying_power authorization inconsistent with intended behavior?

---

## Research Objectives

Determine whether the failing tests represent:

### Classification A

TEST_CORRECT / CODE_CORRECT

Behavior is correct and test expectations require updating.

### Classification B

TEST_INCORRECT / CODE_CORRECT

Tests no longer reflect intended execution behavior.

### Classification C

TEST_CORRECT / CODE_INCORRECT

Implementation contains a genuine execution defect.

### Classification D

TEST_INCORRECT / CODE_INCORRECT

Both implementation and tests require modification.

---

## Hypotheses

### H1 — Test Drift

Execution logic evolved during:

- post-sell rebudgeting
- buying-power basis migration
- cash reserve redesign

Tests may no longer represent intended behavior.

Confidence: Medium

---

### H2 — Pending-Sell Logic Conflict

Legacy pending-sell safeguards may still influence buy authorization despite post-sell rebudgeting being active.

Confidence: High

---

### H3 — Buying-Power Basis Inconsistency

Different execution paths may be using:

- cash
- buying_power

inconsistently.

Confidence: High

---

### H4 — Capital Deployment Regression

A genuine execution defect may remain in one or more buy-budget pathways.

Confidence: Medium

---

## Scope

### Included

- Buy authorization logic
- Pending-sell handling
- Cash gating
- Post-sell rebudgeting interactions
- Buying-power budgeting
- Reserve-cash enforcement
- Operator reason-code generation
- Test expectation validation

### Excluded

- Strategy selection
- Portfolio construction
- Target weights
- Signal generation
- Fractional share sizing
- Broker integration
- Order submission mechanics
- FR-069 telemetry sequencing

---

## Deliverables

### Phase A — Root Cause Analysis

Produce:

outputs/research/fr070_cash_gating_review/

Artifacts should include:

- failing test inventory
- intended behavior matrix
- current behavior matrix
- code-path analysis
- deployment risk assessment
- recommended classification for each failure

---

### Phase B — Governance Review

For each failing test determine:

- Intended behavior
- Current behavior
- Risk level
- Recommended disposition

Classify each failure as:

- TEST_CORRECT_CODE_CORRECT
- TEST_INCORRECT_CODE_CORRECT
- TEST_CORRECT_CODE_INCORRECT
- TEST_INCORRECT_CODE_INCORRECT

---

### Phase C — Remediation Proposal

Only if justified by Phase A findings.

Possible outcomes:

#### Outcome A

Update tests only.

#### Outcome B

Update implementation only.

#### Outcome C

Update both implementation and tests.

#### Outcome D

No changes required.

---

## Success Criteria

The investigation must establish:

1. Whether live execution behavior is correct.
2. Whether buying_power is being applied as intended.
3. Whether affordable buys can proceed after sells.
4. Whether reserve-cash enforcement behaves correctly.
5. Whether pending-sell logic remains necessary.
6. Whether operator reason codes remain accurate.
7. Whether any genuine capital-deployment defect remains.

---

## Deployment Governance

NO EXECUTION-LOGIC CHANGES MAY BE DEPLOYED DURING ACTIVE TRADING HOURS.

Any implementation changes resulting from FR-070 must:

- complete research review
- pass targeted execution validation
- pass operational validation
- receive explicit approval before deployment

Preferred deployment window:

Weekend maintenance window.

---

## Current Status

Remediation implementation is deployed and FR-070 is in observation/monitoring.

The June 12 cash discrepancy was classified as `ARTIFACT_TIMING_FAILURE`, not
failed sell-first rebudgeting. Post-sell rebudgeting correctly used confirmed
sell proceeds; the stale cash miss came from posttrade artifacts captured after
buy submission but before buy fills. The post-buy timing patch now delays final
posttrade artifacts until buy fills reach a terminal observation or timeout.

This execution-artifact timing issue is separate from the resolved Shadow NAV
scorecard incident and from FR-066 canonical portfolio NAV.

No new implementation work is active. Future FR-070 implementation should reopen
only if next-run diagnostics show:

- stale/pre-buy posttrade snapshot
- buy timeout/failure
- unclassified cash drift
- reconciliation/target-attainment contradiction
- achieved cash materially outside tolerance without a classified reason

Next live-run validation gates:

- `buy_phase_status=BUY_PHASE_COMPLETED` or a properly classified terminal
  timeout/fail state
- `posttrade_snapshot_stage=post_buy` when buys fill
- `pending_buy_count=0` when buys fill
- `achieved_cash_weight` within tolerance of `target_cash_weight`
- MCP target-attainment status `OK_TARGET_ATTAINED` or a properly classified
  warning

No production action is required unless those gates produce classified failure
evidence.
