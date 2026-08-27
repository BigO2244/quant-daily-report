# Orion Decision Freshness and Precompute Recovery

## Purpose

This runbook covers the post-close hydration → Orion computation → morning
PAPER precompute dependency. It does not change Orion's model, target weights,
current/prior XNYS-session policy, capital allocation, or broker behavior.

## Incident boundary

On 2026-08-24 the post-close price hydration returned empty downloads for the
requested universe and remained behind the completed session. No eligible
2026-08-24 Orion artifact was produced. The 2026-08-25 precompute and self-heal
correctly failed closed. Hydration and artifact generation recovered later on
2026-08-25; subsequent precompute runs succeeded.

The hardening closes a separate evidence gap: a newly dated Orion JSON file is
no longer sufficient evidence of a newly computed capital decision.

## Required chain

Every completed Orion artifact used by PAPER must prove this chain:

`market data → normalized panel → features → full rank history → current rank table → target weights`

The `decision_lineage` object binds every stage to its direct parent and records
the completed session, generation time, model version, coverage, and selection
trace. `decision_eligible`, `observation_status`, `data_status`, and
`coverage_status` must all explicitly be eligible/OK. Pre-close copies remain
ineligible.

An unchanged target is valid when new feature and rank hashes prove a fresh
computation. A changed parent with an unchanged non-target child, an entirely
copied-forward chain, missing lineage, or broken parent binding produces
`STALE_DECISION_SUSPECTED` and blocks capital authority.

The immediately preceding XNYS session must also have complete lineage. Missing
or legacy prior lineage is not silently treated as a bootstrap. The sealed
bundle hash-binds that prior source and lineage so the comparison remains
auditable after sealing.

## Migration prerequisite

Before enabling this gate, replay and write complete lineage chronologically,
starting with 2026-08-25 and then 2026-08-26. Generate the first replay with
`--prior-lineage-anchor-for 2026-08-26`. That makes the 2026-08-25 Orion
artifact explicitly `decision_eligible=false`, scopes it to
`PRIOR_LINEAGE_TRUST_ANCHOR`, and permits it only as evidence for the exact
next session. Verify its internal hashes and source data. The 2026-08-26
artifact then binds to that migration anchor as its immediate prior session.
Do not copy or relabel a legacy artifact.

If deployment occurs after later completed sessions, continue the deterministic
replay in XNYS order without gaps through the latest completed session. There is
no implicit first-run or missing-history exception in the authority code.

## Readiness marker

Strict post-close hydration writes:

`outputs/price_hydration/<completed-session>/orion_decision_ready.json`

The marker is only READY when hydration coverage and shadow refresh are OK. It
hash-binds the hydration status, completed Orion artifact, full decision
lineage, and deployed Git SHA.

Morning precompute resolves the latest completed XNYS session and validates the
marker before invoking the planner. The marker's deployed SHA must equal both
the checked-out repository HEAD and `outputs/deploy_state.json` when that
deployment record exists. A missing or invalid marker blocks precompute without
weakening the governed current/prior-session source policy.

## Operator checks

For report date `YYYY-MM-DD`, inspect:

- `outputs/workflow/YYYY-MM-DD/orion_precompute_dependency.json`
- `outputs/price_hydration/<completed-session>/status.json`
- `outputs/price_hydration/<completed-session>/orion_decision_ready.json`
- `outputs/shadow_candidates/<completed-session>/caerus_orion.json`
- `outputs/precompute/YYYY-MM-DD/precompute_bundle_validation.json` or the
  workflow validation path
- `outputs/precompute/YYYY-MM-DD/paper_target_package.json`

The precompute email reports market-data as-of, Orion generation time, market,
feature, rank, and target hash prefixes, changes versus the prior session,
freshness state, and deployed Git SHA. `STALE_DECISION_SUSPECTED` must never be
present in an OK email or executable bundle.

## Recovery

1. Do not copy, rename, or relabel a prior Orion artifact.
2. Diagnose the post-close hydration status and per-symbol/anchor coverage.
3. Rerun strict hydration and shadow refresh for the missing completed XNYS
   session using the normal governed command.
4. Confirm a new READY marker whose source and hydration hashes match disk and
   whose deployed SHA matches repository/deployment state.
5. Rerun precompute or allow the existing fail-closed self-heal path to do so.
6. Confirm sealed-bundle validation is OK and the email freshness section is
   VERIFIED before permitting normal PAPER execution flow.

Do not bypass the readiness guard or edit output hashes. Deployment, scheduler
changes, canonical replay, email sending, broker access, and PAPER submission
remain owner-controlled operational actions.

## Rollback

Roll back through the repository's governed deployment process to a previously
validated exact SHA. A rollback does not make an older decision eligible: the
post-close readiness marker and complete decision lineage remain mandatory.
