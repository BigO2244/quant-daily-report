# DABL — Data Asset and Blocker Ledger (Phase 2 Preparation)

`projects.alpha_lab.data_foundry` is a **DRAFT_NONCANONICAL / PHASE1_BLOCKED**
local preparation surface. It is not a collector, a data spine replacement, a
research ledger, an evaluator, or a production command.

## Boundary

The Data Asset and Blocker Ledger (DABL) can plan and validate 19 typed event
kinds, including asset definitions, immutable versions, certifications,
blockers, readiness packets, and evidence-gap packets. Only the relevant asset,
certification, blocker, and packet records carry structural, unverified
`FAM-YYYY-NNN` and `HYP-YYYY-NNN` bindings to an external global-ledger export
receipt. DABL cannot create, remap, ratify, or otherwise speak for a family,
hypothesis, experiment, trial, inference, challenge, or review.

DABL validates exact structural pins only; it does not replace the Phase 1
identity verifier or manufacture authentication. Relevant local bindings state
`EXTERNAL_VERIFIER_REQUIRED`; external verification is required but is neither
performed nor granted by this package.
A future writer must derive the pin only from that verified signed projection
and receipt. A syntactically valid local placeholder is still non-canonical.

Existing `data_spine` readiness/certification/evaluator code is an **untrusted
adapter** here. It cannot satisfy a DABL fact or Tier A certification without a
future separately authorized, immutable, authenticated ceremony.

## What the v2 preparation contract requires

Every asset version must state one unambiguous fact for each of: license, PIT,
universe, delisting, corporate action, revision, missingness, and freshness.
Each fact is either hash-backed evidence, a concrete N/A reason, or an explicit
blocker. Mutable, staging, checkpoint, missing, non-UTC, unknown-field, bad-ID,
or bad-hash input fails closed. The pure event planner accepts no non-finite or
duplicate-key JSON, requires expected-head CAS and monotonic event time, and
replays the complete prospective typed projection before any test harness write.

Every tier, including a future Tier A, is non-activatable locally. All frozen
evaluator, alpha, and lifecycle permission bits are deterministically false.

The immutable schema manifest is pinned at
`a70874492b8bc58f558e3694eb1af3d146aa629c6623877823e4bb738d661429`.
It fixes 19 typed event kinds, their exact class mapping, literal schemas, wire
enums, canonicalization declarations, full event/projection wire-field metadata,
and invariant identifiers. The manifest hash identifies the declared contract;
it is not proof that runtime behavior executed or that external verification
succeeded.

Independent replay has an explicit request timestamp. Its typed receipt cannot
complete before either that request or the asset's model-availability time;
certification cannot predate asset availability, retrieval, ingestion, model
availability, fact observations, accepted license terms, replay request, or a
cited replay completion. A readiness packet cannot predate those same causal
times or its certifications.
Later `BLOCKED`, `REVOKED`, `SUPERSEDED`, or stale certification state preserves
the historical packet but marks its current validity false with a deterministic
serialized reason. Terminal state cannot be reset by a later draft status.

Source routes are `OWNED_FREE`, `LICENSED_TRIAL`, `PAID`, `SELF_COLLECTED`, or
`BOUNDED_PROXY`. A route must name scope, owner/external-write need, cost,
equivalence, and its residual claim. This package creates no purchase, contact,
credential, vendor, cloud, holdout, scheduler, collector, or trading action.

## Status

Phase 2 remains **PLANNED** and blocked on Phase 1 and QS-004. The future
create-only GCP initialization is a distinct owner authority and is explicitly
not implemented here. If later authorized, its only candidate namespace is
`outputs/research/alpha_lab/data_foundry/ledger/<bundle-id>/` on the
authoritative research root. The only test persistence helper is a private,
sentinel-gated synthetic store confined to the OS temporary directory; it is
not exported and cannot create that namespace.
