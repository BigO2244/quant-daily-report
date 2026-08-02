MODE: BUILD
PROJECT_TYPE: GOVERNANCE_TOOLING
RISK_TIER: LOW
OBJECTIVE: Operationalize Aegis as the deterministic registry and executive interface for current Caerus work.

## FILES

create:
- aiops/aegis/importers.py
- aiops/aegis/reconciliation.py
- aiops/aegis/priority.py
- aiops/aegis/brief.py
- aiops/aegis/operations.py
- docs/architecture/aegis_operationalization_adr.md
- reports/aegis/

modify:
- aiops/aegis/
- aiops/cli.py
- Tests/test_aegis.py

## ACCEPTANCE CRITERIA

- Current repository and GitHub planning metadata import idempotently with provenance.
- Hierarchy and typed graph relationships are persistent, deterministic, and cycle-safe.
- Reconciliation, priorities, decisions, briefs, Mission Control, and mission-first CLI are operational.
- The first consolidation mission remains non-executing and approval-required.
- No autonomous Codex dispatch, model API, trading, broker, allocation, sizing, scheduler, VM, deployment, or capital mutation exists.
