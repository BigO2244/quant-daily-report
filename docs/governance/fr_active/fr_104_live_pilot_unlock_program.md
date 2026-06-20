# FR-104 LIVE_PILOT Unlock Program

Status: `ENGINEERING_READY_LOCAL_NOT_OPERATIONALLY_APPROVED`  
Date: `2026-06-19`  
Execution Impact: `DISABLED_BY_DEFAULT`  
Capital Impact: `$0`  
Pilot Engineering Cap: `$100`

## Objective

FR-104 adds a code-supported, test-covered `LIVE_PILOT` execution path for a
tightly capped `$100` pilot. It does not approve capital, add credentials,
enable cron, change strategy selection, alter sizing/allocation, or submit live
orders during this task.

## Routing Decision

`LIVE_PILOT` is separate from existing paper execution.

- `paper_broker.run_paper_day()` continues to refuse `TRADING_MODE=live` and
  `TRADING_MODE=live_pilot`.
- `LIVE_PREFLIGHT` remains observe-only and never submits.
- `scripts/live_pilot_execute.py` is the only FR-104 manual live-pilot path.
- Existing cron remains paper-forced and is not live-pilot enabled.

## Required Controls

Live pilot submission requires all of the following:

- `TRADING_MODE=live_pilot`
- `ALPACA_PAPER=0`
- live Alpaca endpoint
- `CAERUS_LIVE_PILOT_APPROVED=1`
- `CAERUS_LIVE_PILOT_CAPITAL_CAP` present, positive, and `<= 100`
- `CAERUS_LIVE_PILOT_SLEEVE_ID` present
- `CAERUS_LIVE_PILOT_ACCOUNT_ID` or `CAERUS_LIVE_PILOT_ACCOUNT_ID_HASH` present
- `CAERUS_LIVE_PILOT_MAX_ORDERS` present and positive
- `CAERUS_LIVE_PILOT_KILL_SWITCH` not set
- `CAERUS_LIVE_PILOT_DRY_RUN=0` only for actual live submission
- no cron context unless `CAERUS_LIVE_PILOT_CRON_APPROVED=1`
- every order has explicit notional through limit price and quantity

Any failed control blocks before Alpaca SDK submission.

## Order Policy

Initial FR-104 policy:

- long-only US equity limit orders only;
- no market orders without explicit estimated notional;
- no options, crypto, margin, or shorting;
- no fractional quantities unless `CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL=1`;
- sells blocked unless explicitly whitelisted by
  `CAERUS_LIVE_PILOT_SELL_WHITELIST`;
- aggregate submitted notional must be `<=` configured cap;
- order count must be `<= CAERUS_LIVE_PILOT_MAX_ORDERS`.

## Artifact Isolation

FR-104 writes only under:

`outputs/live_pilot/runs/<RUN_ID>/`

Required artifacts:

- `live_pilot_execution_payload.json`
- `live_pilot_preflight.json`
- `live_pilot_orders_intended.json`
- `live_pilot_orders_submitted.json`
- `live_pilot_broker_snapshot_pre.json`
- `live_pilot_broker_snapshot_post.json`
- `live_pilot_reconciliation.json`
- `live_pilot_capital_usage.json`
- `live_pilot_operator_summary.json`

It does not write to `outputs/runs`, `outputs/broker`, `outputs/paper_state`,
or `outputs/orders_sent`.

## Reconciliation States

- `CLEAN`: all submitted orders are terminal filled, no rejects, no unresolved
  states, counts match.
- `PARTIAL`: partial fill state exists.
- `REJECTED`: any broker rejection or submit error exists.
- `UNRESOLVED`: accepted/new/open/non-terminal state exists or counts do not
  match.
- `DRY_RUN`: no live submission occurred.

Any non-clean live state produces operator action and no automatic liquidation
or correction order.

## Engineering Status

`LIVE_PILOT_ENGINEERING_READY_LOCAL`

This means the local code path and tests support a disabled-by-default,
explicitly approved, tightly capped pilot path. It does not mean pilot capital
is investment-ready, credential-ready, deployed to VM, or approved for Monday.

## Remaining Non-Code Gates

- Signed pilot packet.
- Approved sleeve/cap/account.
- Credentials outside git.
- VM deployment and validation.
- Read-only live account preflight.
- Human operator checklist.
- FR-100/FR-101 capital-readiness evidence if investment readiness is required.

## Rollback

Code rollback is a normal `git revert` of FR-104. Operational rollback for any
future non-clean live state is manual: enable kill switch, remove live
credentials from scheduled runtime, preserve artifacts, inspect broker truth,
and do not place corrective orders without a separate incident runbook.
