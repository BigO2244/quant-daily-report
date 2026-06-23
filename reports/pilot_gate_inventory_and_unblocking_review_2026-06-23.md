# Pilot Gate Inventory And Unblocking Review

Date: 2026-06-23
Scope: Governance review / program unblocking / architecture
Runtime impact: none

## 1. Executive Summary

Final recommendation: PILOT SHOULD CONTINUE.

This recommendation applies only to FR-104 Level 2.5 pilot evidence collection:

- tightly capped;
- manually approved;
- dry-run first;
- artifact-isolated;
- broker-truth captured;
- no cron;
- no dynamic allocation;
- no promotion, scaling, or production claim.

FR-068 remains incomplete and correctly blocks decision-grade historical replay
conclusions, allocator research conclusions, sleeve promotion, and production
promotion. It should not, by itself, block forward evidence collection through a
segregated pilot whose purpose is to create the missing real-world broker and
operational evidence.

## 2. Gate Inventory

| Gate | Type | Current owner/source | Correct blocker for conclusions/promotions? | Should block forward pilot evidence collection? | Assessment |
|---|---|---|---|---|---|
| PIT historical replay / FR-068 | Research Gate / Promotion Gate | FR-068, replay certification | Yes | No, not by itself | Correct for historical conclusions; inappropriate as sole pilot blocker |
| Sleeve Phase C readiness | Promotion Gate | FR-069 Phase C | Yes | No, unless pilot attempts to promote/activate a new sleeve | Correct blocker for lifecycle movement |
| Sleeve promotion evidence | Promotion Gate | FR-082 | Yes | No, unless pilot result is used as promotion evidence | Correct blocker |
| Decision-grade evidence window | Pilot Gate / Production Gate | FR-101 | Yes for Level 3 readiness/scaling | No for Level 2.5 evidence collection | Too broad if treated as pilot stop-work |
| Capital readiness framework | Pilot Gate / Production Gate | FR-100 | Yes for Level 3+ | No for Level 2.5 evidence collection | Needed clarification |
| Artifact coverage matrix | Execution Gate / Pilot Gate | FR-078 | Yes for readiness claims | No, but gaps must be recorded as evidence defects | Correct blocker for claims, not collection |
| Operational reliability | Execution Gate / Pilot Gate | FR-074/FR-083 | Yes when RED/FAIL or missing operator action | Yes if live-pilot path itself violates controls | Correct execution blocker |
| Broker/model reconciliation | Execution Gate / Pilot Gate | FR-070/FR-080 | Yes when unresolved | Yes if live-pilot order state is unresolved and no operator action exists | Correct execution blocker |
| Live preflight/infrastructure | Execution Gate / Pilot Gate | FR-102 | Yes for unsafe live path | Yes if FR-104 controls cannot pass | Correct blocker |
| Manual live-pilot controls | Pilot Gate | FR-104 | Yes if any control fails | Yes | Correct pilot evidence gate |
| Monday readiness decision | Pilot Gate | FR-103 | Yes for 2026-06-22 Level 3 go-live | No for later FR-104 Level 2.5 run | Correct historically, superseded for narrower path |
| Production promotion | Production Gate | Doctrine, FR-100, FR-069/082 | Yes | Not applicable | Correct blocker |

## 3. Blocking Analysis

### Correctly Blocking Research Conclusions Or Promotions

These gates are doctrinally justified and should remain strict:

- FR-068 blocks decision-grade historical replay conclusions and any promotion
  argument that depends on historical large-cap reconstruction.
- FR-069 and FR-082 block sleeve lifecycle movement without owner-approved,
  decision-grade evidence.
- FR-100 Level 3, Level 4, and Level 5 gates block capital scaling and
  production-readiness claims.
- FR-101 blocks using paper/live observations as decision-grade readiness
  evidence until complete same-run evidence exists.
- FR-074/FR-078/FR-080/FR-083 block clean reliability/readiness labels when
  artifacts, reconciliation, or reliability evidence are incomplete.

### Incorrectly Blocking Forward Evidence Collection

The following are inappropriate if used as global stop-work for a capped,
manual, evidence-collection pilot:

- FR-068 historical replay incompleteness.
- FR-101 incomplete 20-run FULL_EVIDENCE window.
- FR-077 statement that no sleeve is pilot-capital decision-grade.
- FR-103 Monday `GO_LIVE_BLOCKED` decision for the earlier Level 3-style
  deployment question.

Those gates should downgrade labels and block conclusions, scaling, production,
and promotion. They should not prevent a Level 2.5 run whose purpose is to
create forward evidence, provided the FR-104 live-pilot controls pass.

## 4. Recommended Changes

1. Add a Level 2.5 Pilot Evidence Collection lane between Paper Trusted and
   Pilot Capital Ready.
2. Clarify that Level 2.5 is not production, not promotion, not scaling, and not
   evidence of alpha.
3. Clarify that FR-068 incomplete historical replay blocks conclusions and
   promotions, not forward evidence collection by itself.
4. Preserve all execution/risk controls:
   - explicit approval;
   - cap;
   - dry-run first;
   - no cron;
   - no dynamic allocation;
   - artifact isolation;
   - broker truth;
   - rollback/kill path.
5. Mark FR-103 as superseded for Level 2.5, while preserving its correctness for
   the earlier Level 3-style go-live decision.

## 5. Implemented Changes

Updated:

- `docs/governance/caerus_investment_doctrine.md`
- `docs/governance/fr_active/fr_100_capital_readiness_framework.md`
- `docs/governance/fr_active/fr_101_decision_grade_evidence_window_program.md`
- `docs/governance/fr_active/fr_102_pilot_capital_infrastructure_readiness.md`
- `docs/governance/fr_active/fr_103_monday_pilot_capital_readiness_decision.md`
- `docs/governance/fr_active/fr_104_live_pilot_unlock_program.md`
- `docs/governance/fr_registry.md`
- `docs/governance/fr_active_backlog.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`

No production, paper, broker, execution, scheduler, allocator, or risk-control
behavior was changed.

## 6. Updated Governance Status

| Area | Updated status |
|---|---|
| Research conclusions | FR-068 incomplete; historical replay conclusions remain blocked |
| Sleeve promotion | Still blocked unless decision-grade promotion evidence passes |
| Production | Still blocked |
| Level 3 pilot-capital readiness/scaling | Still blocked |
| Level 2.5 pilot evidence collection | May continue under FR-104 controls |
| Execution safety | Not weakened; FR-104 remains the live-pilot gate |

## 7. Evidence For Final Recommendation

Pilot should continue because:

1. Doctrine defines Pilot Capital as validating execution, broker behavior,
   operational processes, and real-world performance.
2. FR-068 incompleteness affects historical replay and promotion-grade
   conclusions, not the value of collecting forward broker evidence.
3. FR-104 provides a segregated, capped, manual path with dry-run-first,
   artifact isolation, broker-truth capture, and no cron.
4. Stopping Level 2.5 evidence collection would delay the very evidence that
   FR-100/101 require before Level 3 decisions can become decision-grade.

Pilot should pause only if any FR-104 control fails, including missing approval,
cap breach, cron ambiguity, missing dry-run, broker/reconciliation uncertainty,
unresolved order state without operator action, artifact mixing, or rollback
ambiguity.

Final recommendation: PILOT SHOULD CONTINUE.
