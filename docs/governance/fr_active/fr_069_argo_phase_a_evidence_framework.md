# FR-069 Argo Phase A Evidence Framework

Status: RESEARCH_IMPLEMENTED
Owner: Caerus Research Program
Last Updated: 2026-06-17
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Decision Status: RESEARCH_ONLY / NO_RUNTIME_CHANGE

This packet defines Argo Phase A as a research-only evidence consumer. Argo
evaluates sleeve evidence quality and readiness. It does not allocate capital,
select securities, submit orders, change risk controls, promote sleeves, or
alter live/paper trading behavior.

## Executive Summary

Argo Phase A exists to answer: which sleeves have evidence that can be trusted?
It consumes existing FR-069 sleeve evidence and emits classifications for
research governance only.

Current Phase A artifact:

`outputs/research/argo/argo_phase_a_evidence_framework_2026-06-17.json`

Current classification summary:

| Sleeve | Phase A classification | Rationale |
|---|---|---|
| Polaris | EVIDENCE_READY | Current paper baseline with FR-068 PIT rebaseline lineage. |
| Orion | EVIDENCE_READY | Matched PIT evidence exists; currently preferred canonical core-momentum variant if consolidation is approved. |
| Lyra | NOT_READY / merge-watch | Matched PIT evidence exists, but differentiation is not statistically meaningful and governance recommends redeployment/merge watch. |
| Phoenix | EXTERNAL_DEPENDENCY_BLOCKED | Differentiated and risk-shaped, but PIT liquidity/capacity validation is blocked by Nasdaq Data Link access. |
| Cassiopeia | NOT_READY | Spec/onboarding only; event contract and decision-grade evidence missing. |
| Cygnus | NOT_READY | V0 shelved; v1 requires PIT consensus/surprise vendor data. |
| Argo | RESEARCH_READY | Evidence-consumer framework is available for governance review only. |

## Evidence Inventory

Argo Phase A consumes:

- FR-069 sleeve manifest and onboarding packets.
- Orion/Lyra PIT rebaseline artifact and disposition analysis.
- Phoenix crisis/recovery evidence.
- Phoenix Phase B risk-shaping evidence.
- Phoenix Phase C liquidity/capacity blocker artifact.
- FR-069 evidence-envelope templates.

Argo Phase A ignores by design:

- live broker state;
- execution artifacts;
- allocation targets;
- order lifecycle data;
- post-hoc non-PIT evidence;
- any artifact that would cause live or paper behavior to change.

## Sleeve Scoring Framework

The Phase A scoring model is intentionally coarse and governance-oriented. It
does not produce allocations.

Inputs:

- evidence quality;
- PIT readiness;
- differentiation;
- drawdown or risk evidence;
- turnover or cost sensitivity;
- unresolved readiness blockers.

Classifications:

- `NOT_READY`
- `RESEARCH_READY`
- `EVIDENCE_READY`
- `SHADOW_CANDIDATE`
- `PROMOTION_CANDIDATE`
- `EXTERNAL_DEPENDENCY_BLOCKED`

`SHADOW_CANDIDATE` and `PROMOTION_CANDIDATE` are descriptive research labels
only. They do not promote or route capital. Owner approval and separate FRs are
required for any lifecycle transition.

## Recommendation Methodology

Argo Phase A makes three research-only recommendations:

1. Treat Phoenix as `EXTERNAL_DEPENDENCY_BLOCKED` until Sharadar SEP OHLCV access
   is restored and PIT liquidity/capacity evidence can be rebuilt.
2. Treat Orion/Lyra as one redundant core-momentum family for governance review,
   not as two independently proven production sleeves.
3. Use Argo Phase A as an evidence inventory/scoring surface only.

## Governance Controls

- Argo is an observer, not a decision maker.
- No output from Phase A can change strategy selection, ranking, sizing,
  allocation, execution, broker behavior, risk controls, cron, or promotion
  state.
- All artifacts must carry `RESEARCH_ONLY`, `NON_EXECUTIONAL`, and
  `NO_RUNTIME_CHANGE` language.
- Any future Shadow/Paper/Pilot/Production use requires a separate
  owner-approved FR.

## Failure Modes

- Operators mistake evidence classifications for allocation instructions.
- Stale sleeve evidence is scored as current.
- Redundant sleeves are double-counted as independent evidence.
- Phoenix liquidity blockers are treated as solved before Sharadar OHLCV access
  is restored.
- Argo scoring becomes a hidden promotion rule instead of a governance aid.

## Missing Evidence

- Phoenix PIT liquidity/capacity evidence after OHLCV cache rebuild.
- Cassiopeia event contract and event tape.
- Cygnus PIT consensus/surprise data for v1.
- Owner-approved Orion/Lyra merge/redeployment decision.
- Argo holdout-safe scoring history over multiple observation windows.

RESEARCH_ONLY
NO_RUNTIME_CHANGE
