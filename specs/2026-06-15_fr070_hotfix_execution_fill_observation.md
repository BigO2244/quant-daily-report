MODE: HARDEN
PROJECT_TYPE: production_intent_paper_trading
RISK_TIER: HIGH
OBJECTIVE: Diagnose and hotfix the 2026-06-15 execution fill observation incident where submitted Alpaca sell orders filled authoritatively but Caerus reported zero fills, skipped the post-sell buy phase, produced NOT_COMPARABLE reconciliation, and labeled the run EXECUTED.

# 2026-06-15 FR-070 Hotfix: Execution Fill Observation

## Incident

- Trade date: 2026-06-15
- Run ID: 2026-06-15T093505-0400_c68a22d
- Expected sell orders: MNST SELL 2; C SELL 1
- Expected buy orders after post-sell rebudget: SPG BUY 4; UNH BUY 1
- Caerus reported: EXECUTED, submitted=2, accepted=2, filled=0, reconciliation=NOT_COMPARABLE, no halt/skip reason
- Broker truth supplied by operator:
  - C filled 1 at 2026-06-15 09:36:55 ET, avg fill 142.52
  - MNST filled 2 at 2026-06-15 09:38:27 ET, avg fill 91.795

## Required Work

1. Audit governance treatment and register a HOTFIX under the existing FR governance model without FR-number collision.
2. Audit available incident artifacts and distinguish broker-authoritative facts, persisted Caerus facts, inferred facts, and missing evidence.
3. Audit broker order-state refresh, status normalization, sell-phase polling, lifecycle progression, reconciliation, reporting, email semantics, and FR-070 target-attainment integration.
4. Audit the precompute buy-turnover discrepancy and either fix if small/direct/low-risk or register a linked follow-up.
5. Implement the smallest safe correction supported by evidence:
   - authoritative order refresh by stable broker order ID;
   - explicit terminal-state handling including Alpaca filled;
   - no open-orders-only dependence for terminal observation;
   - monotonic state persistence;
   - bounded final authoritative refresh before declaring sell phase unresolved, reconciliation, artifacts, or email;
   - accurate top-level outcome and halt/skip reason when lifecycle phases do not complete;
   - no duplicate order path.
6. Add deterministic regression tests covering accepted-to-filled, filled orders absent from open listings, staggered sell fills, final recovery refresh, unresolved/partial/rejected sell behavior, idempotent continuation, email semantics, FR-070 target attainment, incident-specific reconstruction, and turnover if touched.
7. Run validation proportional to execution-integrity blast radius.
8. Perform an independent final review and patch only if actionable defects remain.
9. Prepare deploy readiness without automatic production merge unless policy authorizes it.

## Agent Loop Output

Use:

`reports/agent_loops/2026-06-15_fr070_hotfix_execution_fill_observation/`

Expected files:

- `01_governance_audit.md`
- `02_execution_lifecycle_audit.md`
- `03_broker_state_audit.md`
- `04_reconciliation_reporting_audit.md`
- `05_implementation_plan.md`
- `06_codex_implement.md`
- `07_independent_review.md`
- `08_codex_patch.md`
- `09_final_validation.md`

## Stop Conditions

Stop with NEEDS_OPERATOR rather than guessing if authoritative artifacts are unavailable and root cause cannot be separated from broker retrieval, status normalization, polling, and persistence; if the fix could duplicate orders; if safe lifecycle resumption cannot be proven; if tests cannot reproduce the failure; if validation fails; or if independent review identifies unresolved capital-protection risk.
