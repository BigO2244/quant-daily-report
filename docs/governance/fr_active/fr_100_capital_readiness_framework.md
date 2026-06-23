# FR-100 Capital Readiness Framework And Trust Model

Status: DRAFT_GOVERNANCE_FRAMEWORK_NOT_CAPITAL_GATE
Owner: Caerus Research Program
Date: 2026-06-19
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL

This framework changes no trading behavior, allocation behavior, broker
behavior, strategy selection, promotion state, cron, or runtime schedule. It is
not an approval to deploy real capital. It defines the evidence model Caerus
must satisfy before pilot capital or scaled capital can be approved.

## Executive Summary

Caerus is trying to convert a paper-traded research platform into a
capital-ready investment system without mistaking diagnostics, shadow
performance, or incomplete artifacts for proof.

The north star is:

> Draw pilot-capital conclusions and scale capital only when data, models, and
> operations are decision-grade through evidence, not assumption.

Recent FR work has improved the system, but it also exposed the central
problem. Caerus can now generate useful research artifacts, operational
invariants, reliability classifications, and data-trust reviews. Those artifacts
do not yet prove that Caerus is ready for pilot capital.

Current answer:

| Question | Current assessment | Reason |
|---|---|---|
| Research-direction ready? | CONDITIONAL | Strong enough to prioritize research and downgrade weak claims, but several sources remain partial or low coverage. |
| Paper-trading ready? | CONDITIONAL | Paper operation can continue as observation, but complete same-run execution evidence is not retained locally. |
| Pilot evidence collection ready? | CONDITIONAL | A tightly capped, manually approved, segregated live-pilot path may collect forward broker/operational evidence if FR-104 controls pass. |
| Pilot-capital conclusion/scaling ready? | NO | FR-077 says no sleeve is pilot-capital decision-grade; local execution evidence has zero complete run bundles. |
| Production ready? | NO | No sleeve has full production promotion evidence, operational coverage, capital controls, and owner approval. |

FR-100 organizes all current and future FRs into four trust pillars:

1. Data Trust
2. Model Trust
3. Operational Trust
4. Capital Readiness

The finish line is not a single GREEN score. The finish line is simultaneous
evidence:

- decision-grade data lineage and freshness;
- decision-grade model evidence for the exact sleeve and capital level;
- same-run operational proof from intent through broker terminal state;
- explicit Brett/CIO approval for the relevant capital level, cap, rollback
  path, and monitoring plan.

Until those are true together, Caerus remains ineligible for capital scaling,
production promotion, or investment conclusions from live results.

This does not prohibit a separate, tightly capped, manually approved
pilot-evidence mode whose explicit purpose is to collect forward broker,
reconciliation, artifact-retention, and operational evidence before perfect
certainty exists. That mode must remain isolated, small, reversible, and
non-promotional.

## North Star

Caerus should deploy pilot capital only when:

1. the data used for the decision is point-in-time where required, fresh,
   reproducible, and explicitly classified;
2. the model evidence is strong enough for the requested lifecycle transition;
3. the execution pipeline can prove what it intended, submitted, accepted,
   filled, reconciled, and attained from same-run artifacts;
4. the capital exposure is bounded by a written cap, risk policy, rollback
   plan, and operator review process;
5. the approval authority is explicit and recorded.

No diagnostic label by itself is sufficient. `RELIABILITY_GREEN`, dashboard
`HIGH`, `PROMOTION_ELIGIBLE`, or a validated static evidence envelope are
inputs to review, not capital approval.

## Trust Pillar 1: Data Trust

### Definition

Data Trust means a research, reporting, promotion, execution, or capital claim
is allowed only when the underlying data has explicit source lineage,
point-in-time validity where required, freshness, identity resolution,
benchmark and return convention, artifact coverage, and a scoped trust class.

### Current State

FR-077 defines the current trust taxonomy:

| Trust class | Current meaning |
|---|---|
| `TRUSTED` | Strong for a narrow purpose when joined correctly. |
| `PARTIAL` | Useful, but has staleness, missingness, survivorship, or convention risk. |
| `LOW_COVERAGE` | Mechanics work, but sample/date/artifact coverage is too thin. |
| `NOT_DECISION_GRADE` | Cannot support operational, promotion, or live-performance truth claims. |

Current source assessment:

