# HYP-2026-003 Independent Adversarial Review

Date: 2026-08-03 UTC
Reviewer role: independent implementation review
Classification: `RESEARCH_ONLY_NON_EXECUTIONAL`

## Executive verdict

The work is **fit as a fail-closed contract and evaluator scaffold**. It is
**not fit to run the frozen return experiment, calculate the primary alpha
metric, or support a research decision**.

The current result must remain `UNPROVEN / BLOCKED`. The settlement auditor
correctly reports `NOT_CERTIFIED`; the evaluator correctly returns a null
primary metric and enumerates its unfinished obligations. No result in this
change supports `PURSUE`, Shadow activation, Paper promotion, allocation, or
production use.

## Severity-ranked findings

### Critical — no runnable path from the current certifications

The evaluator requires every asset to contain a nested ready gate, a
SHA-256-bound pre-challenge extract, a maximum observation date before
2025-01-01, and—on the Form 4 asset—causal amendment-lineage certification.
`run_data_gate` was strengthened during review to propagate the ready gate and
an evidence-hash-bound `evaluator_contract` from provider certification. The
current provider certifications do not yet contain the required pre-challenge
contracts. The current Form 4 tape also excludes all history for an issuer if
any amendment is observed anywhere in the capture; that uses future amendment
existence for earlier observations and therefore cannot receive the new
causal-lineage attestation.

This makes the adapter intentionally unreachable from current evidence. That
is safer than running an invalid backtest, but it means the requested frozen
evaluator has not yet been operationally completed.

Post-review integration now makes that condition explicit at source: newly
materialized Form 4 provider certifications are `BLOCKED`, set historical PIT
verification false, and carry evidence-hash-bound false attestations for the
pre-challenge extract, causal amendment lineage, and beneficial-owner
independence. The data gate propagates an evaluator contract only from a valid
provider-certification evidence hash. It cannot turn those fields true itself.

### Critical — the primary return experiment is deliberately unimplemented

The adapter constructs only candidate cluster and single-purchase events. It
does not implement the 60-session issuer cooldown, stateful maximum-ten-name
portfolio, next-open/terminal return chain, factor and sector adjustment,
matched baseline, expanding annual walk-forward, issuer/event-month inference,
Romano-Wolf correction, contribution limits, yearly sign gates, or cost and
capacity gates. These omissions are accurately listed in
`INCOMPLETE_OBLIGATIONS`, and the adapter returns `primary_metric_value: null`.

The null result is honest. It must not be described as an evaluated alpha
result or as completion of the frozen experiment.

### High — beneficial-owner independence is not established

Event construction treats distinct reporting-owner CIKs as independent. It
does not use a certified cross-filing common-control identity. The current
`control_group_id` is derived from accession/source identity and is not a
beneficial-owner control map. Two controlled vehicles with different owner
CIKs can therefore form a false cluster.

Before returns are joined, the input contract needs a causally available,
independently reviewed person/control identity or must fail closed on every
unresolved pair. A reporting-owner CIK alone does not satisfy the frozen
independence rule.

### Resolved after review — causal single-purchase comparator

Brett Olson approved Option B before any return access. The first eligible
filing creates and retains its original single-purchase event. A later filing
that first completes a qualifying cluster creates only the cluster event; it
does not create another single event, and the earlier single is never removed
using future information. Later purchases during the cluster cooldown enrich
attribution only. Candidate construction now applies the rule when the cluster
is formed and while the 10-calendar-day cluster window remains active. The full
60-session cooldown and stateful single portfolio remain unimplemented and are
still explicit evaluator blockers.

The post-review tie rule treats distinct filings with the exact same SEC
acceptance timestamp as one public-information batch. If that batch completes
a cluster, none of its filings becomes an artificial earlier single event.

### High — settlement readiness still depends on substantive human evidence review

The settlement auditor now requires a hashed explicit termination population,
official source documents, pinpoint locators, extracted terms, reviewer
attestations, source timing, a complete-scope attestation, positive unique last
prices, and exact cash or final-zero outcomes. These are strong fail-closed
controls.

The code can verify hashes and field consistency, but it cannot prove that an
`extracted_term` matches the cited source or that a named reviewer is genuinely
independent. Those are substantive review obligations, not machine facts. A
real `CERTIFIED_READY` packet therefore requires retained human review evidence
outside the preparer's self-assertion. Current repository evidence does not
provide it, so `NOT_CERTIFIED` is the only supportable status.

### High — exact return-chain semantics are not fully certified

The auditor trusts the manifest assertion that the last unadjusted close
excludes terminal proceeds. It does not independently reconcile the panel's
corporate-action/adjustment fields or prove that cash is expressed on the same
pre-action share basis as that last close. Splits, exchange ratios, mixed
consideration, contingent rights, and multiple distributions can invalidate
the simple `proceeds / last_close - 1` chain.

