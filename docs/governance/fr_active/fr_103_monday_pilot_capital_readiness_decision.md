# FR-103 Monday Pilot Capital Readiness Decision

Status: `SUPERSEDED_BY_FR104_LEVEL_2_5_EVIDENCE_COLLECTION`
Date: `2026-06-19`
Target trading day: `2026-06-22`
Requested capital: `$1,000 live capital / approximately 10 percent portfolio`
Confidence: `HIGH`
Execution impact: `NON_EXECUTIONAL`
Capital impact: `$0`

## Supersession Note

This decision remains correct for the requested `2026-06-22` Level 3-style
go-live decision: Caerus was not ready for pilot-capital conclusions, scaling,
or production-adjacent deployment.

It is superseded for a narrower question: whether a de minimis, manually
approved, FR-104-controlled Level 2.5 live pilot may collect forward
broker/operational evidence. That narrower evidence-collection path is not a
promotion, not production, not dynamic allocation, and not a claim that FR-100
or FR-101 have passed.

## Executive Summary

Caerus should not deploy Level 3 live pilot capital on `2026-06-22`.

The blocker is not a missing final command or a small implementation gap. The
blocker is that the evidence required by FR-100 and FR-101 does not yet exist:
no decision-grade forward window, no pilot-capital-ready sleeve, no complete
same-run evidence streak, no signed sleeve/cap/rollback packet, and no reviewed
live execution path.

FR-102 live-preflight controls are useful and no-submit behavior validates
correctly, but they do not convert the system into a live-capital-ready platform.

## Decision

`GO_LIVE_BLOCKED_FOR_LEVEL_3_READINESS`

No live cron, production-adjacent live trading, capital scaling, or Level 3
capital deployment should occur from this packet.

This packet does not block a later FR-104 Level 2.5 manual live-pilot evidence
run if that run has separate approval, cap, dry-run, artifact isolation,
broker-truth capture, and rollback controls.

## Blockers

| Severity | Blocker | Evidence | Remediation |
|---|---|---|---|
| `CRITICAL` | No decision-grade forward evidence window. | FR-101 says `FULL_EVIDENCE` is blocked today, with 0 complete same-run bundles and earliest default FR-100 evaluation on `2026-07-21`. | Run and retain the FR-101 daily evidence window. |
| `CRITICAL` | No sleeve is pilot-capital decision-grade. | FR-100 and FR-101 both state no current sleeve is decision-grade for pilot capital. | Produce a named sleeve evidence packet with PIT lineage, performance labels, capacity, drawdown, costs, concentration, and owner approval. |
| `CRITICAL` | No signed pilot packet. | No approved sleeve, account, cap, instruments, rollback, kill criteria, or approver record exists. | Create and manually approve the pilot packet after evidence clears. |
| `CRITICAL` | No approved live execution path. | FR-102 says `TRADING_MODE=live` is still refused and no live executor has been designed, reviewed, or validated. | Build/review a separate live pilot executor only after preflight and approval. |
| `CRITICAL` | Monday VM does not have local FR-102 guardrails deployed. | Read-only VM audit found VM HEAD behind local readiness work, no `core/live_pilot_preflight.py`, and `core/trading_mode.py` limited to `paper` / `live`. | Do not add live credentials to VM; deploy/review guardrails before any live reconsideration. |
| `HIGH` | No live account inspection evidence. | Real live credentials were not provided and must not be added to repo. | Run read-only `live_preflight` with approved external credentials after human approval. |
| `HIGH` | Mode/account artifact segregation remains incomplete. | FR-102 lists execution, broker, recon, reliability, target-attainment, and performance artifacts that need explicit mode/account partitioning. | Add partitioning before API key setup. |
| `HIGH` | Market-order cap enforcement is incomplete for any future live executor. | FR-102 submission guard enforces cap when order notional is supplied; current market-order submit calls do not provide a notional estimate. | Require live-bound market orders to carry pretrade notional or use capped limit/notional order semantics before live execution is designed. |
| `MEDIUM` | Auditability of the current decision packet is local-only until committed/deployed. | Several FR-100/101/103 and generated `outputs/` artifacts are local worktree artifacts. | Commit/push reviewed decision artifacts if they are intended to govern Monday operations. |

## Data Trust Assessment

Classification: `FAIL`

Current data trust is sufficient for research triage, not for Monday live
capital. FR-077 says no sleeve is decision-grade for pilot capital and FR-101
says current local run roots do not contain complete same-run evidence bundles.
Performance and operational claims still require explicit evidence labels and
run-linked broker truth.

## Model Trust Assessment

Classification: `FAIL`

No sleeve should receive live capital on `2026-06-22`.

Current sleeve posture:

- At this decision date, the PAPER control was `Polaris`; it was not a live-pilot nomination.
- `Orion`: shadow/redundancy triage only; matched PIT evidence does not prove a decisive lead.
- `Lyra`: not independently promotion-ready; any apparent watch-list advantage is low-confidence.
- `Cassiopeia`: research only; 13D campaign dedupe weakened the signal and Form 4 evidence remains pilot/research-grade.

