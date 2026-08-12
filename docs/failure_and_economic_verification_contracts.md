# Failure, Attempt, WAL, and Economic Verification Contracts

Status: additive operational hardening contract. These modules do not select
investments, route orders, call a broker, change cron, or alter sleeve policy.

## Failure semantics

`core/failure_semantics.py` is the canonical typed vocabulary for:

- `DATA_FAILURE`
- `SIGNAL_FAILURE`
- `PRECOMPUTE_FAILURE`
- `STATE_FAILURE`
- `REGIME_FAILURE`
- `PORTFOLIO_CONSTRUCTION_FAILURE`
- `RISK_FAILURE`
- `AUTHORIZATION_FAILURE`
- `PLAN_INTEGRITY_FAILURE`
- `EXECUTION_FAILURE`
- `BROKER_FAILURE`
- `RECONCILIATION_FAILURE`
- `REPORTING_FAILURE`

Every class has a retry policy, fail-closed flag, escalation policy, allowable
fallbacks, forbidden fallbacks, and recovery procedure. All exposure-affecting
classes fail closed. Reporting failure may preserve already-produced canonical
economic artifacts, but it must be visible and may never invent or silently
reuse economic values.

`AUTHORIZED_NO_TRADE` is a resolved nonfailure. It is valid only when the plan
hash and authorization were validated and exactly zero orders were requested.

`SUBMISSION_UNKNOWN` means a broker mutation may have occurred. It requires a
stable order reference, immediate operator escalation, read-only broker lookup,
and no automatic resubmission.

## Immutable attempt and incident registry

`core/execution_attempt_registry.py` writes immutable, hashed artifacts beneath
a caller-supplied registry root:

```text
<root>/<trade_date>/attempts/<attempt_id>.json
<root>/<trade_date>/incidents/<incident_id>/<event_id>.json
<root>/<trade_date>/selection.json
```

Attempt and incident files use exclusive creation and are fsynced. Reusing an
identifier cannot overwrite history. `selection.json` is only a mutable index;
it references immutable hashes and does not replace the underlying attempts.

A later clean retry may be selected while a prior zero-submission failure stays
preserved. An unresolved `SUBMISSION_UNKNOWN` blocks success selection. Only an
explicit later `RECONCILED_SUCCESS` naming the ambiguous attempt in
`resolves_attempt_ids` clears that block.

## Pre-submit write-ahead log

`core/submission_wal.py` provides the broker-mutation boundary:

1. Construct one exact `OrderIntent` with the immutable plan ID/hash, generated
   order ID, stable client order ID, broker request fields, control price/
   notional, PAPER account scope, and sleeve lineage.
2. Call `prepare_order_intent` before any broker call.
3. Call the broker only when `broker_submission_allowed` is true.
4. Append a resolution event: `SUBMITTED`, `RECOVERED_BY_LOOKUP`, `REJECTED`, or
   `SUBMISSION_UNKNOWN`.

Artifacts are:

```text
<wal_root>/<trade_date>/intents/<client_order_id>.json
<wal_root>/<trade_date>/resolutions/<client_order_id>/<resolution_id>.json
```

The prewrite uses exclusive creation plus file and directory fsync. Failure to
persist raises `WalPersistenceError`; the caller has no submission permission.
On restart, the identical order returns the prior record with submission
permission false. If no resolution was durably recorded, broker lookup is
required. The same client order ID cannot be rebound to different order intent.

`created_at` and a restart's attempt ID are artifact metadata rather than order
replay identity. The original persisted attempt remains in the returned record.
All economic order fields, plan lineage, generated order ID, and client order
identity must still match exactly.

## Canonical economic truth

`core/economic_reconciliation.py` verifies:

```text
starting positions + signed fills = ending positions
starting cash - buys + sells - fees = ending cash
ending quantities x marks = position value
ending cash + position value = broker equity / canonical NAV
```

Quantity, cash, position-value, and NAV tolerances are explicit artifact fields.
Missing identities, broker-value drift, duplicate positions, and unexpected
short positions fail reconciliation.

Date-and-sleeve attribution independently verifies:

```text
sum(sleeve result dollars for trade date) ~= portfolio result dollars
```

Missing rows, wrong dates, unknown/unattributed sleeves, or an out-of-tolerance
sum fail reconciliation. `verify_canonical_economics` reports success only when
both broker economic truth and sleeve attribution reconcile.

## Integration rule

These contracts are fail-closed primitives, not evidence that runtime wiring is
complete. Executor integration must happen at the real production-equivalent
submission boundary. Reporting/health integration must consume the combined
economic verdict without translating a failed component to GREEN.
`write_canonical_economic_verification` can persist the combined verdict as an
immutable content-hashed run artifact.
