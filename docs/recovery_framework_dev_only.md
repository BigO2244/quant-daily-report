# Interrupted Rebalance Recovery Framework

This recovery framework is dev-only architecture for interrupted rebalance
intelligence. It must not be treated as production recovery automation.

## Current Scope

- Reconstruct historical interrupted execution timelines.
- Classify lifecycle states.
- Simulate buy-only normalization deltas.
- Score operational recovery risk.
- Generate governance, lineage, certification, and incident artifacts.
- Replay canonical fixtures deterministically.

## Explicit Non-Scope

- No automatic recovery.
- No broker mutation.
- No order submission.
- No replay of original execution payloads.
- No production execution import path.
- No cron, workflow, or deployment integration.

## Why It Is Not Production-Ready

Interrupted rebalance recovery changes portfolio exposure after an execution
failure. That requires operator judgment, broker-authoritative state validation,
settlement confidence, and explicit audit evidence. A deterministic simulation is
not enough to authorize trading.

Before any production consideration, the platform would need:

- explicit operator approval workflow
- independent broker state refresh and reconciliation
- durable idempotency ledger for recovery events
- recovery-specific execution lock semantics
- pre-submit duplicate order guard
- no-sell enforcement at the execution gateway
- post-recovery reconciliation and notification
- rollback and incident-review procedure
- governance sign-off documenting why recovery is safer than waiting

## Broker-Authoritative Principles

Broker state dominates historical model state. Any recovery candidate must be
computed from current broker positions, current account state, current open
orders, and terminal order/fill evidence. Historical execution artifacts are
inputs for intent reconstruction only; they are never replay instructions.

## Supervised Recovery Philosophy

Recovery is a separate supervised operational event. It must have distinct
artifacts, distinct client order IDs, distinct notes, and an explicit human
approval boundary. The normal executor must not silently continue a failed run.

## Governance Requirements

Future production promotion would require:

- a governance report showing legality and operator requirements
- a certification summary showing deterministic replay stability
- a lineage graph linking the failed execution, settlement observation,
  simulation, approval, recovery execution, and reconciliation
- an incident package suitable for operational review
- tests proving terminal-state enforcement and duplicate replay rejection

Until those controls are implemented outside this dev-only layer, the framework
is restricted to local analysis and dry-run artifact generation.