The recommended live sleeve for Monday is `NONE`.

## Operational Trust Assessment

Classification: `FAIL_FOR_LIVE_CAPITAL`

FR-074 reliability reporting and FR-102 preflight controls improve observability,
but FR-101 remains decisive: complete same-run evidence bundles, run-linked
reconciliation, target-attainment, broker terminal order/fill truth, and
capital-eligible reliability rows are not yet proven.

Monday paper operation is `CONDITIONAL` for observation only. Read-only VM audit
found the scheduled path is forced to paper mode, but the latest run was a
market-closed `NO_ACTION` run for Juneteenth and did not validate a buy-capable
cycle.

Operational paper work may continue. Live capital should not.

## Infrastructure Trust Assessment

Classification: `CONDITIONAL_FOR_LOCAL_PREFLIGHT`, `FAIL_FOR_VM_LIVE_EXECUTION`

The live-preflight no-submit guard was validated with:

```text
TRADING_MODE=live_preflight
ALPACA_PAPER=0
ALPACA_BASE_URL=https://api.alpaca.markets
CAERUS_ALLOW_LIVE_TRADING=approve_live_pilot
CAERUS_LIVE_CAPITAL_CAP_USD=1000
CAERUS_APPROVED_PILOT_SLEEVE_ID=UNAPPROVED_MONDAY_TEST
```

Result:

```text
status=BLOCKED
reason_code=live_preflight_never_submits_orders
live_orders_allowed=false
orders_submitted=0
```

This supports no-submit preflight safety. It does not support go-live.

Deployment caveat: read-only VM audit found Monday cron is paper-forced
(`MODE=paper`, `TRADING_MODE=paper`, `ALPACA_PAPER=1`) and therefore low
accidental-live risk on the scheduled path. Manual/direct execution remains
high risk if live credentials are injected because the VM does not yet have the
local FR-102 submission guardrails.

Important caveat: preflight guardrails are not a substitute for a reviewed live
executor. Before any future live executor exists, market orders must carry a
pretrade notional estimate or use capped order semantics so the live capital cap
can be enforced before submission.

## Pilot Capital Recommendation

Recommended Monday pilot amount: `$0`

| Amount | Decision |
|---:|---|
| `$100` | `BLOCKED` |
| `$250` | `BLOCKED` |
| `$500` | `BLOCKED` |
| `$1,000` | `BLOCKED` |

The smallest Level 3 live trade would still violate the evidence and approval
gates in this packet. A later Level 2.5 evidence-collection trade must be
evaluated under FR-104, not this superseded Monday readiness packet.

## Rollback Plan

Because the decision is blocked, rollback is prevention:

- keep live capital at `$0`;
- keep paper execution as the only order-submission workflow;
- do not add live credentials to repo;
- do not change cron;
- do not change strategies, sizing, allocation, or promotion state;
- remove any externally staged live credentials unless a human-approved
  read-only preflight is actively being run.

## Monitoring Plan

Continue monitoring paper/research operation only:

- daily evidence coverage;
- execution reliability classification and score;
- run-linked reconciliation;
- target-attainment;
- operator summary top failure reason;
- broker terminal order/fill evidence when paper orders occur;
- FR-101 window progress.

## Remaining Risks

1. Existing positive reliability labels can still be misread without evidence
   coverage.
2. Legacy run roots cannot reconstruct broker-authoritative truth.
3. A future live credential setup could blur paper/live artifacts unless
   mode/account partitioning is completed first.
4. Sleeve evidence remains research/shadow/paper level, not live-capital level.
5. A manual desire to "just try `$100`" would test infrastructure before the
   investment and operational evidence are ready.
6. Local decision artifacts are not operationally authoritative until reviewed,
   committed, and deployed to the environment operators actually use.
7. VM scheduled paper safety could be bypassed by manual direct execution with
   live credentials before FR-102 is deployed there.

## Required Human Actions

1. Accept `GO_LIVE_BLOCKED_FOR_LEVEL_3_READINESS` for `2026-06-22`.
2. Keep live capital at `$0`.
3. Keep VM cron paper-forced; do not add live credentials or set
   `ALPACA_PAPER=0`.
4. Decide whether to start or continue the FR-101 forward evidence window.
5. Nominate exactly one candidate sleeve only after model evidence is
   decision-grade.
6. Approve a signed pilot packet before any live credential setup.
7. Deploy/review FR-102 guardrails on VM before any future live preflight.
8. Review the live preflight packet before any future live executor is built.

## Supporting Artifacts

- `docs/governance/fr_active/fr_100_capital_readiness_framework.md`
- `docs/governance/fr_active/fr_101_decision_grade_evidence_window_program.md`
- `docs/governance/fr_active/fr_102_pilot_capital_infrastructure_readiness.md`
- `outputs/research/monday_pilot_readiness/live_pilot_preflight_packet.md`
- `outputs/research/monday_pilot_readiness/pilot_capital_deployment_plan.md`
