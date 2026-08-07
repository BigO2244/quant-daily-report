# Caerus Authority Handoff Contract

The migrated path is an immutable, hash-linked chain:

`Evidence (alpha observations) -> Decision (sole investment authority) -> Risk (constraints only) -> Execution (mechanical Trader handoff) -> Audit (read-only)`

Each package has a versioned schema, deeply immutable payload, stable content
hash, source references, and a parent package hash. Decision owns target
selection. Risk may suppress or reduce Decision targets, but cannot introduce
symbols, reverse sides, increase exposure, reduce the approved cash reserve, or
invent alpha. Execution verifies
the full package hash and may consume only an approved `caerus.execution.v1`
package when the migrated path is selected; malformed, incomplete, or tampered
packages fail closed. An explicit empty package remains an approved no-action
decision. Audit records observed orders and lineage findings without changing
any upstream package.

The existing legacy execution callers remain compatible while migration is
staged. New callers should pass `ExecutionRequest.approved_execution_package`
and use `authority.pipeline.execution_package_from_risk`.

`scripts/build_authority_packages.py` wraps an existing validated precompute
payload and writes the complete evidence, decision, risk, and execution chain.
The unified plan builder embeds the risk-approved target and cash package; the
Trader derives broker transitions mechanically from only that verified package.