| Source family | Current trust | Capital-readiness implication |
|---|---|---|
| PIT liquidity / ADV | `TRUSTED` narrowly | Can support liquidity/capacity checks when joined to the right PIT candidate set. |
| Price and benchmark data | `PARTIAL` | Requires source, adjustment, return convention, max date, and missingness labels before promotion use. |
| Security master / universe | `PARTIAL` | PIT research universe is useful; execution security-master state needs reconciliation. |
| CIK / ticker / EDGAR identity | `PARTIAL` | Historical joins must route through PIT security identity, not static ticker maps alone. |
| EDGAR 13D | `PARTIAL` | PIT timing is credible; strategy evidence is not decision-grade. |
| EDGAR Form 4 | `LOW_COVERAGE` | Pilot builder artifact only; not a full-window promotion artifact. |
| Broker snapshots | `PARTIAL` | Broker-authoritative only when linked to run ID, trade date, and recon artifacts. |
| Execution run evidence | `NOT_DECISION_GRADE` | Local run roots cannot reconstruct exact broker/model truth. |
| Reliability artifacts | `NOT_DECISION_GRADE` locally | Historical GREEN replay is classifier telemetry over low evidence coverage. |

### Target State

Data Trust becomes a mandatory evidence contract consumed by promotion,
CIO/reporting, research packets, reliability, and execution review.

Target properties:

- every claim declares a trust class and allowed use;
- every performance artifact declares price source, benchmark source, return
  convention, max source date, and missingness;
- every promotion artifact declares PIT universe method, universe hash, holdout
  status, cost model, drawdown, turnover, capacity, and known bias risks;
- every execution claim is linked to a same-run evidence bundle;
- missing or stale artifacts downgrade claims to `RESEARCH_ONLY`,
  `LOW_COVERAGE`, or `NOT_DECISION_GRADE`.

### Required Evidence

- Machine-readable `decision_grade_evidence_contract.json`.
- Price and benchmark lineage fields on every performance artifact.
- Fresh price hydration through the latest completed trading day.
- PIT identity graph joining ticker, CIK, security ID, symbol history, aliases,
  and active-date validity.
- Security-master refresh/reconciliation that clears execution resolver gaps.
- Full-window EDGAR artifacts with source errors, missingness, campaign or
  purchase cohorts, holdout exclusion, and matched benchmark returns.
- Run-retention evidence in the same run root: payload, operator summary,
  execution results, broker orders/fills, posttrade recon, target attainment,
  execution integrity, and reliability report.
- Broker-authoritative portfolio/NAV history refreshed beyond stale local
  windows.

### Gating Criteria

- No claim can be decision-grade unless it declares trust class and allowed use.
- `RELIABILITY_GREEN` is promotion-usable only with FULL evidence coverage and
  valid trade-date lineage.
- Broker terminal order/fill truth outranks model-reported execution status.
- Standalone broker, recon, or target files cannot prove a run unless linked by
  `run_id` and `trade_date`.
- Missing or stale data fails closed for promotion and capital-readiness claims.

### Related FRs

FR-068, FR-069, FR-074, FR-076, FR-077, proposed FR-078, proposed FR-079,
proposed FR-080, proposed FR-083.

### Open Gaps

1. FR-077 is not yet wired into registry/backlog governance.
2. Price and benchmark lineage are inconsistent across surfaces.
3. Execution security-master evidence is incomplete locally.
4. Reliability lacks native artifact-completeness gating.
5. Historical run roots cannot prove broker-authoritative execution truth.
6. PIT sector/SIC mapping and matched Polaris daily returns remain missing for
   Cassiopeia segmentation.

## Trust Pillar 2: Model Trust

### Definition

Model Trust means a sleeve or model claim can support lifecycle movement,
capital routing, promotion, retirement, or operator-facing confidence only when
its evidence lineage, PIT controls, benchmark convention, differentiation,
capacity, execution coverage, and owner approval support that exact claim.

### Current State

