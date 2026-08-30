# Trading integrity certification

## Purpose

Caerus certifies the latest 20 expected XNYS sessions against six binary
controls. A session passes only when all six controls pass; missing or ambiguous
evidence fails closed.

1. Required data is fresh and the universe is PIT-valid and decision-grade.
2. The model computation actually reran, proven by current and prior causal
   lineage plus changed upstream market, feature, or rank hashes.
3. The decision is the output of that certified computation and the same
   lineage is preserved across the decision surfaces.
4. The precompute bundle is sealed and every declared artifact hash verifies.
5. The executor used the hash-bound approved package and exact-plan path; a
   downstream target rebuild or missing source hash fails.
6. Paper-lane and broker reconciliation match intended positions and cash with
   no unexplained difference or manual intervention.

Trading Integrity Rate is:

`certified sessions / expected sessions`

The target is 20/20 sessions and 120/120 control observations. This is not an
average-quality score: 119/120 still means the window is RED.

The certifier is a read-only assurance score, not mutation authority and not an
automatic capital-lane halt. A low historical rate cannot revoke an existing
Live or PAPER owner decision. Current-session transaction lineage, artifact,
execution, and reconciliation gates continue to fail closed at their existing
boundaries. A bounded universe-pedigree gap prevents full certification and
capital scaling, but does not by itself prove that a current order is unsafe.

## Run

```bash
python3 scripts/certify_trading_integrity.py \
  --repo-root . \
  --through-date YYYY-MM-DD \
  --sessions 20 \
  --output outputs/governance/trading_integrity/YYYY-MM-DD.json
```

The command is read-only unless `--output` is supplied. It exits zero only for
a fully GREEN window.

## Interpretation

- An unchanged target is not itself stale. It is certifiable when current
  market/features/ranks differ from the prior computation and the target is a
  deterministic output of the governed selection rule.
- `legacy_current_universe` and `NON_DECISION_GRADE_UNIVERSE` fail the Data/PIT
  control unless the original session envelope binds an exact governed
  prospective static-universe freeze. The freeze must predate the evaluation,
  match the configured artifact hash and exact universe bytes/order, and be
  effective no later than the session. This narrow exception certifies only
  prospective operating membership; it does not make a current-universe
  historical replay PIT-safe or eligible for a promotion claim.
- Do not translate a retrospective RED certification window into “PAPER is
  halted” or “Live is blocked.” Capital-lane authority comes from the operating
  lane registry and its scoped owner decisions. A halt requires an applicable
  current-session safety gate, reconciliation failure, or explicit owner action.
- A correct fail-closed no-trade day is operationally safe, but it is not a
  certified session when the upstream decision or artifact did not exist.
- Historical sessions are never retroactively upgraded without their original
  immutable evidence.

## Prospective static-universe freeze

Orion's prospective proof is configured in
`config/research/strategy_registry.json` and points to the immutable freeze in
`docs/evidence/`. The source remains `data/universe.csv`; the freeze does not
add, remove, reorder, or rename a member and does not alter weights, sleeve
decisions, allocation, execution, or capital authority. The existing
`legacy_current_universe` label is retained so this operational proof cannot be
misread as historical survivorship remediation.

Missing artifacts, a configured-file hash mismatch, changed universe bytes or
order, a member-count mismatch, missing evaluation time, or a session before
the freeze cutoff all fail the Data/PIT control closed.

Rollback is one scoped git revert: remove the two Orion freeze-reference fields,
the prospective evidence JSON, and the supporting validation code/tests. That
restores the prior unproved Data/PIT rating without changing universe bytes,
target weights, decision hashes, lane authority, or runtime artifacts. Never
delete already generated certification evidence during rollback.

## CIO brief contract

The daily brief should consume only this certification artifact for Operations:

```text
OPERATIONS — GREEN|YELLOW|RED
Trading integrity: <certified>/<expected>
Yesterday: PASS|FAIL (<passed controls>/6)
Exceptions: <control names and exact reason codes>
```

Capital, Alpha, and at most three CIO Attention items remain separate. A
dashboard or narrative may summarize this artifact but may not reinterpret a
failed binary control as green.
