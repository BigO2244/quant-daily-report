# Caerus Authority Handoff Contract

The migrated path is an immutable, hash-linked chain:

`Evidence (alpha observations) -> Decision (sole investment authority) -> Risk (constraints only) -> Execution (mechanical Trader handoff) -> Audit (read-only)`

Each package has a versioned schema, deeply immutable payload, stable content
hash, source references, and a parent package hash. Decision owns target
selection. Risk may suppress or reduce Decision targets, but cannot introduce
symbols, reverse sides, increase exposure, reduce the approved cash reserve, or
invent alpha. Execution verifies the full package hash and may consume only an
approved `caerus.execution.v2` package when the migrated path is selected.
Version 2 copies Risk's immutable constraints into the Trader handoff; version
1 remains read-compatible for preserved historical artifacts but is not emitted
by the new path. Malformed, incomplete, or tampered packages fail closed. An
explicit empty package remains an approved no-action decision. Audit records
observed orders and lineage findings without changing any upstream package.

The PAPER Trader executes rotations in two mechanical phases without changing
Decision targets: submit approved sells, poll for at most 120 seconds until all
sells are terminal and broker cash/buying power reflect confirmed fills, then
rebuild approved buys against actual post-sell holdings and funds. Only proceeds
from current-run confirmed PAPER fills may augment the execution cash ceiling;
partial, open, rejected, or unreflected sells keep the buy phase closed. The
live-capital lane retains the stricter settled-cash/GFV clamp and cannot inherit
the PAPER proceeds-reuse policy.

The governed whole-share policy keeps 5% cash as the objective and 2.5% as the
hard post-optimization floor. The Trader deterministically searches the nearest
feasible whole-share allocation over a provably bounded integer search region,
records the objective and tie-break proof, and submits that exact mechanical
result. The ordinary
target-attainment band is ±2 percentage points. A result outside that band may
pass only if reconciled broker quantities exactly match the immutable proof and
cash remains above the hard floor.

Universal GREEN is fail-closed. Nested audit failure, stale or missing required
artifacts, package/order equality divergence, open/partial/rejected orders,
lineage failure, or broker reconciliation failure must propagate to the daily
status. The first clean post-fix PAPER run creates an immutable comparison epoch
record; prior live-vs-shadow history is retained but excluded from the new epoch.

The existing legacy execution callers remain compatible while migration is
staged. New callers should pass `ExecutionRequest.approved_execution_package`
and use `authority.pipeline.execution_package_from_risk`.

`scripts/build_authority_packages.py` wraps an existing validated precompute
payload and writes the complete evidence, decision, risk, and execution chain.
The unified plan builder embeds the risk-approved target and cash package; the
Trader derives broker transitions mechanically from only that verified package.

For pre-open PAPER execution, Decision accepts Orion evidence only from the
current XNYS session or the immediately preceding XNYS session. The original
source date, path, and SHA-256 remain unchanged in lineage while the Decision
and Execution packages use the current session as their effective trade date.
This bounded market-calendar rule is not a latest-file fallback: missing,
malformed, or older snapshots fail closed.

Before the current session closes, shadow tracking may publish a reporting-only
snapshot carried from the last complete session. It is explicitly marked
`decision_eligible=false` and `PENDING_SESSION_CLOSE`; Decision must skip it and
use only an eligible current or previous-session artifact. The normal
post-close hydration replaces that provisional snapshot with completed
same-session evidence.
