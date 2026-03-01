# Strategy Change Protocol (Tier C Capital Protection) v0.1

## Purpose

Protect capital by enforcing strict governance on strategy-impacting changes.

Applies to:
- Signal logic
- Allocation logic
- Risk overlays
- Rebalance frequency
- Portfolio construction rules
- Slippage/turnover modeling changes

---

## Required Before Capital Consideration

For any strategy-impacting change:

1. `aiops run-all` must complete successfully (exit 0).
2. `pytest -q` must pass.
3. `aiops verify` must pass.
4. Turnover/Cost audit must be refreshed.
5. Diff must be reviewed.
6. RUN_ID artifacts must be complete:
   - plan.md
   - spec_snapshot.md
   - run_all_summary.md
7. Evidence must be reproducible from the run directory.

---

## Mandatory Questions (Self-Review)

Before considering capital deployment:

- Did expected return profile change?
- Did drawdown profile change?
- Did turnover increase?
- Did execution assumptions change?
- Did capital concentration change?
- Did risk constraints loosen?

If any answer is “yes,” document rationale in the spec.

---

## Separation of Concerns

AIOPS governs changes.
It does NOT execute trades.

Deployment remains a separate, explicit decision.

---

## Version

v0.1