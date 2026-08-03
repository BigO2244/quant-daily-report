# Exact Terminal-Settlement Certification

Status: `RESEARCH_ONLY_NON_EXECUTIONAL`

## Purpose

`terminal_settlement_certification.py` is the fail-closed audit boundary for
historical terminal proceeds. It does not collect filings, infer payouts, edit
the price panel, or authorize a return evaluator. Its output can become
`CERTIFIED_READY` only when the complete in-scope terminated-common-equity
population has case-specific finality evidence.

The current Caerus data does **not** meet this contract. Sharadar SEP provides
delisted price histories and a final observed daily return, while the SEC 8-K
index provides discovery candidates. Neither proves terminal consideration.

## Exact scope

The scope is an explicit start and end date. A security-master `effective_end`
is not treated as an economic terminal event. The population comes from a
separately hashed file of explicit eligible termination actions, each tied to
official source evidence, and is joined to an exact, hashed PIT security master
to require `Domestic Common Stock`. An independent reviewer must attest that
the action population is complete for the scope and describe the comparison
method; the attestation explicitly confirms independence from the evidence
preparer. The evidence provider cannot supply an unexplained hand-selected
denominator. An empty population is never vacuously certified.

The HYP-2026-003 validation scope is 2012-01-01 through 2024-12-31. The price
input must be a separately materialized, immutable pre-challenge extract whose
certified maximum observation date is 2024-12-31. A combined file containing
2025+ observations is inadmissible even if a query could filter those rows.
Challenge period 2025-01-01 through 2026-06-30 remains locked and is not
accessed by this audit.

## Immutable evidence manifest

The auditor requires a JSON manifest with:

- schema `caerus_alpha_lab_terminal_settlement_evidence_v1`;
- classification `RESEARCH_ONLY_NON_EXECUTIONAL`;
- population rule `explicit_eligible_terminal_actions_v1`;
- exact scope dates;
- a certified pre-challenge price-extract contract with maximum observation
  date equal to the scope end;
- SHA-256 hashes of the PIT security master and price panel;
- a SHA-256-hashed JSONL settlement file;
- a SHA-256-hashed eligible-termination population with action identity, type,
  effective date, and source-document links;
- a SHA-256-hashed inventory of every supporting document;
- source URI, provider identity distinct from the price provider,
  timezone-aware publication time, official authority, pinpoint locator,
  extracted term, and a dated independent-reviewer attestation for every
  finality source, including independence from the source and price provider;
- this exact price basis:

```json
{
  "field": "close",
  "semantics": "UNADJUSTED_LAST_OBSERVED_TRADE",
  "terminal_proceeds_included": false,
  "terminal_return_application": "AFTER_LAST_OBSERVED_RETURN_ONLY"
}
```

Source paths must remain within the immutable evidence bundle. Official
finality authorities are an SEC filing, court order, exchange notice, transfer
agent record, or issuer final-distribution record. A nearby filing, vendor
action code, last trade, or sensitivity assumption is not finality evidence.

## Case contract

Every security requires exactly one record with:

- `security_id`;
- `outcome_type`;
- `finality_basis`;
- `currency: USD`;
- exact `terminal_proceeds_per_pre_action_share`;
- `settlement_effective_date`;
- timezone-aware `evidence_available_at`; and
- one or more verified `source_document_ids`.

The implemented certifiable outcomes are `FINAL_CASH` and
`FINAL_ZERO_RECOVERY`. Zero is accepted only with an official final order or
final-distribution source; absence of data is never converted to zero.

Stock consideration, mixed consideration, contingent-value rights,
post-bankruptcy distributions, or any other unresolved leg remain blocked
unless all legs have final resolution and an exact receipt-date valuation
contract is added and independently reviewed. They must not be flattened into
a guessed cash number. Multi-tranche cash distributions must be complete and
final before they are represented by the aggregate per-share proceeds.

The evidence availability time cannot precede any supporting source's
publication time or exceed the audit's `as_of` time. Settlement cannot precede
the explicit terminal action or exceed `as_of`. Price scanning is bounded by
the scope end; the last in-scope observation must be unique and have a positive
unadjusted close.

This intentionally leaves some corporate actions non-certifiable rather than
creating false precision.

For a certified cash case, the verifier computes:

```text
verified_terminal_return = terminal proceeds per pre-action share
                           / last unadjusted observed close - 1
```

That return applies once, after the last observed price return. The provider's
last-day return cannot be reused as settlement and the terminal return cannot
be added to the same observation. This prevents double counting.

## Deterministic command

The command is read-only and prints canonical JSON. Exit status is `0` only for
`CERTIFIED_READY`; non-certification exits `2`.

```bash
python -m projects.alpha_lab.data_spine.terminal_settlement_certification \
  --evidence-manifest /path/to/immutable/manifest.json \
  --security-master data/pit_universe/security_master.csv \
  --price-panel outputs/research/pit_liquidity/pit_liquidity_panel.parquet \
  --scope-start 2012-01-01 \
  --scope-end 2024-12-31 \
  --as-of 2026-08-03T18:00:00Z
```

Run this against finalized evidence on the authoritative GCP research root.
The Mac rollback tree must not receive new source data or generated research
bundles.

## Promotion boundary

A passing settlement audit is one data condition, not evidence of alpha and
not approval to access the challenge period. It cannot promote a strategy,
submit orders, change allocation, or alter broker, scheduler, paper, pilot,
live, deployment, or capital behavior.
