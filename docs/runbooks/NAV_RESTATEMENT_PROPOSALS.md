# NAV Restatement Proposals

## Purpose

Historical NAV disagreements are evidence problems, not automatic execution
incidents. The proposal workflow records a later broker-derived history without
rewriting the immutable canonical NAV or silently changing reporting.

The workflow is non-executional. It cannot submit orders, change strategy lanes,
accept a restatement, or switch a consumer.

## Two-step procedure

1. Run a dry comparison and review the exact base/source hashes and dates:

   ```bash
   python3 scripts/build_nav_restatement_proposal.py --repo-root . --json
   ```

2. If the inputs and classification are correct, materialize the immutable
   proposal using the two hashes printed by the dry run:

   ```bash
   python3 scripts/build_nav_restatement_proposal.py \
     --repo-root . \
     --write-proposal \
     --expected-base-sha256 <reviewed-base-hash> \
     --expected-source-sha256 <reviewed-source-hash> \
     --json
   ```

The proposal is written under
`outputs/portfolio_history/restatement_proposals/<proposal_id>/` and contains:

- `dispositions.jsonl`: one proposed, unaccepted record per disagreement;
- `nav_as_restated.csv`: a review projection with equity/derived returns changed
  and all other canonical fields preserved;
- `manifest.json`: exact input/output hashes and the explicit statement that no
  consumer switch is authorized.

## Fail-closed conditions

The utility refuses to materialize when reviewed hashes are absent or changed,
dates are duplicated, rows are invalid, the canonical schema is incomplete, or
an existing immutable proposal fails its recorded hashes. It verifies that the
canonical base hash is unchanged after writing the proposal.

## Acceptance boundary

Materialization is not acceptance. The canonical `nav.csv` remains untouched.
An owner must separately review the economic reason for each disagreement and
authorize both acceptance and any reporting-consumer change. Until then the
current history remains canonical and the proposal remains evidence only.
