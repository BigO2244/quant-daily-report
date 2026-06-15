# Implementation Plan

Roles: Test/failure-injection designer, implementation agent

1. Add `ALPACA_SELL_PHASE_RECOVERY_TIMEOUT_SECONDS` defaulting to 240 seconds.
2. After the primary sell timeout, continue bounded authoritative `get_order` refreshes until all sells are filled/positions-confirmed, terminal failure occurs, refresh fails, or recovery expires.
3. Classify unresolved sell outcomes to explicit buy block reasons.
4. Prevent buy submission when sell orders remain unresolved after recovery.
5. Add deterministic tests with simulated monotonic clock and sleep.
6. Register governance and operational lessons.

Precompute turnover discrepancy:
- Not fixed in this hotfix. Incident artifacts/precompute email inputs for 2026-06-15 are missing locally, so the discrepancy cannot be proven as a calculation defect here.
- Linked follow-up recommended: `FR-073` or a child backlog item for precompute turnover definition/audit.

