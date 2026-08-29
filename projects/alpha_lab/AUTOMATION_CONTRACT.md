# Alpha Sunday Automation Contract

Status: `ACTIVE_BOUNDED_EXCEPTION`  
Owner approval: recorded 2026-08-29  
Automation id: `alpha-lab-weekend-research-cycle`

## Purpose and Window

The Alpha Sunday cycle is an owner-approved Codex automation that performs a
bounded, research-only discovery cycle each Sunday from 00:05 through 05:00
America/New_York. It is not a production VM cron job or GitHub Action.

The executable prompt and schedule live in Codex automation state. This file is
the durable repository contract used to audit that external authority.

## Permitted Work

- Read institutional memory and build the opportunity map required by
  `RESEARCH_IDEA_GENERATION_WORKFLOW.md`.
- Screen no more than three economically distinct candidates.
- Select no more than one candidate for the cheapest honest falsification.
- Run a formal experiment only when the exact frozen hypothesis already has a
  durable owner `RUN EXPERIMENT` authorization.
- Produce exactly one weekly terminal status:
  `REJECT`, `CANDIDATE_READY_FOR_FREEZE`,
  `AUTHORIZED_EXPERIMENT_RESULT`, or `BLOCKED_OWNER_SUPPORT`.

## Prohibited Work

The automation may not:

- purchase data, contact a vendor, or create or rotate credentials;
- freeze a hypothesis or spend a trial without the required recorded owner
  authority;
- release holdout or challenge evidence outside its existing control contract;
- activate Shadow, nominate or promote Paper or Live, allocate capital, submit
  orders, or change production behavior;
- broaden its own schedule, scope, or authority.

All lineage and data gates fail closed. A blocked or rejected week is a valid
outcome and must not be converted into an unauthorized experiment.

## Companion Automations

The weekday CIO review queue, analyst proxy, options proxy observation, storage
audit, and free-data completion heartbeat may collect, observe, validate, or
notify within their own contracts. They do not extend the Sunday cycle's
authority and cannot perform a lifecycle or capital transition.

## Review and Rollback

- Review reliability, scope compliance, and decision usefulness after the
  first four completed Sunday cycles and whenever the executable prompt or
  schedule changes materially.
- Pause or delete the Codex automation to stop execution. Preserve this file
  and prior outputs as governance history.
- A full autonomous research-factory charter remains a later Atlas governance
  phase. This bounded exception does not mark that phase complete.

