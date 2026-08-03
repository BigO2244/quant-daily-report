# HYP-2026-003 Evaluator Validation

Date: 2026-08-03 UTC
Classification: `RESEARCH_ONLY_NON_EXECUTIONAL`

## Outcome

- Terminal-settlement contract: implemented and machine-auditable.
- Current terminal-settlement evidence: `NOT_CERTIFIED`.
- HYP-2026-003 evaluator: implemented as a fail-closed frozen boundary and
  causal event-construction scaffold.
- Return estimate: not run.
- Primary metric: `null` by design until the enumerated frozen portfolio and
  inference obligations are implemented and independently reviewed.
- Research state: `UNPROVEN / BLOCKED`.
- Challenge period: not accessed.

## Frozen governance clarifications

Approved by Brett Olson on 2026-08-03 before any return access:

- Option B comparator: the first purchase retains its single event; the filing
  that first completes the cluster creates only the cluster event; no earlier
  event is removed using future information.
- The 120-day hold is removed from the formal variant family. It remains a
  descriptive diagnostic only. The formal family is five total variants:
  primary, CEO/CFO-required, three-insider minimum, five-basis-point purchase-
  value floor, and 20-day hold.

## Validation results

- Full Alpha Lab test collection: `109 passed`.
- Focused settlement, evaluator, control-plane, gate, and data-spine tests:
  `81 passed` before the final Form 4 certification hardening; the full
  collection above includes the final state.
- Python compilation: passed for all changed Python modules.
- Evaluator specification hash and load: passed.
- Evaluator production-boundary scan: `PASS`, no findings.
- Data-spine production-boundary scan: `CLEAN`, no findings.
- Git whitespace validation: passed.
- Configured Ruff executable: unavailable in the reused project environment.

## Repository-wide suite

The repository-wide run was attempted and stopped after a prolonged external
price lookup at 61%: `1688 passed`, `10 skipped`, and `19 failed` in 277
seconds. All 19 collected failures were rerun at the exact base commit
`2b4f6c99216a2764d3692735f0e3f783ce7dca0a`; all 19 reproduce there.

The pre-existing failures cover Argo priority expectations, daily health
fixtures, differentiation and feedback-loop expectations, a pandas date-type
merge in flow detection, governance strategy-name expectations, an absent PIT
universe fixture, and missing Alpaca-package behavior in price-source tests.
None imports or exercises the HYP-2026-003 implementation. The full suite is
therefore not reported as passing.

## Fail-closed proofs

- A non-ready data gate is rejected before evaluator files are opened.
- Gate dictionaries must match their canonical gate hashes.
- Every file must match its certified SHA-256.
- Every asset must be a separately certified pre-2025 extract with a maximum
  observation date before 2025-01-01.
- Form 4 data additionally requires causal amendment-lineage and certified
  beneficial-owner independence attestations.
- Current Form 4 materialization explicitly writes a blocked provider
  certification; issuer-wide future amendment exclusion cannot be certified
  as causal.
- The evaluator rejects challenge phase and submits no orders.

## Boundary statement

This change does not modify broker, execution, allocation, sizing, scheduler,
cron, VM, Paper, pilot, live, deployment, or capital behavior. It does not
promote a hypothesis or authorize challenge access.
