# AIOPS Change Control Contract (v0.1)

## Purpose

Define what constitutes a governed change and when `aiops run-all` is required.

This document prevents workflow drift.

---

## What Requires a Spec + run-all

The following changes MUST use:
aiops run-all –spec  –mode BUILD|HARDEN|EXPLORE
- Any strategy logic change
- Signal calculation changes
- Position sizing changes
- Risk overlays
- Rebalance logic
- Backtest methodology changes
- Audit module logic changes
- Execution routing or brokerage integration changes
- Lifecycle orchestration changes
- Exit code changes
- Determinism or artifact structure changes

---

## What Does NOT Require run-all

- Markdown-only documentation edits
- Comment-only code changes
- Non-functional refactors that do not affect behavior
- CI workflow formatting edits (if no logic impact)

If uncertain → default to governed spec.

---

## Rule of Discipline

If a change affects behavior, it must:
1. Have a spec in `specs/`
2. Produce a RUN_ID
3. Produce deterministic artifacts
4. Pass verify + pytest

No exceptions.

---

## Version

v0.1  
Repository: quant-daily-report-main