Cash-only and officially final zero-recovery cases may be certifiable after
this reconciliation. Stock, mixed, contingent, or unresolved cases correctly
remain blocked. A complete population containing any such case cannot yet
become ready.

### Medium — challenge lock relies on a new extract contract that does not exist yet

The evaluator checks file hashes and requires each file to be declared as a
pre-challenge extract with `maximum_observation_date < 2025-01-01`. This is a
sound direction, but the declarations must be generated and validated by an
adapter boundary rather than authored in an arbitrary input packet. The
settlement auditor bounds its price query at 2024-12-31, but hashing a combined
panel still reads the underlying file bytes. The strongest lock is a separately
materialized, immutable, manifest-bound pre-2025 extract for every input.

### Resolved after review — frozen variant budget

Brett Olson removed the 120-day hold from the formal variant family before any
return access. The five formal variants are now the primary plus CEO/CFO-
required, three-insider minimum, five-basis-point purchase-value floor, and
20-day hold. The 120-day result remains a descriptive diagnostic only and
cannot enter promotion, parameter selection, or multiple-testing correction.

## Material safety improvements made during review

- Removed the provisional return implementation that would have loaded the
  challenge period and produced a methodologically incomplete alpha estimate.
- Added file SHA-256 verification before evaluator inputs are opened.
- Added a hard requirement for separately certified pre-challenge extracts and
  maximum observation dates before 2025-01-01.
- Added a hard failure for the current noncausal issuer-wide amendment policy.
- Removed the unfrozen automatic exclusion of 10b5-1 purchases.
- Replaced officer-title heuristics with a required frozen role
  classification.
- Aggregated multiple transaction rows within an owner/accession event rather
  than emitting duplicate transactions as separate events.
- Implemented the approved Option B comparator without removing earlier single
  events using later cluster knowledge.
- Made every missing portfolio, inference, matching, cost, capacity, and
  challenge obligation explicit; no alpha claim is permitted.
- Strengthened terminal-settlement population, source, timing, uniqueness,
  completeness, zero-recovery, and double-count checks while retaining the
  honest `NOT_CERTIFIED` outcome.

## Required validation before a return run

1. Produce immutable, manifest-bound pre-2025 extracts for every required
   asset; verify hashes, schemas, maximum dates, and exact source lineage.
2. Replace issuer-wide future amendment exclusion with causal transaction-level
   supersession resolution, or exclude only information known as of each event.
3. Certify natural-person, role, and common-control identity without title or
   name heuristics; report unresolved coverage loss.
4. Carry the approved Option B comparator through the stateful single portfolio
   and implement only the five approved formal variants.
5. Complete and independently attest the explicit terminal-action population;
   reconcile cash/share units and price adjustments; retain exact source
   locators and reviewer evidence.
6. Test entry at the next full regular-session open, prior-session eligibility
   data, PIT membership/characteristics, holiday handling, 60-session cooldown,
   delisting inside the holding window, and no terminal double count.
7. Test a stateful maximum-ten-name portfolio, cash residual, deterministic
   ties, actual entry/exit turnover, costs on every trade, and 5%-ADV capacity
   at all three reference capitals.
8. Implement the same-rule single portfolio, matched event baseline, daily
   factor and sector controls, expanding 2019-2024 walk-forward, clustered
   inference, deterministic block bootstrap, Romano-Wolf/max-T, yearly sign
   tests, and contributor limits.
9. Demonstrate stable output across repeated runs and fail closed on altered
   bytes, duplicate rows/dates, missing factors, ambiguous identities,
   incomplete holdings, and incomplete terminal outcomes.
10. Repeat independent code/data review before any challenge authorization.

## Challenge lock and production boundary

This review did not open or query challenge-period return data. It inspected
the frozen specification, source code, documentation, and synthetic test
fixtures only. The evaluator rejects every phase other than `DISCOVERY`, and
the reviewed implementation submits no orders and changes no broker,
allocation, execution, scheduler, VM, Paper, pilot, live, deployment, or
capital path.

The 2025-01-01 through 2026-06-30 challenge period must remain locked. Neither
settlement certification nor completion of the discovery evaluator would by
itself authorize challenge access.

## Governance-delta revalidation

The approved Option B and five-variant clarification were re-reviewed after
implementation. The formal family is exactly the primary plus four
alternatives; 120-day CAR is diagnostic only. The amended hypothesis body
recomputes to its declared SHA-256
`c8426e20909f2a45e936b06339defae99b06989c3fbce16333456cf418d3f75b`.
Forty-three focused evaluator, settlement, control-plane, and four-lane tests
passed; Python compilation and `git diff --check` passed. No challenge data was
opened.