| Sleeve | Current model trust assessment |
|---|---|
| Polaris | Paper baseline/control. PIT rebaseline materially downgraded legacy risk claims; parity harness and migration evidence remain incomplete. |
| Orion | Shadow only. Useful for Orion/Lyra redundancy triage, not promotion. |
| Lyra | Shadow only. Not independently promotion-ready; should remain in Orion/Lyra disposition work or a future approved redeployment thesis. |
| Cassiopeia | Research only. 13D tape is PIT-safe but campaign dedupe weakened returns; Form 4 is pilot only. |
| Phoenix | Research only and not viable in current Phase B candidate because capacity failed the 5 percent ADV policy. |
| Cygnus | Research/shelved. v0 shelved; v1 blocked on PIT consensus/EPS-surprise data. |
| Argo | Research advisory layer, not a capital sleeve or allocation switch. |

No sleeve is decision-grade for pilot capital today.

### Target State

Every sleeve lifecycle transition is backed by a standardized evidence packet:

- manifest row and strategy identity;
- PIT universe and price source;
- universe snapshot hash;
- holdout handling;
- benchmark source and return convention;
- alpha, drawdown, hit rate, turnover, costs, capacity, concentration, and
  correlation/active-share evidence;
- operational evidence required for the requested level;
- explicit owner approval.

### Lifecycle Gates

| Gate | Evidence required |
|---|---|
| Research | Manifest row, thesis/spec, data requirements, known bias risks, non-executional impact, artifact paths, PIT feasibility, and no capital behavior. |
| Shadow | Owner-approved transition, validated evidence envelope, PIT universe, universe hash, holdout excluded, benchmark convention, costs, drawdown, turnover, capacity, correlation, and overlap. |
| Paper | Separate owner-approved FR, stable shadow window, execution-risk review, target-attainment expectations, complete paper run evidence, and no unresolved allocation or risk ambiguity. |
| Pilot Capital | Live-readiness packet, capital cap, broker/reconciliation safety, rollback plan, FULL run-bundle evidence, and FR-070/FR-074 operational coverage. |
| Production | Long-window evidence, monitoring plan, runbook, incident response, stable correlations, artifact completeness, and explicit promotion record. |
| Retirement | Owner-approved disposition, redundancy/failure evidence, lineage note, artifact-retention plan, and no name reuse without recorded approval. |

### Gating Criteria

- Legacy current-universe backtests are lineage-only and cannot drive promotion.
- Model evidence must be evaluated against the requested lifecycle level, not a
  generic "good result" standard.
- Shadow and paper metrics do not imply real-capital readiness.
- Capacity, liquidity, slippage, concentration, and drawdown/tail risk must be
  hard-reviewed before any pilot-capital approval.
- Static evidence-envelope validation is necessary but insufficient; it does
  not prove metrics, fills, liquidity, or benchmark lineage.

### Related FRs

FR-028, FR-037, FR-038, FR-050, FR-051, FR-052, FR-053, FR-063, FR-068,
FR-069, FR-077, proposed FR-079, proposed FR-082.

### Open Gaps

1. Orion/Lyra need FR-069 evidence envelopes and owner disposition thresholds.
2. Cassiopeia needs richer Form 4 cohorts, PIT sector/SIC, matched Polaris
   daily returns, and source-backed campaign consolidation.
3. Strategy registry promotion eligibility for Orion/Lyra is stronger than
   governance evidence supports.
4. Argo rankings must remain research-priority signals, not hidden allocation
   logic.
5. No unified model evidence contract exists across sleeves.

## Trust Pillar 3: Operational Trust

### Definition

Operational Trust means Caerus can explain, from same-run artifacts:

- what it intended to trade;
- what reached the broker;
- what the broker accepted, rejected, filled, or left unresolved;
- whether model state reconciled to broker state;
- whether target exposure and cash were actually attained;
- what the operator must do next.

This is broader than `OK_RECONCILED`. Reconciliation can prove broker/model
agreement without proving target attainment or complete execution.

### Current State

FR-074 provides observe-first reliability reporting. FR-076 replayed the last
available historical run roots and showed all GREEN under FR-074-native scoring,
but every replayed run had LOW evidence coverage. FR-077 then found local run
evidence is not decision-grade:

- 33 run directories;
- 13 payloads;
- 20 operator summaries;
- 1 `execution_results.json`;
- 0 in-run posttrade reconciliation artifacts;
- 0 in-run target-attainment artifacts;
- 0 execution-integrity artifacts;
- 0 reliability reports;
- 0 decision-grade execution bundles.

### Target State

Every paper or live execution run that is used for readiness must retain a
complete same-run evidence bundle.

Required artifacts:

