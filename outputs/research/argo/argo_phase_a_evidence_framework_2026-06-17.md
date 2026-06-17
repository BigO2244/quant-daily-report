# Argo Phase A Evidence Framework - 2026-06-17

RESEARCH_ONLY
NO_RUNTIME_CHANGE

## Executive Summary

Argo Phase A consumes existing sleeve evidence and emits research-only readiness classifications. It does not allocate capital, select securities, submit orders, or change production behavior.

## Sleeve Scores

| Sleeve | Classification | Score | Blockers |
|---|---:|---:|---|
| polaris | EVIDENCE_READY | 90 | none |
| orion | EVIDENCE_READY | 85 | none |
| lyra | NOT_READY | 73 | merge_watch_redundancy |
| phoenix | EXTERNAL_DEPENDENCY_BLOCKED | 42 | external_dependency_blocked, nasdaq_data_link_qelx06_temporary_disablement, pit_liquidity_ohlcv_unavailable |
| cassiopeia | NOT_READY | 0 | decision_grade_evidence_missing, event_contract_missing, research_spec_only |
| cygnus | NOT_READY | 5 | decision_grade_evidence_missing, eps_surprise_consensus_vendor_missing, v0_shelved |
| argo | RESEARCH_READY | 80 | none |

## Governance Controls

- Argo is an observer, not a decision maker.
- SHADOW_CANDIDATE and PROMOTION_CANDIDATE labels are descriptive only.
- Owner approval is required for any lifecycle, allocation, or production change.
- Execution, broker, risk, allocation, strategy-selection, and promotion code are out of scope.

## Recommendations

- `phoenix`: `EXTERNAL_DEPENDENCY_BLOCKED` - hold until Sharadar SEP OHLCV access is restored, then rebuild liquidity evidence.
- `orion_lyra`: `EVIDENCE_READY_FOR_GOVERNANCE_REVIEW` - treat as one redundant core-momentum family pending any owner-approved merge/redeploy decision.
- `argo`: `RESEARCH_READY` - use Phase A as an evidence inventory/scoring surface only.
