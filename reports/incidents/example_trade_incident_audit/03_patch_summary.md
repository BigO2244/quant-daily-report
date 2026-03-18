# Patch Summary

## Scope

Narrow observability hardening only. No strategy logic, reconciliation policy, or workflow trigger behavior changed.

## Change Intent

- make `execution attempted / not attempted / reason` explicit
- persist partial submission counts on broker abort
- prevent downstream summaries from collapsing partial execution into zero execution

## Expected Behavior After Patch

- operator summary records submitted, accepted, and rejected counts before abort
- execution results preserve prior attempt state when payload handoff is missing
- execution audit distinguishes true non-attempt from partial execution followed by failure

## Non-Goals

- no change to planner decisions
- no change to order filtering rules
- no change to broker routing or rejection handling policy