- `execution_payload.json`;
- `operator_summary.json`;
- `execution_results.json`;
- `audit/execution_integrity.json`;
- `audit/execution_target_attainment_<TRADE_DATE>.json`;
- `audit/execution_reliability_report_<TRADE_DATE>.json`;
- broker order/fill evidence;
- posttrade account and position snapshots;
- `broker/recon_posttrade_<TRADE_DATE>.json`;
- `broker/post_sell_rebudget_<TRADE_DATE>.json` when sells are present;
- `audit/sleeve_numeric_trace_*` when numeric invalidation occurs;
- `outputs/reliability/reliability_history.json`;
- `outputs/reliability/reliability_readiness.json`;
- date-aligned precompute contract and planned payload when exact planned
  execution is expected.

### Reliability Readiness Levels

| Status | Meaning | Capital-readiness use |
|---|---|---|
| `RELIABILITY_GREEN + FULL_EVIDENCE` | Score >= 95, no FAIL, complete same-run evidence, valid trade-date lineage. | Required but not sufficient for promotion or capital. |
| `RELIABILITY_GREEN + LOW_EVIDENCE` | No invariant fired, but artifacts are incomplete. | Observe-only. Must not support promotion or capital. |
| `RELIABILITY_YELLOW` | WARN state or score 80-94 without critical fail. | Blocks scaling; requires operator review. |
| `RELIABILITY_RED` | Any FAIL invariant or score below 80. | Blocks promotion, capital readiness, and report-clean labels. |

### Current Fail-Closed Paths

Current hard or fail-closed behavior includes:

- pretrade reconciliation `BLOCK`;
- position mismatch;
- malformed or missing planned payload;
- execution lock collision;
- asset/security-master hard failure;
- nonempty planned payload dropping to zero execution;
- submitted orders with zero broker acceptance;
- missing terminal reason for failed/halted/skipped/no-action states;
- buy-only continuation containing sells.

Current observe-only surfaces include:

- FR-074 reliability report;
- target-attainment warning;
- broker preflight/PDT warnings;
- cash/equity drift with clean positions;
- dashboard/live readiness diagnostics.

### Gating Criteria

- Missing same-run evidence bundle blocks capital readiness.
- `RELIABILITY_RED` or any FAIL invariant blocks promotion and capital
  readiness.
- Non-clean posttrade reconciliation blocks readiness.
- `OK_RECONCILED` plus target-attainment miss is not clean.
- Accepted-only activity cannot be labeled clean execution.
- Unresolved sell terminality blocks buy continuation and final clean reporting.
- Terminal `NO_ACTION`, `HALTED`, `FAILED`, or `PARTIAL` states require a
  non-empty reason and operator action.

### Related FRs

FR-070, HOTFIX-2026-06-15-FR070, FR-073, FR-074, FR-075, FR-076, FR-077,
proposed FR-078, proposed FR-080, proposed FR-081, proposed FR-082.

### Open Gaps

1. No machine-readable controls registry exists yet.
2. Reliability lacks native artifact-completeness gating.
3. Reliability is not unified with dashboard/promotion readiness.
4. Runtime artifact retention is documented but not enforced.
5. Sell-suppression reason codes are less complete than buy-suppression reason
   codes.
6. Local retained run roots cannot prove current operational trust.

## Trust Pillar 4: Capital Readiness

### Definition

Capital Readiness means Caerus has satisfied Data Trust, Model Trust, and
Operational Trust for a specific sleeve, account, capital cap, instrument set,
and monitoring plan, and Brett/CIO has explicitly approved the transition.

Capital readiness is not a system-wide vibe. It is a scoped approval:

- sleeve or model;
- account;
- allowed instruments;
- exact capital cap;
- position and turnover limits;
- liquidity and slippage assumptions;
- rollback plan;
- monitoring and review cadence;
- expiration or re-review condition.

### Capital Readiness Levels

