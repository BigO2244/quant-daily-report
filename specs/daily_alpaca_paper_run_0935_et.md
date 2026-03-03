MODE: HARDEN
PROJECT_TYPE: Python Trading Pipeline
RISK_TIER: High
OBJECTIVE: Validate scheduled paper trading execution with deterministic artifacts and non-blocking email delivery.

---

# Daily Alpaca Paper Run — 09:35 ET (HARDEN)

## 1. OBJECTIVE
Run the daily Alpaca **paper** trading pipeline on a fixed schedule (target: ~09:35 ET) such that:

1) The engine produces deterministic, persisted artifacts for the day (signals + execution payloads + logs), and  
2) Trade execution is **never blocked** by email/report delivery failures (SMTP issues are non-fatal by default).

This spec is the canonical reference for:
- GitHub Actions scheduled paper execution
- AIOps HARDEN verification of the pipeline's operational contract

---

## 2. SCOPE
In-scope:
- The scheduled GitHub Action workflow that runs paper trading (e.g., `.github/workflows/daily-alpaca-paper.yml`)
- The engine entrypoint that generates selections and executes paper trades
- Artifact persistence needed to replay/report without rerunning the model
- Email/report generation as a downstream, best-effort operation

Out-of-scope:
- Changing selection logic, model math, or sleeve definitions
- Changing broker (Alpaca) or switching to live trading
- New risk overlays (e.g., MoO, stop losses) beyond current model behavior

---

## 3. NON-GOALS
- No changes to what is bought/sold (signals/weights) beyond what the model currently produces
- No dependency on external databases or services beyond existing broker + GitHub Actions environment
- No requirement that email must always succeed (only that artifacts exist and trades run)

---

## 4. ASSUMPTIONS
- Trading day basis is America/New_York.
- Workflow runs in a CI context with required Alpaca paper credentials set.
- Email may be unavailable intermittently (SMTP rate limits, auth errors, network issues).

---

## 5. INVARIANTS (HARDEN)
### 5.1 Capital / Execution Invariants
1) **Execution must not depend on SMTP**: email/report failure must not block the trade engine.
2) If model outputs are valid and broker is reachable, orders submit deterministically.
3) Reconciliation and ledger invariants remain unchanged from prior system behavior.

### 5.2 Artifact Invariants
For the scheduled run date (YYYY-MM-DD ET), the engine must write and/or update:
- A persisted **execution email payload** (or equivalent) before any SMTP attempts
- Canonical logs / run archive artifacts required for audit and replay
- Any existing canonical ledger files currently used by the system

### 5.3 Email Behavior Invariants
- Default mode: `EMAIL_STRICT=0` (non-blocking)
  - Email failures are warnings only; exit code remains success if engine succeeds.
- Strict mode: `EMAIL_STRICT=1`
  - Email failure is treated as fatal and the run fails (paranoid/debug mode).
- `EMAIL_DRY_RUN=1` skips SMTP sends entirely.

---

## 6. FAILURE MODES & EXPECTED BEHAVIOR
| Failure | Expected Result |
|---|---|
| Model computation fails | Run fails (non-zero). No trades. |
| Broker/API unreachable | Run fails (non-zero). |
| Reconciliation invariant fails | Run fails (non-zero). |
| Artifact write fails | Run fails (non-zero). |
| SMTP/auth/email fails (EMAIL_STRICT=0) | Run continues; engine succeeds; email step logs warning. |
| SMTP/auth/email fails (EMAIL_STRICT=1) | Run fails (non-zero). |

---

## 7. ACCEPTANCE CRITERIA
### A. Baseline Health
- `pytest -q` passes.
- AIOps verify passes for this spec.

### B. Decoupling Proof (Core Requirement)
In CI (or locally with stubs/mocks):
1) Force email send to fail (e.g., invalid SMTP credentials, mocked exception).
2) Ensure `EMAIL_STRICT=0` (default).
3) Confirm:
   - Engine stage completes successfully (exit 0)
   - Orders are submitted (paper) OR execution path completes as designed
   - Execution payload/artifacts exist for the run date
   - Email stage may fail but does not block engine completion

### C. Strict Mode Behavior
With `EMAIL_STRICT=1`, forced email failure must produce a non-zero exit.

### D. Dry Run Behavior
With `EMAIL_DRY_RUN=1`, SMTP is not called and the run completes successfully (assuming engine succeeds).

---

## 8. TEST PLAN
Required:
1) `pytest -q`
2) Unit tests covering:
   - non-blocking email failure with `EMAIL_STRICT=0`
   - fatal email failure with `EMAIL_STRICT=1`
   - dry-run short-circuit behavior

Recommended operational test:
- Trigger the paper workflow manually with intentionally broken SMTP credentials:
  - engine job must be green
  - email job can be red (best-effort) without blocking

---

## 9. ROLLBACK PLAN
If CI or scheduled trading health regresses:
1) Revert the workflow split commit(s) on main.
2) Revert email orchestration changes.
3) Re-run:
   - `pytest -q`
   - scheduled paper workflow dispatch test
4) Confirm engine execution restored.

---

## 10. IMPLEMENTATION NOTES
- Primary design principle: **capital execution is authoritative; notification is downstream**.
- Workflow should be structured so engine artifacts are persisted and uploaded independent of email success.
