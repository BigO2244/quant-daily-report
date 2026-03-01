# Spec: Daily Alpaca Paper Run @ 9:35 ET + Pre-Market Suggested-Trades Report

**Spec ID:** `spec.daily_alpaca_paper_0935_et.v1`  
**Owner:** Brett  
**Repo:** `quant-daily-report-main`  
**Timezone:** `America/New_York`  
**First scheduled day:** **2026-03-02 (Mon)**  
**Mode:** `HARDEN` (governed change via `aiops run-all`)  

---

## 0) Summary

We will run **two** automated, daily jobs on **trading days**:

1. **Pre-market Daily Quant Report (suggested trades)**  
   - Target: **08:00 ET** (deliver before open)  
   - Output: email + archived run artifacts (suggested trades, gates/diagnostics)

2. **Alpaca Paper Execution**  
   - Target: **09:35 ET** (5 minutes after the 09:30 open)  
   - Output: Alpaca paper orders + reconciliation + archived artifacts

This spec also explicitly requires a **trading-day guard** (weekends/holidays skip cleanly) and **idempotent execution** (no duplicate order placement for the same `REPORT_DATE`).

---

## 1) Motivation

- Separate “**suggested trades**” from “**execution**” to:
  - get human visibility early (pre-market),
  - reduce open-auction noise by executing at 09:35,
  - keep a clean audit trail for what was proposed vs. what was executed.

---

## 2) Scope

### In scope
- Scheduling automation for:
  - pre-market suggested-trades report
  - 09:35 ET Alpaca paper execution
- Trading-day guard for both jobs
- Artifacts + notifications for both jobs
- Explicit DST-handling note and recommended scheduler behavior

### Out of scope
- Changes to signal generation, portfolio construction, or breaker logic
- Live trading (must remain paper)
- Broker migration or IBKR work

---

## 3) Definitions

- **Trading day:** A date where the US equities market is scheduled to be open (NYSE calendar is acceptable proxy).
- **REPORT_DATE:** The market session date the run is acting on (must match across report + execution).
- **Suggested trades:** The trade list produced by the model for `REPORT_DATE` prior to execution.

---

## 4) Requirements

### R1 — Pre-market report runs earlier than execution
- A scheduled job must run and deliver the Daily Quant Report (suggested trades) **before** 09:35 ET.
- Target schedule: **08:00 ET**.

### R2 — Alpaca paper execution runs at 09:35 ET
- A scheduled job must execute **paper** orders at **09:35 ET** on trading days.

### R3 — Trading-day guard
- Both jobs must:
  - detect non-trading days (weekends + holidays),
  - log `SKIP (market closed)` and exit `0` (non-failing).

Acceptable guard implementations:
- Alpaca market calendar endpoint (preferred if already in codebase), or
- `pandas_market_calendars` for NYSE.

### R4 — Idempotency (no duplicate orders)
- For a given `REPORT_DATE`, re-running the execution job must not place duplicate orders.
- Mechanisms may include:
  - deterministic `client_order_id` + broker-side “already exists” detection,
  - persisted “orders_sent” markers keyed by `REPORT_DATE`,
  - pre-flight reconciliation of existing orders/fills.

### R5 — Audit artifacts
Each job must write a deterministic run archive:
- `RUN_ID`
- `plan.md` / `run_all_summary.md` (AIOPS)
- Suggested trades (report job)
- Orders CSV + fills CSV + reconciliation snapshot (execution job)

### R6 — Notifications
- Report job: notify with suggested trades summary + gate status
- Execution job: notify with orders placed summary + reconciliation + gate status
- Include `RUN_ID`, `REPORT_DATE`, and git SHA/branch metadata.

---

## 5) Schedule Specification

**Timezone:** `America/New_York`

### Target times (ET)
- Pre-market report: **08:00 ET** (Mon–Fri)
- Alpaca execution: **09:35 ET** (Mon–Fri)

### UTC cron (valid for EST = UTC-5)
**NOTE:** After DST begins (2026-03-08), ET becomes UTC-4 and these cron times must shift by +1 hour unless the scheduler supports ET natively.

For **2026-03-02 to 2026-03-06** (EST):
- 08:00 ET = 13:00 UTC → `0 13 * * 1-5`
- 09:35 ET = 14:35 UTC → `35 14 * * 1-5`

**Preferred:** use a scheduler/workflow setting that respects `America/New_York` (if supported).  
**Fallback:** document DST update procedure (see §9).

---

## 6) Implementation Plan (Repo-level)

### A) Add/adjust workflows

Create or update two workflows (names illustrative):

1. `.github/workflows/daily_quant_report_premarket.yml`
   - Trigger: cron (08:00 ET target), plus optional manual dispatch
   - Steps:
     - trading-day guard
     - run report-only command that outputs suggested trades
     - send email (if enabled)
     - upload artifacts / persist run archive

2. `.github/workflows/alpaca_paper_execute_open.yml`
   - Trigger: cron (09:35 ET target), plus optional manual dispatch
   - Steps:
     - trading-day guard
     - run Alpaca paper execution command
     - upload artifacts / persist run archive
     - notify

### B) Standardize commands

Both workflows should call the **canonical lifecycle** command:

    aiops run-all --spec <spec_path> --mode HARDEN

Where `<spec_path>` is this spec in the repo (recommended path: `specs/daily_alpaca_paper_0935_et.md`).

### C) Ensure separation of concerns
- Report job must not mutate execution state (`orders_sent`, broker placement, etc.).
- Execution job is the only job allowed to place Alpaca orders.

---

## 7) Verification Plan

### V1 — Dry-run verification (local or CI)
- Run report job in a non-trading-day simulation and confirm it skips cleanly.
- Run execution job in dry-run mode (if supported) and confirm:
  - no duplicate orders are created on repeated runs,
  - artifacts are produced in the expected locations.

### V2 — Go-live verification (2026-03-02)
- Confirm report delivered before 09:35 ET with suggested trades.
- Confirm execution ran at 09:35 ET and placed paper orders consistent with the report (allowing for gates/constraints).

---

## 8) Acceptance Criteria

- [ ] On **2026-03-02**, suggested-trades report delivered before **09:35 ET**.
- [ ] On **2026-03-02**, Alpaca paper execution ran at **09:35 ET**.
- [ ] Both jobs skip cleanly on weekends/holidays (exit 0 + `SKIP (market closed)`).
- [ ] Execution is idempotent for `REPORT_DATE`.
- [ ] Both jobs produce deterministic artifacts with `RUN_ID` and required summaries.

---

## 9) DST & Scheduling Operational Note

DST begins **2026-03-08** (ET becomes UTC-4). If using UTC cron schedules, update:
- 08:00 ET → 12:00 UTC
- 09:35 ET → 13:35 UTC

If the workflow runner supports a timezone setting, prefer `America/New_York` to eliminate manual DST maintenance.

---

## 10) Rollback Plan

Rollback is safe and immediate:
- Disable the two cron schedules (or revert to previous schedule).
- No data migration required.
- Paper trading only; no live capital risk.

---

## 11) Operator Runbook (one-liners)

Run governed change (CI or local):

    aiops run-all --spec specs/daily_alpaca_paper_0935_et.md --mode HARDEN

Manual report run (optional):

    REPORT_DATE=YYYY-MM-DD aiops run-all --spec specs/daily_alpaca_paper_0935_et.md --mode HARDEN

(Exact env flags may differ; keep the AIOPS entrypoint canonical.)