| Level | Objective | Permitted behavior / capital | Required evidence | Disqualifiers | Approval and rollback |
|---|---|---|---|---|---|
| LEVEL 0 - Research Only | Test thesis and evidence viability. | Docs, offline research artifacts, static validators. Capital: $0. No shadow, paper, broker, allocation, cron, or runtime behavior. | Manifest/evidence envelope, PIT data plan, benchmark, holdout policy, cost model, bias risks, blockers, non-goals. | Missing PIT lineage, look-ahead/survivorship risk, missing manifest, unclear strategy identity, runtime mutation. | Research owner can prepare; Brett/CIO required to advance. Rollback: mark `NON_DECISION_GRADE`, `BLOCKED`, or `SHELVED`. |
| LEVEL 1 - Shadow Trusted | Observe signals without capital. | Non-blocking shadow artifacts, comparisons, readiness reports. Capital: $0. No broker orders or target routing. | CIO-approved transition, decision-grade PIT evidence, universe hash, holdout excluded, benchmark/metrics/reason codes, freshness checks. | Short observation window, unresolved bias, weak differentiation, stale inputs, missing parity evidence, execution coupling. | Brett/CIO approves entry. Rollback: disable/ignore shadow outputs and return to Level 0. |
| LEVEL 2 - Paper Trusted | Prove realistic simulated execution and operations. | Paper target generation and paper-broker execution only. Real capital: $0. | Paper-readiness packet, execution-risk review, target-attainment expectations, pre/post reconciliation, run-level reliability artifacts, broker/paper fill evidence, capacity/cost checks. | Unresolved execution, allocation, broker, reconciliation, target-attainment, artifact completeness, or risk-control ambiguity. Reliability RED/FAIL blocks. | Brett/CIO approves paper allocation; operator reviews execution readiness. Rollback: zero paper allocation and preserve artifacts. |
| LEVEL 2.5 - Pilot Evidence Collection | Collect forward live broker/operational evidence under de minimis exposure. | Manual live-pilot path only; tightly capped; no cron; no dynamic allocation; no promotion or scaling claims. | FR-104 controls, signed cap/account/sleeve approval, dry-run first, isolated artifacts, broker truth capture, explicit operator go/no-go, rollback/kill plan. | Missing approval, cap breach, live/paper artifact mixing, cron ambiguity, missing broker truth, unresolved order, broker reject, missing operator action, any risk-control bypass. | Brett/CIO approves each run or short window. Rollback: kill switch, cancel/flatten by manual approval, preserve artifacts, return to paper. |
| LEVEL 3 - Pilot Capital Ready | First real-dollar proof under tight cap. | Limited live capital under written cap only. | Live-readiness packet, exact capital cap, broker safety review, rollback plan, production monitoring, complete run bundles, clean paper observation window. | Missing broker/recon controls, unclear cash/flatten plan, stale artifacts, capacity failure, unresolved incident, PDT/account risk, unapproved runtime drift. | Explicit Brett/CIO approval plus operator manual go/no-go. Rollback: halt scaling, cancel/flatten or route to cash, demote to Level 2, preserve broker evidence. |
| LEVEL 4 - Limited Capital | Increase from pilot to bounded material capital. | Incremental capital steps only; no open-ended dynamic allocation. | Clean pilot window at current cap, realized slippage/cost vs model, target attainment, clean recon, reliability streak, capacity at proposed dollars, incident-free monitoring. | Reliability RED/FAIL/YELLOW trend, cash drift, broker reject, unresolved fills, cost/capacity breach, model decay, data restatement, broker/model mismatch. | Brett/CIO approves each cap step. Rollback: return to prior cap or Level 3/2 and reset readiness clock. |
| LEVEL 5 - Scaled Capital | Normal production allocation. | Approved live capital at scale; eligible for governed dynamic allocation. | Full production promotion packet, long-window model evidence, full operational coverage, monitoring/runbook/incident response, scaled capacity and tail-risk review. | Missing long-window evidence, unstable correlations, unclassified operational risk, stale lineage, unbounded drawdown/cost/capacity risk, unauthorized model/risk change. | Brett/CIO production promotion record required. Rollback: freeze dynamic allocation, reduce cap, route to cash/baseline, demote level, or retire with lineage note. |

## FR Mapping

