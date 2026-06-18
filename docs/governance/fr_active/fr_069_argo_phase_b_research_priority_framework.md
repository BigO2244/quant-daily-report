# FR-069 Argo Phase B Research Priority Framework

Status: RESEARCH_IMPLEMENTED
Owner: Caerus Research Program
Last Updated: 2026-06-17
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Decision Status: RESEARCH_ONLY / NO_RUNTIME_CHANGE

This packet extends Argo from a Phase A evidence consumer into a Phase B
research-prioritization engine. Argo Phase B recommends where Caerus should
spend the next unit of research effort. It does not allocate capital, select
securities, submit orders, change risk controls, promote sleeves, retire
sleeves, route capital, or alter paper/live trading behavior.

RESEARCH_ONLY
NO_RUNTIME_CHANGE

## Executive Summary

Argo Phase B answers: where should Caerus spend future research effort?

Current Phase B artifact:

`outputs/research/argo/argo_phase_b_research_priority_2026-06-17.json`

Current forced research priority ranking:

| Rank | Sleeve | Classification | Research action |
|---:|---|---|---|
| 1 | Cassiopeia | BLOCKED_DATA | Build a PIT-safe event taxonomy and event-tape contract. |
| 2 | Orion | BLOCKED_DATA | Prepare an owner-facing Orion/Lyra merge-watch disposition packet. |
| 3 | Phoenix | BLOCKED_EVIDENCE | Hold the current candidate after Phase C capacity failure; revisit only with new candidate evidence or owner-approved capacity policy change. |
| 4 | Argo | RESEARCH_PRIORITY_MEDIUM | Accumulate advisory scoring history and reviewer notes. |
| 5 | Cygnus | BLOCKED_DATA | Defer until PIT consensus/EPS-surprise data is selected. |
| 6 | Polaris | RESEARCH_PRIORITY_LOW | Keep as baseline/control; avoid new feature work. |
| 7 | Lyra | RESEARCH_PRIORITY_LOW | Stop independent Lyra promotion research; use only in disposition or redeployment work. |

Highest-ROI task: build Cassiopeia PIT event taxonomy and event-tape contract.

Biggest platform blocker: PIT event taxonomy and event-tape contract missing
for event-driven research.

## Evidence Inventory

Argo Phase B consumes:

- Argo Phase A evidence framework.
- FR-069 sleeve manifest and evidence-envelope templates.
- Orion/Lyra PIT rebaseline and disposition analysis.
- Phoenix crisis/recovery evidence.
- Phoenix Phase B risk-shaping evidence.
- Phoenix Phase C liquidity/capacity blocker artifact.
- Current research roadmap and sleeve onboarding packets.

Argo Phase B ignores by design:

- live broker state;
- execution artifacts;
- allocation targets;
- order lifecycle data;
- promotion state as an automatic action;
- post-hoc non-PIT evidence.

## Priority Methodology

The Phase B score is a research-queue score, not an allocation score.

Inputs:

- differentiation;
- evidence gap;
- expected uncertainty reduction;
- dependency impact;
- implementation readiness;
- governance readiness;
- external blocker penalty.

Classifications:

- `RESEARCH_PRIORITY_HIGH`
- `RESEARCH_PRIORITY_MEDIUM`
- `RESEARCH_PRIORITY_LOW`
- `BLOCKED_EXTERNAL`
- `BLOCKED_DATA`
- `BLOCKED_EVIDENCE`
- `READY_FOR_NEXT_RESEARCH`

Blocked work can still rank highly when resolving the blocker would unlock a
large platform decision. That is why Phoenix ranks first despite being
`BLOCKED_EXTERNAL`.

## Sleeve Rankings

### 1. Phoenix

Phoenix remains differentiated and risk-shaped, but it is no longer blocked by
vendor access. Sharadar SEP OHLCV was restored and Phase C liquidity/capacity
evidence is decision-grade but adverse for the current candidate.

Research to stop: do not tune Phoenix alpha further or run a Shadow-readiness
review for the current candidate after Phase C capacity failure.

### 2. Cassiopeia

Cassiopeia is the highest-ranked unblocked research build. The next task is a
PIT-safe event taxonomy and event-tape contract with availability timestamps.
No event signal implementation should start before the event contract proves
source lineage.

### 3. Orion

Orion research should shift from open-ended comparison to owner-facing
disposition. The evidence supports Orion as the provisional retained
core-momentum implementation if Orion/Lyra are consolidated, but this packet
does not merge, promote, or retire anything.

### 4. Argo

Argo should continue accumulating advisory scoring history. It must not become
a hidden allocation, promotion, or retirement rule.

### 5. Cygnus

Cygnus remains differentiated by thesis but lower priority until PIT
consensus/EPS-surprise vendor data exists. Cygnus v0 retuning remains stopped.

### 6. Polaris

Polaris remains the baseline/control sleeve. New feature work should pause
while execution and target-attainment remain observe-first.

### 7. Lyra

Independent Lyra promotion research should stop. Lyra should appear only in
Orion/Lyra disposition work or a future owner-approved redeployment thesis.

## Dependency Analysis

| Sleeve | Dependencies |
|---|---|
| Phoenix | New candidate evidence or owner-approved capacity policy change after measured Phase C capacity failure. |
| Cassiopeia | Owner-approved event taxonomy, PIT event tape, availability timestamps. |
| Orion | Owner disposition decision, optional sector/factor overlap diagnostic. |
| Lyra | Owner disposition decision, redeployment thesis if retained under a new purpose. |
| Cygnus | PIT consensus/EPS-surprise vendor, v1 holdout-preserving plan. |
| Argo | Future evidence packet history, reviewer challenge log. |
| Polaris | Baseline monitoring, FR-070 observation. |

## Blocker Analysis

The largest remaining research-data blocker is missing PIT event data.
Cassiopeia can attack this with an event contract, not signals.

## Recommended Next Research Tasks

1. Build Cassiopeia PIT event taxonomy and event-tape contract.
2. Prepare owner-facing Orion/Lyra merge-watch disposition packet.
3. Hold current Phoenix candidate after measured Phase C capacity failure.
4. Add Argo scoring-history notes when future evidence packets change ranks.

## Governance Controls

- Argo Phase B is advisory only.
- The ranking is a research queue, not an allocation queue.
- Owner approval and separate FRs are required for any promotion, retirement,
  capital routing, or production behavior change.
- Execution, broker, risk, allocation, strategy-selection, and promotion code
  are out of scope.

## Reviewer Challenge

The strongest challenge is that a ranking can be mistaken for a capital
allocation. The artifact and this packet explicitly prevent that: Argo Phase B
does not allocate, promote, retire, select securities, submit trades, or change
runtime behavior.

The second challenge is that Phoenix is differentiated but capacity-failed. The
ranking handles that directly: Phoenix is evidence-blocked, not externally
blocked, and is not ready for Shadow.

## Decision Status

`RESEARCH_ONLY / NO_RUNTIME_CHANGE`

Allowed next work:

- build Cassiopeia event-contract evidence;
- prepare Orion/Lyra disposition governance.
- revisit Phoenix only with new candidate evidence or owner-approved capacity
  policy change.

Disallowed from this packet:

- allocate capital;
- route capital among sleeves;
- change execution;
- change broker behavior;
- change risk controls;
- promote sleeves;
- retire sleeves;
- activate Argo.
