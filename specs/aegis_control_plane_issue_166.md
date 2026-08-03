MODE: BUILD
PROJECT_TYPE: GOVERNANCE_TOOLING
RISK_TIER: LOW
OBJECTIVE: Build the non-trading Aegis orchestration control plane for issue #166.

## FILES

create:
- aiops/aegis/
- Tests/test_aegis.py
- docs/governance/decision_records/ADR-002_aegis_control_plane.md

modify:
- aiops/cli.py

## ACCEPTANCE CRITERIA

- Deterministic SQLite missions, task DAGs, packets, manifests, and append-only events.
- No broker, scheduler, allocation, execution, paper, pilot, live, or capital imports or changes.
- AIOPS execution is explicit-approval-gated and not invoked by Aegis mission creation.