| FR | Pillar(s) | Classification | Capital impact |
|---|---|---|---|
| FR-063 | Model Trust | `RESEARCH_ONLY`, `RETIRE/MERGE` watch | Blocks Orion/Lyra disposition, not pilot capital globally. |
| FR-064 | Capital Readiness | `ENHANCER`, `RESEARCH_ONLY` | Future scaling research; defer until trust gates are stronger. |
| FR-065 | Operational Trust, Capital Readiness | `ENHANCER` | Operator-surface aid, not pilot blocker. |
| FR-066 | Data Trust, Operational Trust | `ENABLER` | Supports confidence; continue observing. |
| FR-067 | Data Trust | `ENABLER`, effectively closed | Merge lineage into FR-068. |
| FR-068 | Data Trust, Model Trust | `ENABLER`, partial blocker | PIT foundation; legacy evidence remains non-decision-grade. Blocks promotion/production conclusions that depend on historical replay; does not globally block capped pilot evidence collection. |
| FR-069 | Model Trust, Capital Readiness | `ENABLER`, `RESEARCH_ONLY` | Blocks new sleeve lifecycle moves until owner-approved Phase C gates. |
| FR-070 + HOTFIX | Operational Trust | `BLOCKER` | Primary pilot-capital blocker until a buy-capable run proves terminal states. |
| FR-071 | Capital Readiness | `ENHANCER` | Governance alignment; opportunistic. |
| FR-072 | Capital Readiness | `ENHANCER` | Prevents governance drift; no runtime authority. |
| FR-073 | Operational Trust | `ENABLER` | Blocks only if invalid-sleeve evidence recurs or traces are missing. |
| FR-074 | Operational Trust | `BLOCKER`, `ENABLER` | Needs artifact-completeness gate before GREEN supports promotion. |
| FR-075 | Operational Trust | `ENABLER`, unregistered | Control inventory; register or fold into FR-074/FR-081. |
| FR-076 | Operational Trust | `RESEARCH_ONLY`, blocker finding | Shows historical GREEN is low coverage, not promotion-grade. |
| FR-077 | Data Trust, Model Trust, Operational Trust | `BLOCKER`, `RESEARCH_ONLY` | Says no sleeve is pilot-capital decision-grade today. Blocks conclusions/scaling, not a separately approved evidence-collection pilot. |
| Proposed FR-078 | Operational Trust, Data Trust | `BLOCKER` | Artifact Coverage Matrix and Required Evidence Gate for promotion/scaling claims. |
| Proposed FR-079 | Data Trust, Model Trust | `BLOCKER` | Historical Performance Rebuild With Evidence Labels. |
| Proposed FR-080 | Operational Trust | `BLOCKER` | Broker/Model Reconciliation Backfill and run-retention validator. |
| Proposed FR-081 | Data Trust, Model Trust | `BLOCKER` | PIT Benchmark and Universe Integrity Audit. |
| Proposed FR-082 | Model Trust | `BLOCKER` | Sleeve Promotion Evidence Gate v1. |
| Proposed FR-083 | Operational Trust | `BLOCKER` | Reliability Coverage Hardening. |
| Proposed FR-084 | Capital Readiness | `BLOCKER` | Pilot Capital Readiness Checklist and approval packet for Level 3 readiness; Level 2.5 requires the narrower FR-104 approval packet. |

## Current Caerus Readiness Assessment

| Readiness question | Classification | Evidence |
|---|---|---|
| Research-direction ready? | CONDITIONAL | Research artifacts can rank work and falsify claims, but several data sources remain partial and some ranking prose is stale. |
| Paper-trading ready? | CONDITIONAL | Paper operation can continue for observation, but readiness claims require same-run evidence bundles that are not locally complete. |
| Pilot evidence collection ready? | CONDITIONAL | Allowed only through the separate FR-104 manual live-pilot path with cap, approval, dry-run, artifact isolation, broker truth, and no cron. |
| Pilot-capital conclusion/scaling ready? | NO | No sleeve is decision-grade for pilot capital conclusions; local execution evidence is not decision-grade; FR-070/074/077 gates remain open. |
| Production ready? | NO | No sleeve has production promotion packet, long-window evidence, full operational coverage, scaled capacity review, and explicit approval. |

## Pilot Capital Gate

Level 3 pilot-capital readiness and any capital scaling are not allowed until
all requirements below pass for a specific sleeve and capital cap.

Level 2.5 pilot evidence collection is a narrower exception. It may continue
only to gather forward evidence under FR-104-style controls and must not be
used as proof of alpha, promotion readiness, production readiness, or capital
scaling until the Level 3 gate passes.

### Minimum First-Dollar Requirements

1. Level 0 through Level 2 complete with no unresolved disqualifier.
2. Brett/CIO-approved live-readiness packet naming sleeve, model version,
   account, instruments, exact capital cap, position caps, turnover limits,
   liquidity/ADV policy, cash policy, kill criteria, and rollback plan.
