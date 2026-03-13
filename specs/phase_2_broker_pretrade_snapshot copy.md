Task: Implement Phase 2 — Graduated Pre-Trade Reconciliation

Context
This repository recently implemented Phase 1 broker observability:
- pretrade broker snapshots
- reconciliation artifacts
- run observability improvements

We confirmed a real reconciliation failure where the broker and canonical model diverged:

Broker positions
AAPL ABBV ABNB FDX GLW HWM KMI MPC ROST VZ

Model positions
AAPL ABBV ABNB EOG FDX GLW KMI ROST VZ

Diff:
missing_in_broker: EOG
missing_in_model: HWM, MPC

This caused a hard block under the current strict reconciliation logic.

The architectural direction is defined in two repo specs:

Primary implementation spec
specs/phase_2_broker_pretrade_snapshot.md

Architectural reference
specs/broker_authoritative_execution_model.md

The Phase-2 spec is authoritative for implementation.
The broker-authoritative spec provides context but should not be implemented fully in this phase.

Goal
Change reconciliation from a strict blocker into a graduated drift detector that:

1. detects drift
2. classifies it
3. self-heals safe cases
4. only blocks dangerous conditions

Scope
Only implement Phase-2 reconciliation redesign.

Do NOT implement later phases of broker-authoritative execution yet.

Key Implementation Tasks

1. Add classify_drift() in reconciliation.py

Return structure should classify conditions as:

WARN
SELF_HEAL
BLOCK

Conditions to handle:

Symbol set mismatches
Quantity mismatches
Cash drift
Equity drift
Canonical snapshot missing
Canonical snapshot stale
Broker connectivity/auth failure

2. Replace the strict reconciliation gate

Current behavior:
pre_trade_reconcile_or_exit()

New behavior:
pre_trade_reconcile_and_classify()

This function must:

Always write
outputs/runs/<RUN_ID>/broker/recon_pretrade_<DATE>.json

Return structured decision metadata.

3. Implement self-heal logic

Allow execution to proceed when drift is benign.

SELF_HEAL cases:

Missing canonical snapshot
Stale canonical snapshot
Symbol set drift between broker and canonical
Canonical contains symbols not present in broker

In SELF_HEAL cases:

Refresh canonical_positions.json from broker snapshot
Log the repair action
Continue execution

4. WARN cases

Cash drift < 1% equity

Record warning but allow execution.

5. BLOCK cases

Broker authentication failure
Broker unreachable
Account status not ACTIVE
Broker position fetch failure
Corrupt broker payload

Only these conditions should block execution.

6. Update daily_quant_report.py

Replace the strict reconciliation call with the new classification flow.

Handle:

BLOCK
WARN
SELF_HEAL

Ensure artifacts still write correctly.

7. Feature flag

Add environment toggle:

RECON_V2=1

Behavior:

If RECON_V2 is not set → old reconciliation path
If RECON_V2=1 → new Phase-2 logic

Do not remove the old path yet.

8. Artifact requirements

Every run must produce:

broker/recon_pretrade_<DATE>.json

Fields should include:

warnings[]
self_heals[]
hard_blocks[]
missing_in_broker
missing_in_model
qty_mismatches
reconciliation_decision

9. Regression test

Add a test using the real drift scenario:

broker:
AAPL ABBV ABNB FDX GLW HWM KMI MPC ROST VZ

model:
AAPL ABBV ABNB EOG FDX GLW KMI ROST VZ

Expected behavior:

classification = SELF_HEAL
canonical refreshed from broker
execution allowed

10. Tests to add

Test classify_drift decisions
Test missing canonical snapshot
Test stale canonical snapshot
Test symbol set drift
Test benign cash drift
Test broker auth failure

Ensure recon_pretrade artifact is written in all cases.

Constraints

Keep implementation minimal.
Avoid broad refactoring.
Do not alter order execution sequencing.
Do not alter dashboard logic.
Do not implement Phase-3 broker-authoritative trading logic.

Validation

Run:

pytest
python3 -m py_compile on modified files

Output required

Return:

Files changed
Behavior changes
Tests added
Validation results
Risks / follow-ups