3. Decision-grade data window:
   - PIT universe and identity mapping;
   - universe snapshot hash;
   - price and benchmark source lineage;
   - return convention;
   - source freshness through latest completed trading day;
   - missingness accounting;
   - holdout status.
4. Decision-grade model evidence:
   - alpha evidence over pre-registered windows;
   - drawdown/tail-risk analysis;
   - turnover and cost/slippage sensitivity;
   - hit rate and return distribution;
   - correlation/active share versus existing sleeves;
   - concentration and sector/factor exposure where available;
   - capacity at the proposed capital cap;
   - clear thesis falsification review.
5. Decision-grade operational evidence:
   - complete paper run bundles;
   - reliability `GREEN + FULL_EVIDENCE` streak;
   - no `RELIABILITY_RED` events in the trailing review window;
   - no unresolved `RELIABILITY_YELLOW` trend;
   - clean pretrade and posttrade reconciliation;
   - target-attainment confidence not LOW;
   - broker/paper fill evidence with terminal order states;
   - non-empty reasons and operator actions for every halt, skip, fail, or
     partial state.
6. Reconciliation coverage:
   - broker positions and cash reconciled to model state;
   - posttrade snapshot is after the relevant order lifecycle, not stale or
     pre-buy;
   - target cash materially matches actual cash within the approved tolerance.
7. Liquidity/capacity evidence:
   - ADV participation threshold;
   - slippage sensitivity;
   - max single-name, top-N, and sleeve concentration;
   - capacity tested at the proposed dollar cap, not a generic reference size.
8. Operator review:
   - daily run ID and trade date;
   - broker positions/cash/orders/fills;
   - pre/post reconciliation;
   - target-attainment result;
   - reliability status and top reason;
   - data freshness;
   - dashboard/report suppression status;
   - VM/deploy cleanliness if relevant;
   - explicit manual go/no-go for first real dollar.

### Reliability Streak Rule

Default requirement: trailing 20 eligible paper runs must be
`RELIABILITY_GREEN + FULL_EVIDENCE`, with zero RED events and no unresolved
YELLOW trend.

For rare-event sleeves, Brett/CIO may approve an episode-count substitute, but
it must be predeclared before review and cannot be selected after favorable
results.

### Automatic Pause Conditions

Pause scaling or pilot launch on:

- any reliability RED/FAIL;
- unresolved YELLOW trend;
- broker/reconciliation mismatch;
- target cash drift above threshold;
- unresolved accepted order;
- broker reject;
- missing/stale required artifact;
- unexplained no-action/halt;
- capacity/cost breach;
- drawdown/tail breach;
- operator inability to match system state to broker truth;
- material data restatement or model rebaseline;
- unauthorized runtime, risk, allocation, broker, or cron change.

### Readiness Clock Reset

Reset the readiness clock on any material change to:

- model;
- parameter;
- universe;
- data source;
- benchmark;
- cost model;
- holdout;
- execution path;
- broker integration;
- cash policy;
- cron;
- risk limit;
- sleeve allocation;
- production runtime;
- capital cap;
- incident or hotfix affecting broker/model truth.

## Recommended Next FR Roadmap

### Immediate Blockers

1. Complete FR-070/hotfix observation on the next buy-capable run.
2. FR-078 - Artifact Coverage Matrix and Required Evidence Gate.
3. FR-083 - Reliability Coverage Hardening: artifact-completeness invariant,
   `GREEN + FULL_EVIDENCE`, and promotion-usable reliability definitions.
4. FR-080 - Broker/Model Reconciliation Backfill and run-retention validator.

### Evidence Hardening

5. FR-079 - Historical Performance Rebuild With Evidence Labels.
6. FR-081 - PIT Benchmark and Universe Integrity Audit.
7. FR-077 Phase B - machine-readable `decision_grade_evidence_contract.json`
   consumed by reporting, promotion, and readiness surfaces.

### Model Promotion Work

8. FR-082 - Sleeve Promotion Evidence Gate v1.
9. Resume FR-069 Phase C owner review only after the trust gates above are
   explicit.
10. Keep FR-063/068 as Orion/Lyra supporting evidence, not a standalone
    retirement/promotion action.

### Pilot Capital Readiness Work

11. FR-084 - Pilot Capital Readiness Checklist and approval packet.
12. Convert FR-075 into or register FR-081/FR-100-adjacent machine-readable
    controls registry if owner approves.
13. Add dashboard/MCP ingestion of reliability and controls trends only after
    labels cannot be confused with capital approval.

## Falsification And Caveats

The skeptical review rejects adoption of FR-100 as an active capital approval
gate today. It is acceptable as a draft governance framework and diagnostic
organizer only.

Required caveats:

1. `RELIABILITY_GREEN`, dashboard `HIGH`, and `PROMOTION_ELIGIBLE` are
   diagnostic labels, not real-capital approval.
2. Static evidence-envelope validation is necessary but insufficient; it does
   not prove fills, costs, liquidity, benchmark lineage, or metric
   completeness.
3. No current sleeve should be described as pilot-capital-ready without
   contradicting FR-077.
4. Paper execution quality is not evidence of real-money slippage, broker
   behavior, or liquidity capacity.
5. Current readiness code does not enforce all required hard blockers for
   capacity, slippage, liquidity, concentration, drawdown, and benchmark
   lineage.
6. Dashboard/deployment health labels must be partitioned from capital
   readiness labels before operator-facing use.

Changes required before adoption as a gate:

1. Register FR-100 explicitly in `fr_registry.md` and
   `fr_active_backlog.md` with status, owner, scope, and non-capital default.
2. Create a machine-readable capital-readiness contract requiring all
   dimensions to pass: data, model, operations, execution evidence,
   capacity/liquidity, costs/slippage, concentration, drawdown/tail risk,
   broker controls, rollback, and approval.
3. Add artifact-completeness gating so missing run-level broker/recon/target
   reliability evidence blocks promotion-usable GREEN.
4. Rename or partition labels so dashboard/deployment health cannot be mistaken
   for capital readiness.
5. Define explicit thresholds for ADV participation, slippage sensitivity,
   turnover, concentration, drawdown, correlation/active share, and capacity at
   the proposed capital cap.
6. Add owner approval records for Shadow -> Paper -> Pilot Capital, including
   cap, rollback, kill switch, monitoring, and waiver rules.
7. Make any capital-readiness surface consume FR-077 evidence classification
   and fail closed on `LOW_COVERAGE` or `NOT_DECISION_GRADE`.

## Adoption Criteria

FR-100 becomes canonical governance only when:

1. Brett/CIO approves FR-100 as the governing capital-readiness umbrella.
2. `fr_registry.md` and `fr_active_backlog.md` include FR-100 with status,
   owner, scope, non-goals, rollback language, and relationship to FR-063
   through FR-084.
3. Dirty governance files are reconciled without overwriting unrelated work.
4. The proposed FR-078 through FR-084 roadmap is either accepted, renumbered, or
   explicitly rejected.
5. A machine-readable evidence contract exists and is consumed by promotion and
   reporting surfaces.
6. Reliability implements artifact-completeness gating.
7. Capital-readiness labels are separated from dashboard/deployment labels.
8. First-dollar approval packet template exists and records signer, cap,
   duration, monitoring, rollback, and waiver rules.
9. `git diff --check` passes for the governance update.

## Registry, Backlog, And Roadmap Edits Not Applied

`docs/governance/fr_registry.md`, `docs/governance/fr_active_backlog.md`, and
`docs/governance/CURRENT_RESEARCH_ROADMAP.md` are existing governance source
files and may already contain unrelated worktree changes. This task therefore
does not edit them.

Recommended future edits after owner review:

- add FR-100 as `DRAFT_GOVERNANCE_FRAMEWORK_NOT_CAPITAL_GATE`;
- add proposed FR-078 through FR-084 as unapproved roadmap candidates or
  renumber them if conflicts appear;
- link FR-100 from FR-074, FR-077, and FR-069 Phase C readiness;
- record that pilot capital is currently `NO` and paper trading is
  `CONDITIONAL`;
- record that no current sleeve is pilot-capital decision-grade.

## Non-Goals

- No live capital authorization.
- No paper allocation change.
- No sleeve promotion, retirement, rename, or redeployment.
- No strategy selection, ranking, sizing, allocation, cash-policy, risk-limit,
  broker, execution, or cron change.
- No dashboard label change.
- No machine-readable gate implementation in this FR.
